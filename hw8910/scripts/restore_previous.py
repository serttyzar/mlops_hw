"""Restore portable HW6+7 raw evidence from its existing tracked archive; no weights."""
from pathlib import Path
import zipfile

HW67 = Path(__file__).resolve().parents[2] / 'hw67'
with zipfile.ZipFile(HW67 / 'submission/evidence.zip') as archive:
    for name in archive.namelist():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Unsafe archive path')
        if name.startswith(('data/eval/','metrics/')) and not name.endswith('/'):
            target=HW67/path
            target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():
                target.write_bytes(archive.read(name))
print('Previous raw evidence restored; training and weights unchanged')
