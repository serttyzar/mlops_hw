"""Reuse existing model files/run; evaluate both versions with the exact API backend."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backends import TransformersBackend
from gate_bridge import HW67, build_release_report
from publication import write
from release import artifact_hashes, digest, fingerprint
from evaluation.contract import fingerprint as old_fingerprint, read_rows, validate_golden
from evaluation.solution import score


def read(path):
    return json.loads(Path(path).read_text())


def prepare():
    root = ROOT / '.real'
    root.mkdir(exist_ok=True)
    base_spec = read(HW67 / 'evaluation/baseline.json')
    candidate_spec = read(HW67 / 'metrics/candidate-model.json')
    snapshot = ROOT / '.models/hub/models--Qwen--Qwen3-0.6B/snapshots' / base_spec['revision']
    training = ROOT / '.models/training/model/artifacts/adapter'
    training_manifest = read(training / 'manifest.json')
    if training_manifest['model']['revision'] != base_spec['revision']:
        raise ValueError('Different training base revision')
    config = read(HW67 / 'evaluation/params.json')
    metrics = ROOT / 'metrics'
    metrics.mkdir(exist_ok=True)
    write(metrics / 'training-model-manifest.json', training_manifest)
    manifests = {}
    for role in ('baseline', 'candidate'):
        artifact = ROOT / '.models' / role
        artifact.mkdir(exist_ok=True)
        for source in snapshot.iterdir():
            dest = artifact / source.name
            if source.is_file() and not dest.exists():
                dest.symlink_to(source.resolve())
        template = read(snapshot / 'tokenizer_config.json')['chat_template']
        (artifact / 'chat_template.jinja').write_text(template)
        if role == 'candidate':
            dest = artifact / 'adapter'
            dest.mkdir(exist_ok=True)
            for name in ('adapter_config.json', 'adapter_model.safetensors'):
                if not (dest / name).exists():
                    (dest / name).symlink_to((training / name).resolve())
        names = [str(p.relative_to(artifact)) for p in artifact.rglob('*') if p.is_file()]
        files = artifact_hashes(artifact, names)
        import torch
        device = 'mps' if torch.backends.mps.is_available() else 'cpu'
        runtime = dict(base_revision=base_spec['revision'], base_name=base_spec['name'],
                       tokenizer_sha256=fingerprint({n: h for n, h in files.items() if n.startswith('tokenizer') or n in ('vocab.json','merges.txt')}),
                       template_sha256=digest(template.encode()),
                       generation={**{k: config[k] for k in ('system', 'max_new_tokens', 'enable_thinking', 'seed')}, 'do_sample': False},
                       dtype='bfloat16' if device == 'mps' else 'float32', device=device,
                       max_input_tokens=2048, backend_sha256=digest((ROOT / 'backends.py').read_bytes()))
        spec = base_spec if role == 'baseline' else candidate_spec
        model_id = f"{base_spec['name']}@{base_spec['revision']}" if role == 'baseline' else f"{spec['uri']}#run={spec['run_id']}"
        manifests[role] = dict(schema_version=1, model_id=model_id,
                               model_spec=spec, artifact_uri=os.path.relpath(artifact, root),
                               model_sha256=fingerprint(files), file_hashes=files,
                               runtime=runtime, runtime_sha256=fingerprint(runtime),
                               training_run_id=candidate_spec['run_id'])
        if role == 'candidate':
            manifests[role]['adapter_subdir'] = 'adapter'
    if manifests['baseline']['runtime'] != manifests['candidate']['runtime']:
        raise ValueError('Baseline/candidate runtime differs')
    rows, behavioral, training_rows = [read_rows(HW67 / config[k]) for k in ('golden', 'behavioral', 'training_index')]
    proof = validate_golden(rows, training_rows, behavioral)
    protocol = dict(runtime=runtime, baseline_spec=base_spec,
                    critical_slices=config['critical_slices'],
                    scoring_code={n: digest((HW67 / 'evaluation' / n).read_bytes()) for n in ('solution.py','contract.py')},
                    datasets={k: old_fingerprint(v) for k,v in [('golden',rows),('behavioral',behavioral),('training_index',training_rows)]})
    write(metrics / 'protocol.json', protocol)
    for m in manifests.values():
        m.update(protocol_sha256=fingerprint(protocol), golden_sha256=old_fingerprint(rows))
    shutil.copyfile(HW67 / config['golden'], root / 'input.txt')
    write(root / 'config.json', {'format': 'jsonl', 'tokenizer_revision': runtime['tokenizer_sha256'],
                               'template_sha256': runtime['template_sha256'], 'max_length': runtime['max_input_tokens'],
                               'code_revision': digest((ROOT / 'lab.py').read_bytes())})
    return root, metrics, manifests, protocol, rows, behavioral, proof


def evaluate():
    root, metrics, manifests, protocol, rows, behavioral, proof = prepare()
    import mlflow
    mlflow.set_tracking_uri('http://127.0.0.1:5006')
    mlflow.set_experiment('hw8910-release-evaluation')
    with mlflow.start_run(run_name='same-api-backend-baseline-vs-candidate') as run:
        for role, m in manifests.items():
            started = time.perf_counter()
            backend = TransformersBackend(m, (root / m['artifact_uri']).resolve())
            outputs = []
            for i, row in enumerate(rows + behavioral):
                now = time.perf_counter()
                try:
                    answer = backend.generate(row['input'], m['runtime']['generation']['max_new_tokens'])
                    item = dict(id=row['id'], raw=answer['answer'], error=None, output_tokens=answer['output_tokens'])
                except Exception as exc:
                    item = dict(id=row['id'], raw='', error=f'{type(exc).__name__}: {exc}', output_tokens=0)
                item['latency_seconds'] = time.perf_counter() - now
                outputs.append(item)
                print(role, i + 1, row['id'], json.dumps(item, ensure_ascii=False), flush=True)
            by_id = {o['id']: o for o in outputs}
            report = score(rows, [by_id[r['id']] for r in rows])
            report.update(status='complete', model=m['model_spec'], model_sha256=m['model_sha256'],
                          protocol_sha256=m['protocol_sha256'], golden_sha256=m['golden_sha256'],
                          baseline_spec_sha256=old_fingerprint(protocol['baseline_spec']),
                          behavioral=score(behavioral, [by_id[r['id']] for r in behavioral]),
                          leakage=proof, outputs_sha256=old_fingerprint(outputs),
                          runtime_sha256=m['runtime_sha256'], evaluation_run_id=run.info.run_id, synthetic=False)
            write(metrics / f'{role}-outputs.json', outputs)
            write(metrics / f'{role}-eval.json', report)
            mlflow.log_metrics({f'{role}.{k}': report['overall'][k] for k in ('accuracy','format_rate')})
            print(role, report['overall'], 'seconds',time.perf_counter()-started, flush=True)
            del backend
            import gc,torch
            gc.collect()
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        paths = {role: metrics / f'{role}-eval.json' for role in ('baseline','candidate')}
        paths.update({role + '_raw': metrics / f'{role}-outputs.json' for role in ('baseline','candidate')})
        config = read(HW67 / 'evaluation/params.json')
        paths.update({k: HW67 / config[k] for k in ('golden','behavioral','training_index')})
        paths['protocol'] = metrics / 'protocol.json'
        evidence = {k: {'path': os.path.relpath(p, root), 'sha256': digest(p.read_bytes())} for k,p in paths.items()}
        for role, m in manifests.items():
            selection = 'baseline-retention' if role == 'baseline' else 'candidate'
            report = build_release_report(m, evidence, root, selection)
            path = metrics / f'release-report-{role}.json'
            write(path, report)
            m['release_report_uri'] = os.path.relpath(path, root)
            m['release_report_sha256'] = digest(path.read_bytes())
            write(metrics / f'manifest-{role}.json', m)
            write(root / f'manifest-{role}.json', m)
            write(root / f'report-{role}.json', report)
            print('RELEASE', role, 'passed', report['passed'], 'reason', report['reason'], flush=True)
        for path in metrics.glob('*.json'):
            mlflow.log_artifact(str(path), 'release-evaluation')
        mlflow.set_tags({'training_run_id': manifests['candidate']['training_run_id'],
                         'runtime_sha256': manifests['baseline']['runtime_sha256'],
                         'protocol_sha256': manifests['baseline']['protocol_sha256']})
        write(metrics / 'run-links.json', {'training_run_id': manifests['candidate']['training_run_id'],
                                         'evaluation_run_id': run.info.run_id,
                                         'tracking_uri': mlflow.get_tracking_uri()})


if __name__ == '__main__':
    evaluate()
