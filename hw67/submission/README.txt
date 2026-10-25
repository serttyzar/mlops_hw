Реальные логи прогонов и проверок. Синтетические fixture-model в конце check-all.log принадлежат unit-тестам и не изменяют реальный registry.

Данные и результаты на GitHub: evidence.zip.
Из каталога hw67 выполнить: unzip -o submission/evidence.zip
Затем: python3 -m scripts.verify_evidence
Архив содержит data/eval/*.jsonl (DVC-версии), metrics/*.json и логи;
веса, тензоры, виртуальное окружение и Docker volumes исключены.
SHA256 файлов находится внутри submission/evidence-sha256.json.
