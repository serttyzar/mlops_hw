"""Six-minute captioned playback of saved, verifiable evidence; not a desktop recording."""
import csv
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parent.parent/'hw8910-demo.mp4'


def read(name):
    return json.loads((ROOT/'metrics'/name).read_text())


def main():
    from PIL import Image,ImageDraw,ImageFont
    import imageio_ffmpeg
    font_path='/System/Library/Fonts/Menlo.ttc'
    if not Path(font_path).exists():
        font_path='/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
    title_font=ImageFont.truetype(font_path,36)
    body_font=ImageFont.truetype(font_path,25)
    small_font=ImageFont.truetype(font_path,20)
    baseline,candidate=read('baseline-eval.json'),read('candidate-eval.json')
    smoke,restart=read('api-smoke.json'),read('api-smoke-restart.json')
    summary=read('load-summary.json')
    rejected=read('real-publication.json')[-1]
    flows=read('flow-experiments.json')
    result=read('release-report-candidate.json')
    links=read('run-links.json')
    def filtered(log,markers):
        return '\n'.join(line for line in (ROOT/'docs/logs'/log).read_text().splitlines()
                         if any(m in line for m in markers))
    def pretty(obj):
        return json.dumps(obj,ensure_ascii=False,indent=2)
    cards=[
      ('ДЗ 8+9+10: проверяемый выпуск',
       'Демонстрация сохранённых результатов реальных запусков.\n'
       'Это воспроизведение логов с подписями, без озвучки и записи рабочего стола.\n\n'
       'Проект продолжает ДЗ 1–7; история Git сохранена.\n'
       'Исходный прототип: commit 732ddae.\n'
       '7 причин ошибок → регрессии → исправления → Prefect и FastAPI.\n'
       'Синтетические STUB-опыты и реальные ответы модели разделены.\n\n'
       'Training run: '+links['training_run_id']+'\n'
       'Новый evaluation run: '+links['evaluation_run_id']),
      ('Сначала красные проверки исходного кода',
       '$ python scripts/reproduce_before.py\n\n'+
       '\n'.join('FAIL: '+name for name in dict.fromkeys(re.findall(r'FAIL: (test_[a-z_]+)', (ROOT/'docs/logs/integration-before.log').read_text())))+
       '\n\nВсе семь тестов выявили поведение стартера. Импорты, CLI и HTTP работали.\n'
       'Старые tests/ ДЗ 6+7 и 13 контрактных тестов не редактировались.'),
      ('Итоговый локальный CI',
       '$ make check\n\n'+filtered('check-after.log',['Ran ','\nOK'])+
       '\n\n13 контрактов + 12 filesystem/process + 7 FastAPI + 19 eval = 51.\n'
       'Все четыре группы: OK. Веса и MLflow для make check не нужны.\n\n'
       '$ make check-negative\nНамеренная ошибка cache_key внесена во временную копию.\n'
       'Python unittest exit 1; make exit 2. Рабочий код сохранён.\n'
       'Лог: docs/logs/check-intentional-failure.log'),
      ('Восстановление подготовки и инвалидация',
       'Ключ: SHA256 канонического JSON {data_sha256, config}.\n'
       'Config: tokenizer revision, template hash, max_length, code revision.\n'
       'Подготовка проверяет реальные UTF-8/JSONL и уникальные id.\n\n'
       '$ python scripts/check_flow.py\n\n'+
       filtered('flow-failure.log',['PREPARATION_STATE'])+'\n'+
       filtered('flow-recovery.log',['PREPARATION_STATE'])+'\n'+
       filtered('flow-invalidation.log',['PREPARATION_STATE'])+
       '\n\nНовый процесс читает сохранённый результат. Новые байты меняют ключ.'),
      ('Полная реальная оценка; кандидат отклонён',
       'Тот же TransformersBackend, что используется API. Нового обучения нет.\n'
       '50 golden, 3 behavioral; unchanged HW6+7 quality_gate.\n\n'
       '                 accuracy     format rate\n'
       f"baseline         {baseline['overall']['accuracy']:.0%}          {baseline['overall']['format_rate']:.0%}\n"
       f"candidate        {candidate['overall']['accuracy']:.0%}          {candidate['overall']['format_rate']:.0%}\n\n"
       f"passed = {result['passed']}; reason = {result['reason']}\n"
       'Raw-ответы пересчитаны; учтены срезы, ошибки и behavioral.\n'
       'Отчёт связан с файлами модели, runtime, протоколом и eval run.'),
      ('Отказ сохраняет принятую baseline',
       '$ make baseline  # exit 0\n$ make candidate # exit 1, ожидаемый отказ\n\n'
       'SHA256 accepted.json до попытки кандидата:\n'+rejected['before_sha256']+'\n\n'
       'SHA256 после отказа:\n'+rejected['after_sha256']+'\n\n'
       'Указатель побайтно неизменен. MLflow champion не менялся.\n'
       'Missing/foreign report, NaN/Inf, false/string, ошибка замены отклоняются.\n'
       'Запись: temp рядом с target → flush/fsync → os.replace.'),
      ('Сбой между указателем и журналом',
       'Перед заменой accepted.json сохраняется pending-publication.json.\n'
       'После успешной замены — атомарное обновление JSONL-журнала.\n'
       'Один писатель; release_id = SHA256 {manifest, report}.\n\n'+
       filtered('flow-journal-recovery.log',['RECOVERED','PREPARATION_STATE'])+
       '\n\nСобытий выпуска после сбоя, восстановления и повтора: '+str(len(flows['recovered_events']))+
       '\nСобытия выпуска не дублируются; attempts.jsonl хранит все попытки.\n'
       'Дополнительный тест проверяет отказ записи самого журнала.'),
      ('Настоящий FastAPI и прогрев',
       '$ make serve\nGET /health → 200; GET /ready → 503 до прогрева, затем 200.\n\n'
       'Реальный smoke HTTP '+str(smoke['http_status'])+':\n'+pretty(smoke['response'])+
       '\n\nПосле отдельного перезапуска model_id/runtime_sha256 снова совпали.\n'
       'Логи: real-api-server.log, real-api-restart.log.'),
      ('Фактически загруженная версия: STUB A → B → A',
       'Проверено настоящими HTTP-серверами и subprocess выпуска.\n\n'
       '1. Старт на A: ответ содержит STUB ONLY: fixture-A.\n'
       '2. Другой процесс публикует B; работающий API по-прежнему отвечает A.\n'
       '3. После управляемого перезапуска ответ и model_id равны B.\n'
       '4. Публикация ранее принятой A и перезапуск возвращают A.\n\n'
       'test_http_loaded_version_restart_and_rollback: OK\n'
       'Тот же HTTP-тест проходит на stdlib и FastAPI.\n'
       'Это инженерная проверка управления версиями, не оценка Qwen.'),
      ('Неверные запросы не запускают backend',
       'Реальный API; /debug включён только для локального опыта.\n\n'
       'Пустой prompt, max_new_tokens=0/100000/строка, превышение 2048 токенов.\n'
       'Все пять ответов: HTTP 400.\n\n'
       f"backend.calls до: {smoke['calls_before_invalid']}\nbackend.calls после: {smoke['calls_after_invalid']}\n\n"
       'Единица — настоящий warmup, выполненный до опытов.\n'
       'Для модели доступен точный оценённый лимит 32 новых токена.\n'
       'Фактический ответ может завершиться раньше по EOS.'),
      ('Замер 8+8 одинаковых реальных запросов',
       'После прогрева; Mac15,7, MPS, bfloat16; 38 654 705 664 байта RAM.\n'
       'concurrency    n  OK  errors   req/s   mean latency   mean queue\n'+
       '\n'.join(f"{r['concurrency']:>11}    {r['n']}   {r['successes']}     {r['errors']}     {r['successful_req_per_second']:.2f}       {r['mean_client_seconds']:.3f}s        {r['mean_queue_seconds']:.3f}s" for r in summary)+
       '\n\nBackend считает последовательно. Параллельность 2 добавляет ожидание.\n'
       'Пропускная способность почти не меняется, средняя задержка растёт.\n'
       'Каждая попытка сохранена: metrics/load-attempts.jsonl.\n'
       'Полная таблица: metrics/load-summary.csv; ошибки не отбрасываются.'),
      ('Ограничения и воспроизведение',
       '8 запросов на режим недостаточно для устойчивого p95.\n'
       'JSON-ответ целиком: измерена полная задержка, TTFT/ITL не измерялись.\n'
       'Это последовательный Transformers, не бенчмарк vLLM/SGLang.\n'
       'Prefill обрабатывает prompt; decode добавляет токены с KV-cache.\n'
       'KV-cache растёт с длиной контекста и числом одновременно активных запросов.\n\n'
       '$ python scripts/restore_previous.py\n$ make check\n$ python scripts/verify_evidence.py\n\n'
       'Сдача: исходный/исправленный commits, docs/conclusion.md, manifests,\n'
       'полные raw/eval, defects.csv, логи, архив с Git bundle.\n'
       'Веса, окружения, caches и secrets исключены. CI выполнен локально.')]
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix='hw8910-video-') as directory:
        directory=Path(directory)
        paths=[]
        for i,(title,body) in enumerate(cards):
            im=Image.new('RGB',(1600,1000),'#11151d')
            draw=ImageDraw.Draw(im)
            draw.rectangle((0,0,1600,110),fill='#1d2634')
            draw.text((44,32),title,font=title_font,fill='#f4d35e')
            lines=[]
            for line in body.splitlines():
                lines.extend(textwrap.wrap(line,width=96,break_long_words=True,replace_whitespace=False) or [''])
            for n,line in enumerate(lines):
                if n>=25:
                    raise ValueError(f'Chapter {i+1} overflows: {len(lines)} lines')
                draw.text((44,140+n*30),line,font=body_font,fill='#e4e9f1')
            draw.line((44,930,1556,930),fill='#445166',width=2)
            footer=f'{i+1:02d}/12 · {i*30//60:02d}:{i*30%60:02d}–{(i+1)*30//60:02d}:{(i+1)*30%60:02d} · Сохранённые фактические результаты · без озвучки'
            draw.text((44,950),footer,font=small_font,fill='#a9b7cb')
            path=directory/f'{i:02d}.png';im.save(path);paths.append(path)
            if i==7:
                im.save(OUT.with_suffix('.png'))
        # One keyframe per second: terminal evidence is static; timebase remains true 360s.
        playlist=directory/'frames.txt'
        playlist.write_text(''.join(f"file '{p}'\nduration 30\n" for p in paths)+f"file '{paths[-1]}'\n")
        subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(playlist),
                        '-t','360','-vf','fps=1','-c:v','libx264','-preset','veryfast','-crf','19','-pix_fmt','yuv420p',
                        '-movflags','+faststart',str(OUT)],check=True)
    print(OUT)
    print('360 seconds; 12 captioned chapters; playback of actual saved evidence, not a screen capture')


if __name__=='__main__':
    main()
