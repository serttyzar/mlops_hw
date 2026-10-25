"""Render the combined report from measured MLflow runs and saved raw answers."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mlflow import MlflowClient
from src.common import connect, load_params


def read(name):
    return json.loads(Path(name).read_text())


def inline(text):
    return ' '.join(text.split()).replace('|', '\\|')


PAIR_ANALYSIS = {
    'gold-B01M59I7JA': 'В отзыве про аквариумный замок нет высоты. Число в названии товара не является свидетельством из отзыва: протокол ограничен excerpt. База вернула NOT_STATED в JSON, кандидат — ту же метку голым текстом. Это новая ошибка формата, а не новая ошибка понимания; по заранее заданному контракту она даёт ноль.',
    'gold-B074XCXJQ1': 'Отзыв обсуждает непрочность и удобство сумок, но не объём 90 литров. Оба ответа содержат верную по смыслу метку NOT_STATED. Кандидат нарушил JSON-контракт, поэтому valid=false и correct=false. Именно две такие ошибки объясняют падение общей accuracy на 4 п.п.',
    'gold-B0776YYS1B': 'Фраза one of the best spark прямо выражает положительную оценку искры. Обе модели сохранили смысл и строгий формат: SUPPORTED. Это пример устойчивого правильного ответа, а не улучшения от LoRA.',
    'gold-B07MZNNWN1': 'Автор отдельно называет Chicken and Apple твёрдыми, как бетон; soft and chewy относится к другим вкусам. Утверждение переносит мягкость на неправильный вкус и противоречит отзыву. Обе модели ошибочно выбрали SUPPORTED: общий провал различения объектов и противопоставления, который снижение loss не исправило.',
    'gold-B002JPRKYI': 'После 10 часов осталась четверть бака: следовательно, некоторое топливо оставалось. Обе модели дали SUPPORTED в JSON. Не нужно домысливать характеристики нагревателя; достаточно явно названного остатка.',
}


def main():
    params = load_params()
    connect(params)
    client = MlflowClient()
    summary = read('metrics/sweep.json')
    spec = read('metrics/candidate-model.json')
    a, b = (read(f'metrics/{role}-eval.json') for role in ('baseline', 'candidate'))
    diff = read('metrics/eval-diff.json')
    gate = read('metrics/quality-gate.json')
    negative = read('metrics/negative-gate.json')
    storage = read('metrics/storage-check.json')
    runs = [client.get_run(row['run_id']) for row in summary['rows']]
    best = client.get_run(spec['run_id'])
    links = {r.info.run_id: f'http://127.0.0.1:5006/#/experiments/{r.info.experiment_id}/runs/{r.info.run_id}' for r in runs}
    ev = client.get_run(b['evaluation_run_id'])
    eval_url = f'http://127.0.0.1:5006/#/experiments/{ev.info.experiment_id}/runs/{ev.info.run_id}'
    base = read('evaluation/baseline.json')
    fig, ax = plt.subplots(figsize=(9, 4.8))
    for r in runs:
        hist = sorted(client.get_metric_history(r.info.run_id, 'val_loss'), key=lambda m: m.step)
        ax.plot([m.step for m in hist], [m.value for m in hist], marker='o', label=r.data.tags['candidate'])
    ax.set(xlabel='Optimizer step', ylabel='Validation loss', title='AmaSum: same 8 validation examples, 6 LoRA runs', xticks=[0,2,4])
    ax.legend(fontsize=8);ax.grid(alpha=.2);fig.tight_layout();fig.savefig('docs/sweep-curves.png',dpi=160);plt.close(fig)
    lines = ['# ДЗ 6+7: от короткого поиска к проверке качества AmaSum','',
        f'**Решение: {"принять" if gate["passed"] else "не принимать"} кандидата в champion.**',
        f'Гейт: `{json.dumps(gate, ensure_ascii=False)}`.','',
        '## 1. Связь с предыдущими ДЗ и выбор по loss','',
        'Продолжение общего репозитория ДЗ 1–5. ДЗ 3: AmaSum → train/val/test по topic; '
        'ДЗ 4: токенизация Qwen3-0.6B и маска промпта; ДЗ 5: LoRA для резюме отзывов. '
        'ДЗ 6+7 добавляет учёт экспериментов, реестр и проверяемое правило выпуска.', '',
        f'Обучающий коммит: `{best.data.tags["git_sha"]}`, dirty={best.data.tags["git_dirty"]}. '
        f'Базовая модель: `{base["name"]}@{base["revision"]}`.',
        'Полные файлы содержат 3185 train / 398 val; общий короткий бюджет — train-пул 32, '
        'val 8, seed 42, batch 1 × accumulation 2, четыре шага на каждую конфигурацию. '
        'Всего 24 шага оптимизатора. Golden/test не участвовал в выборе run.', '',
        '| Конфигурация | r | lr | val до | val после | final/base | секунды | пик памяти MiB | адаптер MiB |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in sorted(runs, key=lambda r:r.data.metrics['final_val_loss']):
        m = r.data.metrics
        lines.append(f'| [{r.data.tags["candidate"]}]({links[r.info.run_id]}) | {r.data.params["r"]} | '
            f'{r.data.params["lr"]} | {m["base_val_loss"]:.6f} | {m["final_val_loss"]:.6f} | '
            f'{m["final_val_loss"]/m["base_val_loss"]:.6f} | {m["seconds"]:.1f} | {m["peak_memory_mb"]:.1f} | {m["adapter_mb"]:.2f} |')
    lines += ['', f'Выбран `{best.data.tags["candidate"]}`, run `{spec["run_id"]}`; версия `{spec["uri"]}`, '
        f'alias `models:/{spec["name"]}@candidate`. Loss-порог 1.0 пройден до регистрации.',
        '![Кривые validation loss](sweep-curves.png)', '',
        'Четыре шага и восемь val-примеров проверяют инфраструктуру и локальный эффект обучения; '
        'это не устойчивый рейтинг гиперпараметров. Память — максимум замеров MPS driver_allocated_memory (включая кэш драйвера), '
        'а не полная память процесса/ноутбука; размер адаптера включает токенизатор и manifest. Время включает обучение и val, но исключает финальную генерацию и логирование модельного пакета.', '',
        '## 2. Набор и единый протокол','',
        '50 разных товаров из test ДЗ 3, три среза, 3 behavioral-кейса. '
        'Индекс train содержит все 3185 вопросов. Полная повторная токенизация подтвердила '
        'совпадение с обоими .pt (tokenization-lineage.json). '
        'Точных текстовых/групповых пересечений: 0. Смысловые перефразы этим не исключаются.',
        'Метки проверены ассистентом по каждому фрагменту; лист для отдельного человеческого '
        'подтверждения — golden-review.md. Не заявляем независимую человеческую валидацию без подтверждения.',
        'Срезы и риски зафиксированы заранее в evaluation-plan.md. Greedy, seed 42, 32 новых токена, '
        'одинаковые system-инструкция и scorer. Проверяется фактологическая опора утверждений '
        'на отзыв; это диагностическая подзадача, не полная оценка свободного резюме.',
        f'Протокол SHA256: `{b["protocol_sha256"]}`. Golden SHA256: `{b["golden_sha256"]}`.',
        f'[Реальный evaluation run]({eval_url}): `{b["evaluation_run_id"]}`.', '',
        '## 3. Качество: реальные ответы','',
        '| Срез | n | База correct | Кандидат correct | База accuracy | Кандидат accuracy | База valid | Кандидат valid |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name, aa, bb in [('overall',a['overall'],b['overall'])]+[(s,a['slices'][s],b['slices'][s]) for s in sorted(a['slices'])]:
        lines.append(f'| {name} | {aa["n"]} | {aa["correct"]} | {bb["correct"]} | {aa["accuracy"]:.1%} | {bb["accuracy"]:.1%} | {aa["valid"]} | {bb["valid"]} |')
    lines += ['',f'Format rate: база {a["overall"]["format_rate"]:.1%}, кандидат {b["overall"]["format_rate"]:.1%}. '
        f'Ошибки генерации: база {a["overall"]["errors"]}, кандидат {b["overall"]["errors"]}. '
        'Все 50 вопросов остаются в знаменателе; срезы здесь не пересекаются.',
        f'Исправлены: `{diff["improved"]}`.',f'Новые ошибки: `{diff["regressed"]}`.','',
        '## 4. Behavioral и отрицательная проверка','',
        '| Кейс | База: raw | Кандидат: raw | Кандидат прошёл |','|---|---|---|---|']
    ab={r['id']:r for r in a['behavioral']['per_item']}
    for r in b['behavioral']['per_item']:
        lines.append(f'| {r["id"]} | `{inline(ab[r["id"]]["raw"])}` | `{inline(r["raw"])}` | {r["correct"]} |')
    lines += ['',f'Отрицательная проверка: rejected={negative["rejected"]}; причина: {negative["reason"]}. '
        'Испорчена копия ответов, веса и alias не менялись. Дополнительно scripts/check_raw_negative.py '
        'проверяет порчу только raw-ответов с согласованными counts и неизменённым behavioral '
        '(metrics/negative-raw-gate.json).', '', '## 5. Разбор пяти пар ответов','']
    aa={r['id']:r for r in a['per_item']};bb={r['id']:r for r in b['per_item']}
    golden={r['id']:r for r in [json.loads(l) for l in Path('data/eval/golden.jsonl').read_text().splitlines()]}
    selected=list(dict.fromkeys(diff['regressed'][:2]+diff['improved'][:2]+list(golden)))[:5]
    for i,key in enumerate(selected,1):
        r=golden[key]
        lines += [f'### {i}. {key}', '', r['input'], '',
            f'Эталон: `{r["expected"]}`. Основание: {r["evidence"].rstrip(".")}.',
            f'База: `{inline(aa[key]["raw"])}` (correct={aa[key]["correct"]}).',
            f'Кандидат: `{inline(bb[key]["raw"])}` (correct={bb[key]["correct"]}).',
            'Разбор: ' + PAIR_ANALYSIS.get(key, 'Правильность определяется приведённым свидетельством из отзыва.'), '']
    lines += ['## 6. Выпуск, хранение и ограничения','',
        f'Сохранность: probe run `{storage["run_id"]}`, SHA256 `{storage["sha256"]}`; '
        'файл скачан после настоящего пересоздания контейнера MLflow.',
        ('Кандидат принят командой make promote; baseline остаётся исходной моделью, '
         'автоматического baseline-update нет.' if gate['passed'] else
         'Кандидат не выпускается, champion не создаётся/не меняется; baseline остаётся исходной моделью. '
         'Далее: анализировать ошибки на отдельном development-наборе, проверять соответствие задачи обучения '
         'задаче оценки; новый протокол версионировать заранее. Обе версии дали 0/17 на '
         'contradicted_claims — отсутствие регрессии не означает приемлемого абсолютного качества. '
         'Для следующего эксперимента заранее добавить минимальный порог качества этого среза '
         'и отдельные обучающие примеры с отрицанием/противопоставлением. Не понижать пороги ради PASS.'), '',
        'BLEU/ROUGE и сходство embeddings не заменяют проверку фактов: замена отрицания или '
        'выдуманная цифра почти не меняют словесное сходство. Здесь scorer проверяет строгую '
        'метку и формат, но не оценивает полноту и стиль свободного резюме. Модель-судья '
        'полезна для сопоставления фактов длинного резюме с отзывами; риски — предпочтение '
        'длинных ответов, порядка A/B, знакомого стиля и собственные галлюцинации. Нужны '
        'скрытые имена моделей, смена A/B, рубрика, сохранённые решения и человеческий '
        'разбор спорных случаев. Judge в обязательных измерениях не использовалась.', '',
        'Дополнительные ограничения golden: первые 50 test-товаров не являются случайной выборкой; '
        'несколько товаров относятся к одной группе, поэтому наблюдения могут зависеть друг от друга. '
        'В NOT_STATED преобладают отсутствующие числовые характеристики/гарантии; это не полное '
        'покрытие возможных галлюцинаций резюме.', '',
        'Ссылки MLflow локальные: преподаватель увидит их на видео/этой машине. '
        'Переносимые raw-ответы и отчёты находятся в metrics/ внутри архива hw67-submission.zip; веса и volumes '
        'в архив сдачи не включаются.', '']
    Path('docs/conclusion.md').write_text('\n'.join(lines))
    Path('docs/run-links.json').write_text(json.dumps(dict(training=links,evaluation=eval_url),indent=2)+'\n')


if __name__ == '__main__':
    main()
