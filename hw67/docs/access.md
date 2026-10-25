# Запуск и доступ к данным

## Готовая рабочая копия на этом компьютере

```bash
cd /Users/serttyzar/Projects/MLOps/hw67/mlops_hw/hw67
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
uv sync --frozen
colima start --cpu 2 --memory 2 --disk 15  # если VM остановлена
make up
make check-all
make verify-evidence
```

MLflow: http://127.0.0.1:5006. Установлены Docker CLI, Compose и Colima;
два учебных контейнера — PostgreSQL и MLflow. Клиент использует Python 3.12,
MLflow 2.22.2, Torch 2.14.0, Transformers 5.17.0, PEFT 0.21.0 из uv.lock.

Обучение уже выполнено, повторять sweep для сдачи и видео не нужно.
`make eval` **ожидаемо завершится ненулевым кодом**: accuracy 62% → 58%.
`make check-negative` завершится успешно, потому что порча ответов отклоняется.
Не запускайте promote для отклонённого кандидата. Baseline не обновлялся.
В выводе unit-тестов встречается `fixture-model/2` и фиктивный champion:
это изолированная синтетическая фикстура, реальный реестр она не меняет.

`make down` останавливает сервер, сохраняя volumes. Не используйте `down -v`:
этим удаляется сохранённое состояние учебного MLflow.

## DVC

Версия используемого CLI — DVC 3.67.1. Если CLI отсутствует:

```bash
uv tool install 'dvc==3.67.1'
dvc pull data/eval.dvc data/tokenized.dvc
dvc status
```

Локальный remote — `/Users/serttyzar/Projects/MLOps/hw67/dvc_remote`, путь
в конфиге относительный. На машине автора cache и remote синхронизированы.
Это не ссылка для преподавателя: файлы проверки отдельно вложены в архив.
Тензоры train/val в архив сдачи не входят, но лежат в локальном remote.

`data/eval.dvc` версионирует golden (50), behavioral (3) и полный train-index
(3185). `data/tokenized.dvc` версионирует точные .pt из ДЗ 4. Связь с сырьём
подтверждается docs/tokenization-lineage.json и исходным кодом hw3/hw4.
Для пересборки eval используются исходные hw4/data/train.jsonl и hw3/data/test.jsonl;
на этой машине они скопированы без изменений из предыдущих работ.

## Преподавателю: переносимый комплект

`hw67-submission.zip` содержит tracked-код всех ДЗ, полную Git-историю в
`repository.bundle`, три JSONL проверки, JSON-метрики и логи. В нём нет
весов, .pt, .venv и volumes.

После распаковки можно сразу проверить ответы и counts без модели и сервера:

```bash
cd mlops_hw/hw67
python3 -m scripts.verify_evidence
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Для работы именно с Git/DVC клонируйте bundle в отдельный каталог, перенесите
из архива `data/eval` и `metrics` в его `hw67/` и выполните:

```bash
git clone repository.bundle restored_repo
# перенести mlops_hw/hw67/data/eval и metrics в restored_repo/hw67/
cd restored_repo/hw67
uvx --from 'dvc==3.67.1' dvc commit data/eval.dvc
uvx --from 'dvc==3.67.1' dvc status data/eval.dvc
```

Полный повтор обучения требует тензоров своего ДЗ 4 и скачивания закреплённой
базы Hugging Face. Архив доказывает сохранённые измерения, но не переносит
живой сервер с его registry. Его UI и реальные проверки показываются на видео.

Официальная документация по инфраструктуре:
[MLflow: хранение артефактов через сервер](https://www.mlflow.org/docs/latest/self-hosting/architecture/tracking-server/),
[uv в Docker](https://docs.astral.sh/uv/guides/integration/docker/).
