# ДЗ 11 — Kubernetes под нагрузкой

Продолжение API из [ДЗ 8–10](../hw8910/README.md). Сервис работает в локальном
Kubernetes-кластере с двумя репликами. В опытах используются управляемый
CPU-имитатор, ограниченная очередь и потоковые ответы: `backend_mode=simulated`.

Полный прогон: **15 812 записей запросов, 29 Kubernetes-проверок, 12 сравнений
балансировки, масштабирование 2→3→2 и два алерта**.

- [Отчёт](report.html), [фактические результаты](docs/results.md), [ревью](docs/review.md).
- [Протокол опытов и формулы](docs/experiments.md), [проверки](docs/validation.md).
- [Сырые результаты](results/terminal-20261115T212518Z-5488/).
- [Коммит с кодом и измерениями](https://github.com/serttyzar/mlops_hw/commit/a490e568be55cd3bc76265b85e22ede3ac2ce25b).

## Реализация

`app/backend.py` продолжает интерфейс `hw8910.backends.StubBackend`:
`generate(prompt, limit)`. API сохраняет `POST /generate` с `prompt` и
`max_new_tokens`, добавляет `stream`, `messages` и `session_id`. Поток NDJSON
заканчивается записью `done=true`. Ответы содержат request ID, Pod, версию
и хеш фактически загруженного артефакта.

В каждом Pod работают CPU-вычисления с учётом `thread_time`, ограничение
параллелизма, очередь и локальный LRU-кеш префиксов. Артефакты A, B и BAD
включены в образ; при запуске проверяются SHA256 и версия. Историю диалога
передаёт клиент, поэтому она переживает замену Pod.

Helm-чарт задаёт Deployment, ConfigMap, два Service над общими репликами,
ресурсные ограничения, startup/readiness/liveness probes, завершение активных
ответов и HPA. Prometheus собирает метрики каждого API Pod через discovery,
а также cAdvisor и kube-state-metrics. Конфигурации мониторинга, два алерта
и экспорт дашборда Grafana находятся в `charts/hw11/files/`.

## Подготовка и запуск

Требуются macOS, Docker CLI, Colima, Python 3.12 и доступ к реестрам образов.
Для стенда предусмотрены 4 CPU, 6 GiB RAM и не менее 8 GiB свободного места.
Перед созданием кластера скрипт сохраняет свободное место и лимиты окружения.

```sh
cd hw11
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-report.txt
bash scripts/run_local.sh
```

Скрипт устанавливает закреплённые версии kubectl, Helm и kind в `.tools`,
проверяет их SHA256, запускает отдельный профиль Colima `mlops-hw11`, собирает
образ и выполняет все опыты. Результаты каждого запуска сохраняются в
`results/terminal-<дата>-<pid>/`. Полный цикл занимает около часа.

Кластер называется `mlops-hw11`, context — `kind-mlops-hw11`, namespace —
`hw11`. Kubeconfig хранится в `.state/kubeconfig`.

## Проверки

```sh
make check PYTHON=.venv/bin/python
python3 scripts/verify_evidence.py results/terminal-20261115T212518Z-5488
```

Первая команда запускает 23 локальных теста: прогрев, версии и хеши, очередь,
обрывы потоков, восстановление клиентской истории, изоляцию диалогов и чтение
сжатых журналов. Вторая проверяет опубликованные доказательства полного
Kubernetes-прогона и не требует работающего кластера.

Результат проверки сохранённой сдачи:

```json
{"complete": true, "missing": [], "requests": 15812, "checks_passed": 29}
```

## Воспроизведение опытов

Все опыты выполняются скриптом `scripts/cluster.py`. После подготовки стенда
можно запускать их отдельно, сохраняя новые результаты в отдельных каталогах:

```sh
export PATH="$PWD/.tools:$PATH"
export KUBECONFIG="$PWD/.state/kubeconfig"
export DOCKER_CONTEXT=colima-mlops-hw11
.venv/bin/python scripts/cluster.py A --output results/repeat-A
.venv/bin/python scripts/cluster.py B --output results/repeat-B
.venv/bin/python scripts/cluster.py C --output results/repeat-C
```

A проверяет прогрев до/после исправления, штатное обновление, неготовую версию,
откат, аварийный выход процесса и замену Pod. B сравнивает обычный Service
и ClientIP на одинаковых расписаниях: два профиля трафика, по три повтора,
каждый по 120 с. C проверяет CPU HPA, горячую реплику и состояния алертов.

Генератор работает в четырёх клиентских Pod, сохраняет запланированное
и фактическое время отправки, отдельно учитывает отказы, обрывы и тайм-ауты.
Клиентских повторов нет. Полные условия и ограничения приведены
в [протоколе](docs/experiments.md).

## Мониторинг

Для просмотра после запуска выполните в отдельных терминалах:

```sh
export KUBECONFIG="$PWD/.state/kubeconfig"
kubectl --context kind-mlops-hw11 -n hw11 port-forward svc/hw11-grafana 3000:3000
kubectl --context kind-mlops-hw11 -n hw11 port-forward svc/hw11-prometheus 9090:9090
```

Grafana: http://127.0.0.1:3000, дашборд `HW11 · CPU simulation`.
Prometheus: http://127.0.0.1:9090. Рабочие запросы идут из Pod через Service.
Port-forward используется для просмотра мониторинга.

## Версии и ресурсы

| Компонент | Версия или ограничение |
|---|---|
| Python | 3.12.12-slim-bookworm |
| kind / Kubernetes | 0.27.0 / kindest/node:v1.32.2 |
| kubectl / Helm | 1.32.2 / 3.16.4 |
| metrics-server | 0.7.2; insecure kubelet TLS только для локального kind |
| Prometheus / Grafana | 3.2.1 / 11.6.0 |
| kube-state-metrics | 2.15.0 |
| API Pod | requests 250m CPU, 96Mi; limits 1 CPU, 256Mi |
| Prometheus | limits 500m/384Mi; retention 2h и 256 MB; emptyDir ≤512Mi |
| Grafana | limits 300m/256Mi; emptyDir ≤128Mi |
| Клиентский Pod, 4 шт. | limits 500m/192Mi; emptyDir ≤128Mi |

Зависимости закреплены в `requirements.txt`. Веса модели и PVC не используются.
Фактический API-образ занимает 48,4 MiB, выделенные блоки дисков Colima —
около 5,54 GiB. Замеры: `image.json`, `size-after.txt`, `host-vm-size.json`
в каталоге полного прогона.

## Данные и отчёт

В каталоге результатов находятся запросы, расписания, журналы API и клиентов,
события Ready/EndpointSlice/HPA, конфигурация сети, снимки счётчиков Prometheus,
`checks.jsonl`, `summary.json`, `verification.json` и четыре графика.

`events.jsonl.gz` содержит полный журнал событий, сжатый без потерь.
SHA256 до/после распаковки совпадает; контрольные суммы и размеры записаны
в `evidence-manifest.json`. `requests.jsonl` опубликован без сжатия.
Проверка и генератор отчёта читают оба формата событий автоматически.

Построение отчёта из сохранённых данных:

```sh
make report PYTHON=.venv/bin/python RESULTS=results/terminal-20261115T212518Z-5488
```

`report.html` встраивает графики и переносится одним файлом. В нём приведены
исходные дефекты, изменения, результаты повторов, ограничения измерений
и письменное ревью с кейсом из лекции.

## Удаление стенда

```sh
export PATH="$PWD/.tools:$PATH"
export DOCKER_CONTEXT=colima-mlops-hw11
make cleanup PYTHON=.venv/bin/python
```

Команда удаляет кластер `mlops-hw11` и API-образ. Для остановки VM без удаления:

```sh
colima stop --profile mlops-hw11
```

После сохранения результатов профиль можно полностью удалить:

```sh
colima delete --profile mlops-hw11
```
