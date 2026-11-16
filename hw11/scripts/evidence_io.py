"""Read recorded JSONL verbatim, including losslessly compressed event logs."""
import gzip
import json
from pathlib import Path


def jsonl_exists(path):
    path = Path(path)
    return path.exists() or Path(str(path) + '.gz').exists()


def read_jsonl(path):
    path = Path(path)
    compressed = path.suffix == '.gz'
    if not path.exists():
        path = Path(str(path) + '.gz')
        compressed = True
    opener = gzip.open if compressed else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)
