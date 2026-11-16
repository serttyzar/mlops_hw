"""Fail closed when the mandatory experiments have not actually been run."""
import argparse
import json
from pathlib import Path
from analyze import analyze
from evidence_io import read_jsonl, jsonl_exists


def verify(directory):
    missing=[]
    def require(condition, message):
        if not condition:
            missing.append(message)
    requests=directory/'requests.jsonl'
    require(requests.exists(),'Нет сырых Kubernetes-запросов requests.jsonl')
    rows=[json.loads(x) for x in requests.read_text().splitlines() if x] if requests.exists() else []
    checks_path=directory/'checks.jsonl'
    checks=[json.loads(x) for x in checks_path.read_text().splitlines() if x] if checks_path.exists() else []
    names={c['name'] for c in checks if c.get('passed') is True}
    for name in ['four_distinct_client_ips','warmup_defect_reproduced','warmup_fixed_no_unready_requests',
                 'graceful_rollout_zero_5xx_and_incomplete','version_B_actually_loaded','bad_release_created',
                 'bad_release_excluded_from_answers','loaded_A_after_rollback',
                 'forced_exit_recorded_incomplete_stream','service_recovered_after_forced_exit',
                 'replacement_restores_version_and_dialogue','two_dialogues_isolated','hpa_2_to_3_to_2',
                 'HighTTFC-pending-firing','HighFailureRatio-pending-firing','alerts_returned_to_normal']:
        require(name in names,'Не подтверждена проверка: '+name)
    require(not any(c.get('passed') is False for c in checks),'Есть проваленные Kubernetes-проверки')
    summary=analyze(directory)
    for traffic in ['uniform','skew']:
        for repeat in [1,2,3]:
            paired=[]
            for policy in ['plain','affinity']:
                name=f'B-{traffic}-{policy}-{repeat}'
                samples=[r for r in rows if r.get('run')==name]
                require(len(samples)==600,name+': ожидается 600 запланированных запросов')
                require({r.get('client') for r in samples}=={0,1,2,3},name+': нужны четыре клиента')
                paths=[directory/f'{name}-client{i}.schedule.json' for i in range(4)]
                schedules=[json.loads(p.read_text()) for p in paths if p.exists()]
                require(len(schedules)==4,name+': нет всех четырёх расписаний')
                require(all(s.get('duration',0)>=120 and s.get('rate')==5 for s in schedules),name+': фаза <120 с либо изменён rate')
                hashes={s['sha256'] for s in schedules}
                require(len(hashes)==1,name+': расписания клиентов различаются')
                paired.append(hashes)
                compare=summary['histogram_comparison'].get(name,{})
                require(compare.get('available') and compare.get('same_success_count') and compare.get('client_n',0)>=30,name+': нет сопоставления с Prometheus для того же набора успехов')
                require(all(r.get('backend_mode')=='simulated' for r in samples if r.get('status')==200),name+': backend mode не подтверждён')
            require(paired[0]==paired[1] and bool(paired[0]),f'B-{traffic}-{repeat}: политики сравнивались на разных расписаниях')
    events=directory/'events.jsonl'
    data=list(read_jsonl(events)) if jsonl_exists(events) else []
    for event in ['snapshot','prometheus','hpa_3_pods_created','hpa_3_pods_ready','hpa_back_to_2','experiments_complete']:
        require(any(e['event']==event for e in data),'Нет события '+event)
    require(bool(list(directory.glob('pod-*.log'))),'Нет серверных журналов Pod')
    for filename in ['environment-before.json','nodes.json','services.yaml','kube-proxy.yaml','image.json','size-after.txt']:
        require((directory/filename).exists(),'Нет '+filename)
    for figure in ['01-warmup-rollout.svg','02-pod-delay-queue.svg','03-policy-comparison.svg','04-hpa-load.svg']:
        require((directory/'graphs'/figure).exists(),'Нет графика '+figure)
    result={'complete':not missing,'missing':missing,'requests':len(rows),'checks_passed':len(names)}
    (directory/'verification.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);args=p.parse_args()
    result=verify(args.directory)
    print(json.dumps(result,indent=2,ensure_ascii=False))
    raise SystemExit(0 if result['complete'] else 1)
