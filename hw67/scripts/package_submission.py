"""Export tracked code + portable evaluation evidence, never weights or volumes."""
import hashlib
import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

root = Path.cwd().parent
output = root.parent / 'hw67-submission.zip'
commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
required = ['sweep.json', 'candidate-model.json', 'baseline-outputs.json', 'candidate-outputs.json',
            'baseline-eval.json', 'candidate-eval.json', 'eval-diff.json', 'quality-gate.json',
            'negative-gate.json', 'storage-check.json', 'registry-state.json']
for name in required:
    if not (Path('metrics') / name).is_file():
        raise SystemExit(f'Missing evidence: {name}')
code = subprocess.check_output(['git', 'archive', '--format=tar', 'HEAD'], cwd=root)
bundle = root.parent / 'mlops_hw.bundle'
subprocess.run(['git', 'bundle', 'create', str(bundle), '--all'], cwd=root, check=True)
readme = f'''# ДЗ 6+7 — комплект сдачи

Git-коммит: {commit}

mlops_hw/ — код общего репозитория ДЗ 1–7 (git archive), плюс данные проверки
и реальные отчёты hw67. repository.bundle — полная Git-история для локального
клонирования: git clone repository.bundle restored_repo.

Начать с mlops_hw/hw67/docs/conclusion.md, changes.md, video.md.
Отпечаток исходных тестов: c42ac7788026. Видео 5–8 минут записать и приложить
отдельно. Человеческое подтверждение golden — см. docs/golden-review.md.

JSONL проверки находятся в mlops_hw/hw67/data/eval и соответствуют data/eval.dvc.
Для восстановления DVC cache после распаковки: из hw67 выполнить
`dvc commit data/eval.dvc`, затем `dvc status data/eval.dvc`.
Это отдельное приложение с доступными преподавателю данными: локальный DVC
remote автора не считается общедоступным сетевым хранилищем.

Веса, train/val .pt, .venv, Docker volumes и секреты исключены. Для повторного
обучения нужны результаты собственного ДЗ 4 или исходный DVC remote; на машине
автора они сохранены. Ссылки MLflow локальные и доступны на машине автора/видео.
Сохранённые метрики позволяют проверить оценку без загрузки модели:
`python -m scripts.verify_evidence` из hw67 (нужен только Python).
'''
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
    with tarfile.open(fileobj=io.BytesIO(code)) as archive:
        for entry in archive.getmembers():
            if entry.isfile():
                z.writestr('mlops_hw/' + entry.name, archive.extractfile(entry).read())
    for file in sorted(Path('metrics').glob('*.json')):
        z.write(file, 'mlops_hw/hw67/' + str(file))
    for file in sorted(Path('data/eval').glob('*.jsonl')):
        z.write(file, 'mlops_hw/hw67/' + str(file))
    for file in sorted(Path('submission').glob('*')):
        if file.is_file():
            name = 'mlops_hw/hw67/submission/' + file.name
            if name not in z.namelist():
                z.write(file, name)
    z.write(bundle, 'repository.bundle')
    z.writestr('START-HERE.md', readme)
with zipfile.ZipFile(output) as z:
    forbidden = [name for name in z.namelist() if any(part in name.split('/') for part in ('.venv', '.git', 'mlartifacts')) or name.endswith(('.pt', '.safetensors'))]
    if forbidden:
        raise SystemExit(f'Forbidden payload: {forbidden}')
    bad = z.testzip()
    if bad:
        raise SystemExit(f'Corrupt archive entry: {bad}')
print(json.dumps(dict(path=str(output), commit=commit, bytes=output.stat().st_size,
                     sha256=hashlib.sha256(output.read_bytes()).hexdigest()), indent=2))
