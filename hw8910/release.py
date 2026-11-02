"""Строгий контракт метаданных и атомарный локальный указатель. Один писатель."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

HASH_FIELDS = ('model_sha256', 'protocol_sha256', 'golden_sha256', 'runtime_sha256')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return digest(canonical(value))


def cache_key(data_path: Path, config: dict) -> str:
    return fingerprint({'data_sha256': digest(Path(data_path).read_bytes()), 'config': config})


def validate_release(manifest: dict, report: dict) -> None:
    try:
        if not isinstance(manifest, dict) or not isinstance(report, dict):
            raise ValueError('Manifest/report must be objects')
        if type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
            raise ValueError('Unsupported schema_version')
        for key in ('model_id', 'artifact_uri'):
            if not isinstance(manifest.get(key), str) or not manifest[key].strip():
                raise ValueError('Missing ' + key)
        for key in HASH_FIELDS:
            if not isinstance(manifest.get(key), str) or not re.fullmatch('[0-9a-f]{64}', manifest[key]):
                raise ValueError('Invalid ' + key)
        if not isinstance(manifest.get('runtime'), dict) or not manifest['runtime']:
            raise ValueError('Missing runtime')
        if fingerprint(manifest['runtime']) != manifest['runtime_sha256']:
            raise ValueError('Stale runtime hash')
        if report.get('status') != 'complete' or report.get('passed') is not True:
            raise ValueError('Incomplete or rejected evaluation')
        if not isinstance(report.get('evaluation_run_id'), str) or not report['evaluation_run_id'].strip():
            raise ValueError('Missing evaluation_run_id')
        for key in ('model_id', *HASH_FIELDS):
            if report.get(key) != manifest[key]:
                raise ValueError('Report mismatch: ' + key)
        metrics = report.get('metrics')
        if not isinstance(metrics, dict):
            raise ValueError('Missing metrics')
        for key in ('accuracy', 'format_rate'):
            value = metrics.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('Invalid metric: ' + key)
        canonical(manifest)
    except (TypeError, KeyError, OverflowError) as exc:
        raise ValueError('Invalid release metadata') from exc


def atomic_write(target, data):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def publish(manifest: dict, report: dict, target: Path) -> None:
    validate_release(manifest, report)
    data = canonical(manifest) + b'\n'
    if Path(target).exists() and Path(target).read_bytes() == data:
        return
    atomic_write(target, data)


def artifact_hashes(artifact, names):
    artifact = Path(artifact)
    base = artifact.parent if artifact.is_file() else artifact
    result = {}
    if not isinstance(names, (dict, list)) or not names:
        raise ValueError('Missing pinned file list')
    for name in sorted(names):
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not name:
            raise ValueError('Unsafe artifact path')
        result[name] = digest((base / path).read_bytes())
    return result


def verify_artifact(manifest, root):
    uri = manifest['artifact_uri']
    if '://' in uri or uri.startswith('models:'):
        raise ValueError('Materialize immutable artifact locally before release/load')
    path = Path(uri)
    if not path.is_absolute():
        path = Path(root) / path
    actual = artifact_hashes(path, manifest.get('file_hashes'))
    if actual != manifest['file_hashes'] or fingerprint(actual) != manifest['model_sha256']:
        raise ValueError('Artifact bytes do not match manifest')
    runtime = manifest['runtime']
    if fingerprint(runtime) != manifest['runtime_sha256']:
        raise ValueError('Runtime checksum mismatch')
    if runtime.get('base_revision') != 'fixture-only':
        present = {str(p.relative_to(path)) for p in path.rglob('*') if p.is_file()}
        if present != set(actual):
            raise ValueError('Unpinned or missing artifact files')
        required = {'config.json', 'tokenizer.json', 'tokenizer_config.json', 'generation_config.json'}
        if not required <= actual.keys() or not any(n.endswith('.safetensors') for n in actual):
            raise ValueError('Incomplete model file list')
        if manifest.get('adapter_subdir') is not None:
            if manifest['adapter_subdir'] != 'adapter' or not {'adapter/adapter_config.json', 'adapter/adapter_model.safetensors'} <= actual.keys():
                raise ValueError('Incomplete adapter file list')
        tokenizer = {n: h for n, h in actual.items() if n.startswith('tokenizer') or n in ('vocab.json', 'merges.txt')}
        if fingerprint(tokenizer) != runtime['tokenizer_sha256']:
            raise ValueError('Tokenizer checksum mismatch')
        if digest((path / 'chat_template.jinja').read_bytes()) != runtime['template_sha256']:
            raise ValueError('Template checksum mismatch')
        if not re.fullmatch('[0-9a-f]{40}', runtime['base_revision']):
            raise ValueError('Unpinned base revision')
    return path
