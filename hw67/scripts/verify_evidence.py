"""Offline verification of packaged raw answers, hashes, counts and gate result."""
import json
from pathlib import Path
from evaluation.contract import fingerprint, read_json, read_rows, validate_golden
from evaluation.solution import score, quality_gate

cfg = read_json('evaluation/params.json')
rows = read_rows(cfg['golden'])
training = read_rows(cfg['training_index'])
behavior = read_rows(cfg['behavioral'])
proof = validate_golden(rows, training, behavior)
base = read_json('evaluation/baseline.json')
protocol = dict(config=cfg, golden=rows, behavioral=behavior, training_index=training,
                evaluation_code={p.name:p.read_text() for p in sorted(Path('evaluation').glob('*.py'))})
reports = []
for role in ('baseline','candidate'):
    outputs = read_json(f'metrics/{role}-outputs.json')
    report = read_json(f'metrics/{role}-eval.json')
    by_id = {o['id']:o for o in outputs}
    assert len(by_id) == len(outputs) and set(by_id) == {r['id'] for r in rows+behavior}
    actual = score(rows, [by_id[r['id']] for r in rows])
    assert all(report[k] == actual[k] for k in actual), f'{role}: counts differ'
    assert report['behavioral'] == score(behavior,[by_id[r['id']] for r in behavior])
    assert report['outputs_sha256'] == fingerprint(outputs)
    assert report['golden_sha256'] == fingerprint(rows)
    assert report['protocol_sha256'] == fingerprint(protocol)
    assert report['baseline_spec_sha256'] == fingerprint(base)
    assert report['leakage'] == proof
    reports.append(report)
a = {r['id']: r for r in reports[0]['per_item']}
b = {r['id']: r for r in reports[1]['per_item']}
assert read_json('metrics/eval-diff.json') == dict(
    improved=[i for i in a if not a[i]['correct'] and b[i]['correct']],
    regressed=[i for i in a if a[i]['correct'] and not b[i]['correct']])
assert reports[0]['model'] == base
assert reports[1]['model'] == read_json('metrics/candidate-model.json')
try:
    result = quality_gate(*reports,cfg['critical_slices'])
except ValueError as exc:
    result = dict(passed=False,reason=str(exc))
assert read_json('metrics/quality-gate.json') == result
print(json.dumps(dict(verified=True, leakage=proof, quality_gate=result),indent=2,ensure_ascii=False))
