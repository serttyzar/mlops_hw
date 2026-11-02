"""Portable repository + Git history + small evidence, never weights/environments/cache."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
REPO=ROOT.parent
DEST=REPO.parent/'hw8910-submission.zip'
BUNDLE=REPO.parent/'repository.bundle'
subprocess.run(['git','bundle','create',str(BUNDLE),'--all'],cwd=REPO,check=True)
files=set(subprocess.check_output(['git','ls-files','-z'],cwd=REPO).decode().strip('\0').split('\0'))
for folder in ('hw67/data/eval','hw67/metrics'):
    files.update(str(p.relative_to(REPO)) for p in (REPO/folder).rglob('*') if p.is_file())
# Excluded files should never be present even if accidentally tracked later.
for name in files:
    parts=Path(name).parts
    if any(p in ('.venv','.models','.real','.git','__pycache__') or p.startswith('.lab') for p in parts):
        raise ValueError('Forbidden package member: '+name)
    if Path(name).suffix in ('.safetensors','.pt','.bin') or Path(name).name=='.env':
        raise ValueError('Forbidden package member: '+name)
checksums={}
with zipfile.ZipFile(DEST,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    for name in sorted(files):
        path=REPO/name
        if path.is_file():
            archive.write(path,'mlops_hw/'+name)
            checksums['mlops_hw/'+name]=hashlib.sha256(path.read_bytes()).hexdigest()
    archive.write(BUNDLE,'repository.bundle')
    checksums['repository.bundle']=hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    archive.writestr('SHA256.json',json.dumps(checksums,sort_keys=True,indent=2)+'\n')
print(DEST)
print('Archive SHA256:',hashlib.sha256(DEST.read_bytes()).hexdigest())
print('Archive bytes:',DEST.stat().st_size)
