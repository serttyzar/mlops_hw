"""Re-encode all original HW4 rows and compare with the actual training tensors."""
import hashlib
import json
import sys
from pathlib import Path

import torch
import yaml
from transformers import AutoTokenizer

sys.path.insert(0, str(Path('../hw4').resolve()))
from src.tokenize_data import encode_example

params = yaml.safe_load(Path('../hw4/params.yaml').read_text())
tok = AutoTokenizer.from_pretrained(params['model']['name'], revision='c1899de289a04d12100db370d81485cdf75e47ca', local_files_only=True)
tok.padding_side = params['tokenize']['padding_side']
proof = {'method': 'Full re-encoding using unchanged HW4 code, comparison of ids/input_ids/attention_mask/labels', 'splits': {}}
for split in ('train', 'val'):
    source = Path(f'../hw4/data/{split}.jsonl')
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    tensor = Path(f'data/tokenized/{split}.pt')
    saved = torch.load(tensor, weights_only=False)['examples']
    actual = []
    for row in rows:
        encoded = encode_example(tok, row, params, params['tokenize']['max_seq_len'])
        meta = encoded.pop('_meta')
        if meta['supervised']:
            actual.append(encoded)
    if actual != saved:
        raise SystemExit(f'{split}: tensors differ from HW4 re-encoding')
    proof['splits'][split] = dict(source_rows=len(rows), tensor_rows=len(saved), all_tensors_match=True,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), tensor_sha256=hashlib.sha256(tensor.read_bytes()).hexdigest())
Path('docs/tokenization-lineage.json').write_text(json.dumps(proof, indent=2)+'\n')
print(json.dumps(proof, indent=2))
