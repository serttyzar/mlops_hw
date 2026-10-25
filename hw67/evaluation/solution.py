"""Strict label scoring and fail-closed release policy; no model dependencies."""
import json
import math


def _index(items, name):
    if not isinstance(items, list) or not items:
        raise ValueError(f'{name}: empty or invalid list')
    result = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
            raise ValueError(f'{name}: invalid id')
        if item['id'] in result:
            raise ValueError(f'{name}: duplicate id {item["id"]}')
        result[item['id']] = item
    return result


def _counts(items):
    n = len(items)
    correct = sum(r['correct'] for r in items)
    valid = sum(r['valid'] for r in items)
    errors = sum(r['error'] is not None for r in items)
    return dict(n=n, correct=correct, valid=valid, errors=errors,
                accuracy=correct / n, format_rate=valid / n)


def _aggregate(items):
    groups = sorted({s for r in items for s in r['slices']})
    return dict(overall=_counts(items),
                slices={s: _counts([r for r in items if s in r['slices']]) for s in groups},
                per_item=items)


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('Duplicate JSON key')
        obj[key] = value
    return obj


def score(rows, outputs):
    """Score every input, matching by id, including malformed/error outputs."""
    tasks, answers = _index(rows, 'rows'), _index(outputs, 'outputs')
    if tasks.keys() != answers.keys():
        raise ValueError('Input and output ids differ')
    items = []
    for key, row in tasks.items():
        choices, slices = row.get('choices'), row.get('slices')
        if (not isinstance(choices, list) or not choices or
                any(not isinstance(c, str) for c in choices) or row.get('expected') not in choices):
            raise ValueError(f'{key}: invalid choices/expected')
        if (not isinstance(slices, list) or not slices or
                any(not isinstance(s, str) or not s for s in slices) or len(set(slices)) != len(slices)):
            raise ValueError(f'{key}: invalid slices')
        output = answers[key]
        if 'raw' not in output or 'error' not in output:
            raise ValueError(f'{key}: missing raw/error')
        raw, error = output['raw'], output['error']
        if error is not None and (not isinstance(error, str) or not error):
            raise ValueError(f'{key}: error must be null or nonempty string')
        parsed, valid = None, False
        if isinstance(raw, str):
            try:
                value = json.loads(raw, object_pairs_hook=_unique_object)
                if (isinstance(value, dict) and set(value) == {'answer'} and
                        isinstance(value['answer'], str) and value['answer'] in choices):
                    parsed = value['answer']
                    valid = error is None
            except (ValueError, TypeError):
                pass
        items.append(dict(id=key, raw=raw, parsed=parsed, valid=valid,
                          correct=valid and parsed == row['expected'], error=error, slices=list(slices)))
    return _aggregate(items)


def _validate_scored(report, name, minimum):
    if not isinstance(report, dict):
        raise ValueError(f'{name}: missing report')
    items = list(_index(report.get('per_item'), name).values())
    if len(items) < minimum:
        raise ValueError(f'{name}: need at least {minimum} items')
    for item in items:
        if not {'id', 'raw', 'parsed', 'valid', 'correct', 'error', 'slices'} <= item.keys():
            raise ValueError(f'{name}: incomplete per_item')
        if type(item['valid']) is not bool or type(item['correct']) is not bool:
            raise ValueError(f'{name}: invalid flags')
        if item['correct'] and not item['valid']:
            raise ValueError(f'{name}: correct but invalid')
        if item['error'] is not None:
            raise ValueError(f'{name}: generation error on {item["id"]}: {item["error"]}')
        if item['valid'] and (not isinstance(item['parsed'], str) or not isinstance(item['raw'], str)):
            raise ValueError(f'{name}: invalid parsed answer')
        slices = item['slices']
        if (not isinstance(slices, list) or not slices or
                any(not isinstance(s, str) or not s for s in slices) or len(set(slices)) != len(slices)):
            raise ValueError(f'{name}: invalid slice membership')
    expected = _aggregate(items)
    if not isinstance(report.get('slices'), dict) or report['slices'].keys() != expected['slices'].keys():
        raise ValueError(f'{name}: missing/extra slices')
    for label, actual, wanted in [('overall', report.get('overall'), expected['overall'])] + [
            (s, report['slices'][s], counts) for s, counts in expected['slices'].items()]:
        if not isinstance(actual, dict):
            raise ValueError(f'{name}/{label}: missing counts')
        for k, value in wanted.items():
            got = actual.get(k)
            if k in ('accuracy', 'format_rate'):
                if (type(got) not in (int, float) or not math.isfinite(got) or
                        not 0 <= got <= 1 or not math.isclose(got, value, rel_tol=0, abs_tol=1e-12)):
                    raise ValueError(f'{name}/{label}: inconsistent {k}')
            elif type(got) is not int or got != value:
                raise ValueError(f'{name}/{label}: inconsistent {k}')
    return {r['id']: set(r['slices']) for r in items}


def quality_gate(baseline, candidate, critical_slices):
    """Allow no regression; validate completeness and counts before comparison."""
    try:
        if not isinstance(critical_slices, list) or not critical_slices or any(
                not isinstance(s, str) or not s for s in critical_slices):
            raise ValueError('Need at least one named critical slice')
        for report in (baseline, candidate):
            if report.get('status') != 'complete' or not isinstance(report.get('model'), dict) or not report['model']:
                raise ValueError('Incomplete report/model')
            model = report['model']
            if model.get('kind') == 'base' and (not model.get('name') or len(model.get('revision', '')) != 40):
                raise ValueError('Unpinned baseline model')
            if model.get('kind') == 'adapter' and any(not model.get(k) for k in ('name', 'version', 'run_id', 'uri')):
                raise ValueError('Incomplete adapter model')
            if not model.get('kind'):
                raise ValueError('Missing model kind')
        for key in ('protocol_sha256', 'golden_sha256', 'baseline_spec_sha256'):
            if not isinstance(baseline.get(key), str) or not baseline[key] or baseline[key] != candidate.get(key):
                raise ValueError(f'Incompatible {key}')
        a = _validate_scored(baseline, 'baseline', 50)
        b = _validate_scored(candidate, 'candidate', 50)
        if a != b or len(baseline['slices']) < 3:
            raise ValueError('Different ids/slice membership, or fewer than three slices')
        ba = _validate_scored(baseline.get('behavioral'), 'baseline behavioral', 3)
        bb = _validate_scored(candidate.get('behavioral'), 'candidate behavioral', 3)
        if ba != bb:
            raise ValueError('Incompatible behavioral cases')
        for metric in ('accuracy', 'format_rate'):
            if candidate['overall'][metric] < baseline['overall'][metric]:
                raise ValueError(f'Overall {metric} regression')
        for s in critical_slices:
            if s not in baseline['slices'] or s not in candidate['slices']:
                raise ValueError(f'Missing critical slice {s}')
            if candidate['slices'][s]['accuracy'] < baseline['slices'][s]['accuracy']:
                raise ValueError(f'Critical slice {s}: accuracy regression')
        behavior = candidate['behavioral']['overall']
        if behavior['correct'] != behavior['n']:
            raise ValueError('Mandatory behavioral case failed')
        return dict(passed=True, critical_slices=critical_slices,
                    baseline=baseline['overall'], candidate=candidate['overall'],
                    behavioral_passed=behavior['n'], regression_tolerance=0)
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError(f'Invalid quality report: {exc}') from exc
