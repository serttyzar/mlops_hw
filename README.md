# MLOPS-26 — домашние работы

Каждая работа — самостоятельный проект со своим окружением (`uv sync`)
и самопроверкой (`make check`).

| Папка | Тема |
|---|---|
| `hw1/` | Запуск Qwen3-0.6B локально, бенчмарк генерации |
| `hw2/` | Анатомия трансформера: инспекция модели, активации |
| `hw3/` | DVC-пайплайн данных: collect → clean → split, датасет суммаризации отзывов |
| `hw4/` | Стадия tokenize: chat template, маска лосса, packing |
| `hw5/` | LoRA-обучение, сравнение вариантов и проверка переносимости адаптера |
| [`hw67/`](hw67/) | Шесть LoRA-экспериментов в MLflow, проверка качества и решение о выпуске |

## Запуск

```bash
cd hw5          # или hw1 / hw2 / hw3 / hw4
uv sync
make train
make compare
make check
```

Данные (`data/`) в репозитории не хранятся: вход hw4 — выход стадии split
из hw3 (`data/train.jsonl`, `data/val.jsonl`).

Вход hw5 — токенизированные файлы из hw4 (`data/tokenized/train.pt`, `val.pt`).
Для полного результата: `make train && make compare && make check`; подробности — в `hw5/README.md`.

## ДЗ 6+7

[Итоговый отчёт](hw67/docs/conclusion.md), [изменения](hw67/docs/changes.md),
[сценарий видео](hw67/docs/video.md).
Проверки: 9/9 MLflow и 24/24 теста. Кандидат не принят в champion:
accuracy 62% → 58%, format rate 100% → 96%. Отказ предусмотрен заданием.

Данные проверки под DVC и измерения доступны в отдельном
[приложении evidence.zip](hw67/submission/evidence.zip), без весов и тензоров обучения.
Для проверки сохранённых результатов без модели и MLflow:

```bash
cd hw67
unzip -o submission/evidence.zip
python3 -m scripts.verify_evidence
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Перед сдачей нужно записать видео 5–8 минут и подтвердить
[разметку golden set](hw67/docs/golden-review.md).
