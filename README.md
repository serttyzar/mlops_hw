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
