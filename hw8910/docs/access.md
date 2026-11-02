# Запуск, восстановление и доступ

## Рабочая копия

`/Users/serttyzar/Projects/MLOps/mlops26-hw8-9-10/mlops_hw` — новая копия
итогового репозитория ДЗ 6+7 с полной историей ДЗ 1–7. Предыдущие каталоги
с решениями не редактировались. Новая работа лежит в `hw8910/`.

```bash
cd /Users/serttyzar/Projects/MLOps/mlops26-hw8-9-10/mlops_hw/hw8910
uv sync --frozen --extra model
make verify
make serve
```

HTTP: `GET /health`, `GET /ready`, `POST /generate`.
Проверка: `curl -s http://127.0.0.1:8091/ready`.
Пока идёт загрузка/прогрев, ready/generate возвращают 503.
В запросе — `prompt` и `max_new_tokens: 32`. Это точный лимит протокола
для настоящей модели. Stub отдельно допускает целые значения 1..32.
Максимум 16 384 символа, 2048 токенов после chat template, 65 536 байт JSON;
веса и вычисления разделяются одним процессом, backend считает последовательно.
`/debug` автоматически включён только на stub; в реальном API — через `--debug`
для локального опыта. По умолчанию он отключён.

## Переносимые проверки без весов

После клонирования Git или восстановления bundle:

```bash
cd hw8910
uv sync --frozen
python3 scripts/restore_previous.py
make check
python3 scripts/verify_evidence.py
```

`restore_previous.py` читает уже существующий архив `hw67/submission/evidence.zip`:
восстанавливает JSONL проверки и старые метрики без весов. Новые результаты
ДЗ 8–10 хранятся непосредственно в Git в `hw8910/metrics/`.
`verify_evidence.py` требует только стандартную библиотеку и пересчитывает
метрики по raw-ответам, полный gate, привязку runtime, арифметику 8+8 запросов.
Новый eval или MLflow-сервер для этого не нужен.

## Публичная baseline и сохранённый допуск

```bash
uv sync --frozen --extra model
python3 scripts/restore_previous.py
python3 scripts/fetch_artifacts.py --base-only
python3 scripts/restore_baseline.py
make baseline
make serve
```

База публичная: `Qwen/Qwen3-0.6B`, revision
`c1899de289a04d12100db370d81485cdf75e47ca`.
Её файлы скачиваются в `.models/`, хеши пересчитываются перед публикацией
и загрузкой. Допуск относится к сохранённому runtime: MPS, bfloat16, greedy,
32 новых токена, enable_thinking=false и system-инструкция из `metrics/protocol.json`.
На другом устройстве нельзя просто исправить runtime/hash: нужна новая оценка.
Для CPU-переоценки скрипт выбирает float32 и создаёт новый runtime/протокол.

## Полный повтор оценки без обучения

На машине автора MLflow ДЗ 6+7: `http://127.0.0.1:5006`.
Его учебные контейнеры `mlops-hw6-db-1`, `mlops-hw6-mlflow-1` были запущены
для чтения сохранённого адаптера. Volumes, alias и training runs не менялись.

```bash
# При остановленных контейнерах:
docker start mlops-hw6-db-1 mlops-hw6-mlflow-1
python3 scripts/fetch_artifacts.py
make evaluate
make baseline
make candidate                 # ожидаемый ненулевой код: качество хуже baseline
```

Нужен доступ к исходному MLflow с training run и версией адаптера. Адрес localhost
не доступен преподавателю с другого компьютера. В комплекте есть переносимые
raw-ответы, отчёты и их хеши; сами веса адаптера доступны на машине автора
через этот MLflow. Для удалённого MLflow у `fetch_artifacts.py` есть
`--tracking-uri`. Training run и registered model version обязательно сверяются.
Новое обучение не требуется, `sweep` не запускался.

## Красные/зелёные опыты

```bash
python3 scripts/reproduce_before.py  # исходный Git-коммит → ожидаемые падения
make check                         # итоговый зелёный набор
make check-negative                # намеренно завершается ошибкой (make exit 2)
make check-flow                     # 12 команд, свежие workspace, ожидаемые отказы
```

`check-negative` меняет только временную копию функции cache_key; рабочий код
не ломается. В логе видны ошибка теста и exit code Python=1. `make` возвращает 2.
`check_flow.py` сам проверяет ожидаемые exit codes, состояние Cached/Completed,
сохранность указателя и единственное событие после восстановления журнала.
Сетевых retry в этом локальном flow нет. Отказы схемы, файлов и качества
не повторяются автоматически.

## Сдача

`hw8910-submission.zip` рядом с `mlops_hw/` содержит tracked-код всех ДЗ,
`repository.bundle`, новые метрики/логи и необходимые старые данные проверки.
Внутри — `SHA256.json`. Веса, .venv, .models, .real, Prefect DB/cache и volumes
исключены. Клонирование истории: `git clone repository.bundle restored_repo`.
Видео хранится отдельно от Git/архива кода; путь указан в `docs/video.md`.

Удалённый CI не запускался; `ci.example.yml` — только шаблон.
Защита merge не заявляется.
