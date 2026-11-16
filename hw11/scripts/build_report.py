"""Build a standalone HTML report; missing evidence stays explicitly missing."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from analyze import analyze, quantile
from verify_evidence import verify
from evidence_io import read_jsonl, jsonl_exists


def plots(directory,rows,events):
    if not rows:
        return []
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'svg.fonttype':'none','font.size':10})
    target=directory/'graphs';target.mkdir(exist_ok=True)
    produced=[]
    def save(fig,name):
        fig.tight_layout()
        path=target/name
        fig.savefig(path,format='svg',metadata={'Date':None})
        fig.savefig(path.with_suffix('.png'),dpi=110)
        plt.close(fig)
        produced.append(path)
    snapshots=[e for e in events if e['event']=='snapshot']
    def series(prefix):
        values=[]
        begin=None
        end=None
        if prefix=='C-HPA':
            begin=next((e['wall'] for e in events if e['event']=='load_start' and e.get('name')=='C-scale-up'),None)
            end=next((e['wall']+5 for e in events if e['event']=='hpa_back_to_2'),None)
        for e in snapshots:
            if prefix=='C-HPA' and begin is not None:
                if e['wall']<begin or (end is not None and e['wall']>end):continue
            elif not e.get('phase','').startswith(prefix):continue
            pods=[p for p in e['data']['items'] if p.get('kind')=='Pod' and
                  p['metadata'].get('labels',{}).get('app')=='hw11-api']
            if prefix=='C-HPA':
                pods=[p for p in pods if not p['metadata'].get('deletionTimestamp')]
            ready=sum(any(c['type']=='Ready' and c['status']=='True' for c in p.get('status',{}).get('conditions',[]))
                      for p in pods)
            endpoints=sum(sum(x.get('conditions',{}).get('ready') is True for x in p.get('endpoints',[]))
                          for p in e['data']['items'] if p.get('kind')=='EndpointSlice' and
                          p['metadata'].get('labels',{}).get('kubernetes.io/service-name')=='hw11-plain')
            values.append((e['wall'],ready,endpoints,len(pods)))
        return values
    a=series('A')
    if a:
        fig,axes=plt.subplots(2,1,figsize=(12,7),sharex=True)
        t0=a[0][0]
        axes[0].step([v[0]-t0 for v in a],[v[1] for v in a],label='Ready',where='post')
        axes[0].step([v[0]-t0 for v in a],[v[2] for v in a],label='Ready endpoints',where='post',alpha=.7)
        axes[0].set_ylabel('replicas / endpoints');axes[0].legend()
        for outcome,color in [('completed','#228b70'),('rejected','#cf6a25'),('aborted','#d33145'),('transport_error','#6b55a5'),('timed_out','#8e1111')]:
            group=[r for r in rows if r.get('run','').startswith('A') and r.get('end_wall') and r['outcome']==outcome]
            axes[1].scatter([r['end_wall']-t0 for r in group],[r.get('total_seconds',0) for r in group],s=12,label=outcome,color=color)
        for e in events:
            if e['event'] in ['rollout_begin','force_exit']:
                axes[0].axvline(e['wall']-t0,ls='--',alpha=.5)
                axes[0].text(e['wall']-t0,0,e['event'],rotation=90,va='bottom')
        axes[0].set_title('A · warmup, rollout, rollback and forced process exit')
        axes[1].set_ylabel('request duration, s');axes[1].set_xlabel('time since A began, s');axes[1].legend(ncol=3)
        save(fig,'01-warmup-rollout.svg')
    b=[r for r in rows if r.get('run')=='B-skew-affinity-1' and r.get('sent_wall')]
    if b:
        t0=min(r['sent_wall'] for r in b)
        fig,axes=plt.subplots(2,1,figsize=(12,7),sharex=True)
        for pod in sorted({r.get('pod') for r in b if r.get('pod')}):
            group=[r for r in b if r.get('pod')==pod and r.get('ttfc_seconds') is not None]
            axes[0].scatter([r['sent_wall']-t0 for r in group],[r['ttfc_seconds'] for r in group],s=9,label=pod)
            axes[1].scatter([r['sent_wall']-t0 for r in group],[r['queue_seconds'] for r in group],s=9,label=pod)
        axes[0].set_title('B · ClientIP, 80% through one client, repeat 1, 5 req/s, cold cache')
        axes[0].set_ylabel('TTFC, s');axes[1].set_ylabel('queue wait, s');axes[1].set_xlabel('time since first send, s')
        axes[0].legend(fontsize=8)
        save(fig,'02-pod-delay-queue.svg')
    b=[r for r in rows if r.get('run','').startswith('B-')]
    if b:
        fig,axes=plt.subplots(1,2,figsize=(12,5))
        labels=[]
        for x,(traffic,policy) in enumerate([(t,p) for t in ['uniform','skew'] for p in ['plain','affinity']]):
            labels.append(traffic+'\n'+policy)
            for repeat in [1,2,3]:
                group=[r for r in b if r.get('run')==f'B-{traffic}-{policy}-{repeat}']
                p95=quantile([r['ttfc_seconds'] for r in group if r['outcome']=='completed'],.95)
                if p95 is not None:
                    axes[0].scatter(x+(repeat-2)*.12,p95,color=['#2878b5','#ef8a22','#36a267'][repeat-1],label=f'repeat {repeat}' if x==0 else None)
                if group:
                    axes[1].scatter(x+(repeat-2)*.12,100*sum(r['outcome']!='completed' for r in group)/len(group),color=['#2878b5','#ef8a22','#36a267'][repeat-1])
        for ax in axes:
            ax.set_xticks(range(4),labels)
        axes[0].set_ylabel('successful TTFC p95, s');axes[1].set_ylabel('non-completed requests, %');axes[0].legend()
        axes[0].set_title('B · three repeats per policy and traffic, 120 s each')
        save(fig,'03-policy-comparison.svg')
    c=series('C-HPA')
    if c:
        t0=c[0][0]
        fig,axes=plt.subplots(2,1,figsize=(12,7),sharex=True)
        axes[0].step([v[0]-t0 for v in c],[v[3] for v in c],where='post',label='Active Pods')
        axes[0].step([v[0]-t0 for v in c],[v[1] for v in c],where='post',label='Ready')
        axes[0].set_ylabel('replicas');axes[0].legend();axes[0].set_title('C · CPU HPA, target 60% of 250m, min 2 / max 3')
        group=[r for r in rows if r.get('run')=='C-scale-up' and r.get('sent_wall')]
        bins=defaultdict(int)
        for row in group: bins[int((row['sent_wall']-t0)//5)*5]+=1
        axes[1].bar(list(bins),[v/5 for v in bins.values()],width=5,align='edge')
        axes[1].set_ylabel('actually sent, requests/s');axes[1].set_xlabel('time since C began, s')
        save(fig,'04-hpa-load.svg')
    return produced


def markdown(text):
    # Small safe renderer for local prose; code and raw HTML remain escaped.
    out=[]
    for block in text.split('\n\n'):
        level=len(block)-len(block.lstrip('#'))
        if level and block.startswith('#'*level+' '):
            out.append(f'<h{min(level+1,6)}>'+html.escape(block[level+1:])+f'</h{min(level+1,6)}>')
        else:
            escaped=html.escape(block).replace('\n',' ')
            escaped=re.sub(r'\[([^]]+)\]\((https://[^)]+)\)',r'<a href="\2">\1</a>',escaped)
            out.append('<p>'+escaped+'</p>')
    return '\n'.join(out)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--results',type=Path,default=ROOT/'results/local')
    p.add_argument('--output',type=Path,default=ROOT/'report.html')
    args=p.parse_args();directory=args.results.resolve();directory.mkdir(parents=True,exist_ok=True)
    result=analyze(directory)
    path=directory/'requests.jsonl'
    rows=[json.loads(x) for x in path.read_text().splitlines() if x] if path.exists() else []
    events_path=directory/'events.jsonl'
    events=list(read_jsonl(events_path)) if jsonl_exists(events_path) else []
    figures=plots(directory,rows,events)
    verification=verify(directory)
    complete=verification['complete'] and len(figures)==4
    badge='ПОЛНЫЙ ПРОГОН ПОДТВЕРЖДЁН' if complete else 'НЕПОЛНАЯ РАБОТА — НЕТ ВСЕХ KUBERNETES-ДОКАЗАТЕЛЬСТВ'
    env_path=directory/'environment-before.json'
    if not env_path.exists():
        env_path=ROOT/'results/local/environment.json'
    env=json.loads(env_path.read_text()) if env_path.exists() else {}
    tests=directory/'local-tests.log'
    if not tests.exists():
        tests=ROOT/'results/local/tests.log'
    test_text=tests.read_text() if tests.exists() else 'Локальные тесты не запускались.'
    match=re.search(r'Ran (\d+) tests.*?\n\n(OK|FAILED[^\n]*)',test_text,re.S)
    local_test=match.group(0) if match else 'См. tests.log'
    parts=['<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
           '<title>ДЗ 11 · Kubernetes под нагрузкой</title>',
           '<style>body{font:17px/1.6 system-ui,sans-serif;color:#182b36;background:#f2f5f7;max-width:1100px;margin:auto;padding:36px}main{background:white;padding:36px;border-radius:16px}h1,h2,h3{line-height:1.2}h2{margin-top:2em}a{color:#076980}.status{padding:18px;background:#fff0d4;border-left:5px solid #c98010}pre{overflow:auto;background:#f2f5f7;padding:15px;font-size:13px;white-space:pre-wrap}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}svg{max-width:100%;height:auto}li{margin-bottom:6px}.muted{color:#526571}</style><main>',
           '<p class="muted">MLOPS-26 · ДЗ 11 · backend_mode=simulated</p><h1>Сервис под нагрузкой</h1>',
           f'<p class="status"><strong>{badge}</strong></p>',
           '<p>Продолжение hw8910: переиспользуется StubBackend и контракт generate(prompt, limit), сохраняется POST /generate с prompt/max_new_tokens. Добавлены messages, streaming, CPU-work, очередь, LRU, версия/хеш, probes и метрики. Старые решения не переписаны.</p>']
    if not complete:
        parts += ['<h2>Что фактически проверено</h2><p>Обязательные доказательства полного прогона пока собраны не все. Фактически выполненные этапы определяются requests.jsonl, events.jsonl и checks.jsonl; причины остановки доступны в launcher.log, если запуск выполнялся через Terminal. Локальные ASGI-тесты не подтверждают работу Kubernetes. Эта версия не объявляется готовой сдачей на 10 баллов.</p>',
                  '<pre>'+html.escape(local_test)+'</pre>',
                  '<p>Дефект прогрева воспроизведён локально: broken startup/readiness дают 200, но backend возвращает 503. После изменения probes дают 503 до завершения прогрева. Доказательство исключения из EndpointSlice требует запуска в Kubernetes.</p>',
                  '<details><summary>Диагностика окружения до запуска</summary><pre>'+html.escape(json.dumps(env,indent=2,ensure_ascii=False))+'</pre></details>',
                  '<details><summary>Недостающие доказательства</summary><ul>'+''.join('<li>'+html.escape(m)+'</li>' for m in verification['missing'])+'</ul></details>']
    parts += ['<h2>Результаты запросов</h2>']
    if result['available']:
        parts += ['<table><tr><th>Прогон</th><th>План</th><th>Успехи</th><th>p50 TTFC, с</th><th>p95 TTFC, с</th><th>Ошибки</th></tr>']
        def fmt(value): return '—' if value is None else f'{value:.3f}'
        for name,r in result['runs'].items():
            parts.append(f'<tr><td>{html.escape(name)}</td><td>{r["planned"]}</td><td>{r["completed"]}</td><td>{fmt(r["ttfc_seconds_p50"])}</td><td>{fmt(r["ttfc_seconds_p95"])}</td><td>{fmt(r["error_fraction"])}</td></tr>')
        parts.append('</table><p>Полные разрезы по Pod, число отказов/обрывов/тайм-аутов и сопоставление buckets — в summary.json рядом с сырыми результатами.</p>')
    else:
        parts.append('<p>Сырых Kubernetes-измерений нет. Численные задержки и распределение по Pod не подставлялись.</p>')
    if (ROOT/'docs/results.md').exists():
        parts.append(markdown((ROOT/'docs/results.md').read_text()))
    parts.append('<h2>Четыре обязательных графика</h2>')
    if figures:
        for figure in figures:
            svg=figure.read_text();parts.append(svg[svg.index('<svg'):])
    else:
        parts.append('<p>Не построены: прогрев/rollout и Ready; TTFC/очередь по Pod; сравнение политик по трём повторам; HPA 2→3→2 и нагрузка. Скрипт построит их после настоящего прогона.</p>')
    parts.append(markdown((ROOT/'docs/experiments.md').read_text()))
    parts.append(markdown((ROOT/'docs/review.md').read_text()))
    if (ROOT/'docs/submission.md').exists():
        parts.append(markdown((ROOT/'docs/submission.md').read_text()))
    parts.append('<h2>Воспроизведение и ограничения</h2><p>Команды запуска, удаления, лимиты и состав результатов — в README.md. Измерения относятся к CPU-имитатору; веса Qwen не загружаются. Настройки SGLang/llm-d и реальные токены отдельно не измерялись. Срок хранения Prometheus 2 часа, до 256 MB; графики и JSONL сохраняются на хост до удаления стенда.</p>')
    parts.append('<p class="muted">Сформировано '+datetime.now(timezone.utc).isoformat()+' · Источник результатов: '+html.escape(str(directory.relative_to(ROOT)) if directory.is_relative_to(ROOT) else str(directory))+'</p></main></html>')
    args.output.write_text('\n'.join(parts))
    print(json.dumps({'output':str(args.output),'complete':complete,'graphs':len(figures)},ensure_ascii=False))


if __name__=='__main__':
    main()
