"""Write-ahead intent + atomic JSONL ledger. Assumes exactly one writer."""
import json
from pathlib import Path
import uuid
from release import atomic_write, canonical, digest, fingerprint, publish, verify_artifact


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    atomic_write(path, canonical(value) + b'\n')


def events(root):
    path = root / 'publications.jsonl'
    return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []


def record_once(root, event):
    journal = events(root)
    if event['release_id'] not in {r['release_id'] for r in journal}:
        journal.append(event)
        atomic_write(root / 'publications.jsonl', b''.join(canonical(r) + b'\n' for r in journal))


def recover(root):
    pending = root / 'pending-publication.json'
    if not pending.exists():
        return
    intent = read(pending)
    target = root / 'accepted.json'
    if target.exists() and digest(target.read_bytes()) == intent['pointer_sha256']:
        record_once(root, intent['event'])
        print('RECOVERED', intent['event']['release_id'])
    pending.unlink()


def publish_once(root, manifest, report, prepared=None, fail_after_pointer=False):
    root = Path(root)
    recover(root)
    verify_artifact(manifest, root)
    # Preparation identity is independent of attempt UUID and does not identify a release.
    event = {'release_id': fingerprint({'manifest': manifest, 'report': report}),
             'model_id': manifest['model_id'], 'runtime_sha256': manifest['runtime_sha256'],
             'evaluation_run_id': report['evaluation_run_id']}
    intent = {'event': event, 'pointer_sha256': digest(canonical(manifest) + b'\n')}
    # Validate before recording intent; rejected reports never update pointer or ledger.
    from release import validate_release
    validate_release(manifest, report)
    write(root / 'pending-publication.json', intent)
    publish(manifest, report, root / 'accepted.json')
    if fail_after_pointer:
        raise RuntimeError('Injected failure after pointer before ledger')
    record_once(root, event)
    (root / 'pending-publication.json').unlink()
    print('PUBLISHED', manifest['model_id'], event['release_id'])
    return event


def attempt(root, status, **fields):
    # Attempts may repeat, release events may not. Short JSONL logs are diagnostic only.
    with (Path(root) / 'attempts.jsonl').open('a') as stream:
        stream.write(json.dumps({'attempt_id': str(uuid.uuid4()), 'status': status, **fields}) + '\n')

