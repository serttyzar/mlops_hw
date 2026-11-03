# ДЗ 8+9+10 — проверяемый выпуск и API AmaSum/Qwen

Продолжение ДЗ 1–7 в том же репозитории и с прежней Git-историей.
Исправлены семь причин ошибок, добавлены Prefect-flow, FastAPI с внедряемым
backend, интеграционные тесты и реальные измерения.

**Кандидат отклонён: accuracy 62% → 58%, format rate 100% → 96%.**
API обслуживает исходную Qwen3-0.6B, закреплённую в baseline ДЗ 6+7.
Повторного обучения/sweep нет. Прежние тесты не изменены.

- [Единый отчёт](docs/conclusion.md), [изменения](docs/changes.md), [семь дефектов](docs/defects.md), [таблица CSV](defects.csv).
- [Команды и доступ к артефактам](docs/access.md), [видео](docs/video.md).
- [Сырые ответы, отчёты и замер](metrics/), [красные/зелёные логи](docs/logs/).

## Быстрый запуск

Python 3.12, uv 0.12.10. Из каталога `hw8910`:

```bash
uv sync --frozen                 # FastAPI + Prefect, без Torch/весов
python3 scripts/restore_previous.py
make check                      # 51 проверка, без модели и MLflow
python3 scripts/verify_evidence.py  # проверка сохранённых реальных результатов
```

Только стандартная библиотека: `make check-core PYTHON=python3.12`.
Для HTTP-тестов нужен разрешённый localhost. Исходные 13 тестов находятся
в `tests/`, новые — отдельно в `tests_integration/`; `hw67/tests/` сохранён.

```bash
make init WORKSPACE=.lab-demo
make flow WORKSPACE=.lab-demo
.venv/bin/python api.py --workspace .lab-demo --backend stub --port 8091
```

Это явно помеченный **STUB ONLY**, а не ответы/оценка Qwen.

## Реальная модель на машине автора

Готовая `.real` и веса в `.models` уже подготовлены локально:

```bash
uv sync --frozen --extra model
make verify
make serve                      # http://127.0.0.1:8091, baseline
make measure                    # отдельный сервер, 8+8 реальных запросов
```

Полное восстановление, переоценка и ограничения переноса на CPU —
в [access.md](docs/access.md). Веса, окружения, кеши и Docker-volumes в Git
и архив сдачи не включаются. Публикация означает локальный `accepted.json`;
MLflow alias champion не менялся.
