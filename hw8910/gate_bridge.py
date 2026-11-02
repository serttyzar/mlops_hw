"""Reuses unchanged HW6+7 scorer/gate; binds full reports and raw outputs to runtime."""
import json
from pathlib import Path
import sys
from release import HASH_FIELDS, digest, fingerprint

HW67 = Path(__file__).resolve().parents[1] / 'hw67'
sys.path.insert(0, str(HW67))
from evaluation.contract import fingerprint as old_fingerprint, read_rows, validate_golden
from evaluation.solution import quality_gate, score


def read(path):
    return json.loads(Path(path).read_text())


def evidence_paths(root, evidence):
    result = {}
    for key, info in evidence.items():
        path = (Path(root) / info['path']).resolve()
        if digest(path.read_bytes()) != info['sha256']:
            raise ValueError('Evidence checksum mismatch: ' + key)
        result[key] = path
    required = {'baseline', 'candidate', 'baseline_raw', 'candidate_raw',
                'golden', 'behavioral', 'training_index', 'protocol'}
    if set(result) != required:
        raise ValueError('Missing/extra evidence files')
    return result


def verify_full_reports(paths, runtime_sha256):
    protocol = read(paths['protocol'])
    rows, behavioral, training = [read_rows(paths[k]) for k in ('golden', 'behavioral', 'training_index')]
    validate_golden(rows, training, behavioral)
    for key, value in [('golden', rows), ('behavioral', behavioral), ('training_index', training)]:
        if protocol['datasets'][key] != old_fingerprint(value):
            raise ValueError('Dataset changed: ' + key)
    reports = []
    expected_code = {name: digest((HW67 / 'evaluation' / name).read_bytes())
                     for name in ('solution.py', 'contract.py')}
    if protocol['scoring_code'] != expected_code:
        raise ValueError('Scoring code changed; reevaluation required')
    if fingerprint(protocol['runtime']) != runtime_sha256:
        raise ValueError('Protocol runtime mismatch')
    if protocol['runtime']['backend_sha256'] != digest((Path(__file__).parent / 'backends.py').read_bytes()):
        raise ValueError('Generation code changed; reevaluation required')
    for role in ('baseline', 'candidate'):
        report = read(paths[role])
        raw = read(paths[role + '_raw'])
        if (report['runtime_sha256'] != runtime_sha256 or report['protocol_sha256'] != fingerprint(protocol)
                or report['golden_sha256'] != old_fingerprint(rows)
                or report['baseline_spec_sha256'] != old_fingerprint(protocol['baseline_spec'])
                or report['outputs_sha256'] != old_fingerprint(raw) or report.get('synthetic') is not False):
            raise ValueError('Unbound evaluation metadata: ' + role)
        by_id = {o['id']: o for o in raw}
        if len(by_id) != len(raw) or set(by_id) != {r['id'] for r in rows + behavioral}:
            raise ValueError('Missing/duplicate raw response ids')
        recalculated = score(rows, [by_id[r['id']] for r in rows])
        if any(report[k] != recalculated[k] for k in ('overall', 'slices', 'per_item')):
            raise ValueError('Metrics differ from raw outputs')
        if report['behavioral'] != score(behavioral, [by_id[r['id']] for r in behavioral]):
            raise ValueError('Behavioral differs from raw outputs')
        reports.append(report)
    return reports, protocol


def build_release_report(manifest, evidence, root, selection):
    paths = evidence_paths(root, evidence)
    (baseline, candidate), protocol = verify_full_reports(paths, manifest['runtime_sha256'])
    if selection not in ('baseline-retention', 'candidate'):
        raise ValueError('Unknown selection')
    selected = baseline if selection == 'baseline-retention' else candidate
    if selected['model'] != manifest['model_spec'] or selected['model_sha256'] != manifest['model_sha256']:
        raise ValueError('Evaluation belongs to another model artifact')
    if selected['protocol_sha256'] != manifest['protocol_sha256'] or selected['golden_sha256'] != manifest['golden_sha256']:
        raise ValueError('Evaluation belongs to another protocol/golden')
    if fingerprint(manifest['runtime']) != manifest['runtime_sha256'] or manifest['runtime'] != protocol['runtime']:
        raise ValueError('Manifest runtime differs from evaluation')
    try:
        result = quality_gate(baseline, selected, protocol['critical_slices'])
        passed, reason = result.get('passed') is True, None
    except ValueError as exc:
        passed, reason = False, str(exc)
    return dict(status='complete', passed=passed, reason=reason,
                evaluation_run_id=selected['evaluation_run_id'], model_id=manifest['model_id'],
                **{k: manifest[k] for k in HASH_FIELDS}, metrics={k: selected['overall'][k] for k in ('accuracy', 'format_rate')},
                evidence=evidence, selection=selection, synthetic=False,
                note='Baseline retained after candidate rejection; self-check is not acceptance of the candidate'
                     if selection == 'baseline-retention' else 'Full HW6+7 quality gate')


def verify_release_report(manifest, report, root):
    if report.get('synthetic') is not False:
        raise ValueError('Real release needs real evaluation')
    actual = build_release_report(manifest, report['evidence'], root, report['selection'])
    if actual != report:
        raise ValueError('Release report differs from full gate result')
    if report['passed'] is not True:
        raise ValueError('Quality gate rejected: ' + str(report['reason']))

