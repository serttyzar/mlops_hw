"""Extra negative probe with internally consistent counts and unchanged behavior."""
import copy
from evaluation.cli import current_reports
from evaluation.contract import read_json, read_rows, write_json
from evaluation.solution import quality_gate, score

baseline, candidate = current_reports()
config = read_json('evaluation/params.json')
rows = read_rows(config['golden'])
broken = copy.deepcopy(candidate)
broken.update(score(rows, [dict(id=r['id'], raw='INVALID', error=None) for r in rows]))
try:
    quality_gate(baseline, broken, config['critical_slices'])
except ValueError as exc:
    write_json('metrics/negative-raw-gate.json', dict(rejected=True,reason=str(exc),
               counts_consistent=True, original_behavioral_unchanged=True,
               note='In-memory copy only; no weights, raw files or aliases changed'))
    print('PASS: consistent metrics from corrupted raw answers rejected:', exc)
else:
    raise SystemExit('FAIL: corrupted answers accepted')
