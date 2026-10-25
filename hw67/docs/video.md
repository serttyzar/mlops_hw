# Одно видео для сдачи ДЗ 6+7 — 5–8 минут

Записывается экран с вашим голосом. Это обязательный материал сдачи;
готовые код и отчёт не заменяют видеозапись. Долгое обучение заранее,
в кадре показываем завершённые runs и повторяем короткие проверки.

## До записи

```bash
cd /Users/serttyzar/Projects/MLOps/hw67/mlops_hw/hw67
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
colima start --cpu 2 --memory 2 --disk 15  # если VM остановлена
make up
```

Откройте http://127.0.0.1:5006, docs/conclusion.md, docs/sweep-curves.png,
docs/comparison.csv, docs/defects.md и этот сценарий.
Не повторяйте `make sweep` и `baseline-init` для видео: результаты уже есть.
Числа, имя выбранного run и окончательное решение читайте в conclusion.md.

## 0:00–0:45 — о чём работа

«Я продолжаю тему резюме отзывов AmaSum из предыдущих ДЗ. ДЗ 3 готовило
данные, ДЗ 4 — токены, ДЗ 5 — LoRA. В ДЗ 6+7 я сравниваю шесть настроек,
сохраняю артефакты в MLflow и проверяю, можно ли выпустить лучший адаптер.
Меньший loss сам по себе не означает, что ответы стали лучше».

Покажите папки hw1–hw5 и hw67, `git log -3 --oneline`.

## 0:45–1:40 — четыре исправления

Покажите docs/defects.md и короткие участки кода:

1. src/sweep.py — nested run внутри цикла; шесть независимых runs.
2. compose.yaml — volumes для PostgreSQL и для /mlartifacts.
3. src/sweep.py — весь provenance, включая SHA256 train/val.
4. src/promote.py — gate() до register_model(), затем отдельный candidate.

Откройте params.yaml: 6 конфигураций, 4 шага, train 32 / val 8, seed 42.

## 1:40–2:40 — MLflow и выбор кандидата

В Experiments выберите hw67-amasum-lora. Разверните sweep, покажите шесть
FINISHED runs, параметры r/lr/target_modules и кривые val_loss.
Покажите docs/comparison.csv или график. Назовите победителя, loss до/после,
время и память из отчёта. Уточните ограничение четырёх шагов.

В Models откройте amasum-qwen-adapter: alias candidate, версию, ссылку на run.
В его Artifacts покажите model/MLmodel, adapter_config, tokenizer, manifest,
signature, input_example и samples.json. Это сохраняемый модельный пакет.

## 2:40–3:45 — данные и протокол качества

Покажите docs/golden-review.md: 50 разных товаров test, утверждение, эталон,
обоснование. В training-index — все 3185 train-вопросов. Отметьте проверку
групп topic и совпадение тензоров в tokenization-lineage.json.

Покажите data/eval.dvc и evaluation/params.json: три среза, два критических,
32 токена, одинаковый prompt, greedy decoding. Это проверка grounding:
подтверждается / противоречит / не указано. Она не оценивает всё резюме.

## 3:45–5:00 — ответы, гейт и решение

В docs/conclusion.md покажите таблицу accuracy/format/counts и новые ошибки.
Разберите 2–3 пары из пяти подготовленных: что в отзыве подтверждает эталон,
какой ответ дала база и кандидат. Откройте evaluation run с JSON-артефактами.

Покажите три behavioral-кейса и выполните:

```bash
make eval
make check-negative
```

Если make eval возвращает отказ, покажите ненулевой код (`echo $?` сразу
после make eval), JSON quality-gate и причину. Это допустимый итог, не ошибка
сдачи. Не запускайте promote при отказе. Если PASS — покажите champion,
который установлен штатным make promote; baseline сам не обновляется.
Отрицательная проверка портит копию ответов, не веса и не реестр.

## 5:00–6:45 — сохранность и тесты

```bash
make check-storage
make check-all
```

Покажите пересоздание контейнера, совпадение SHA256 probe, `Итог: 9/9`,
отпечаток tests/ `c42ac7788026` и 24 теста оценки `OK`.
Проверка загрузки запускает отдельный процесс без локального конфига.

## 6:45–7:15 — итог

Назовите решение о выпуске из conclusion.md. Объясните: сходство слов не
ловит подмену факта или отрицания; judge можно использовать для длинного
резюме, но нужны рубрика, скрытые имена, смена A/B и ручная проверка.
Покажите комплект submission и один итоговый Git-коммит.
