"""Verify full raw gate and measurement arithmetic offline, without model/Prefect/FastAPI."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from gate_bridge import build_release_report, verify_release_report
from publication import read
from release import digest, validate_release


def main():
    workspace=ROOT/'.real'
    for role in ('baseline','candidate'):
        manifest=read(ROOT/f'metrics/manifest-{role}.json')
        report=read(ROOT/f'metrics/release-report-{role}.json')
        actual=build_release_report(manifest,report['evidence'],workspace,report['selection'])
        if actual!=report:
            raise ValueError('Stored report differs from recalculated full evaluation')
        if role=='baseline':
            validate_release(manifest,report)
            verify_release_report(manifest,report,workspace)
        else:
            try:
                verify_release_report(manifest,report,workspace)
            except ValueError as exc:
                if not str(exc).startswith('Quality gate rejected:'):
                    raise
            else:
                raise ValueError('Candidate unexpectedly allowed')
    smoke=read(ROOT/'metrics/api-smoke.json')
    restart=read(ROOT/'metrics/api-smoke-restart.json')
    manifest=read(ROOT/'metrics/manifest-baseline.json')
    for result in (smoke,restart):
        if result['http_status']!=200 or result['calls_before_invalid']!=result['calls_after_invalid']:
            raise ValueError('API smoke/validation failed')
        if any(not 400<=r['status']<500 for r in result['invalid_requests']):
            raise ValueError('Invalid request succeeded')
        for key in ('model_id','runtime_sha256'):
            if result['response'][key]!=manifest[key]:
                raise ValueError('API loaded another model/runtime')
        if result['response']['output_tokens']<=0 or 'STUB ONLY' in result['response']['answer']:
            raise ValueError('Missing real model response')
    attempts=[json.loads(s) for s in (ROOT/'metrics/load-attempts.jsonl').read_text().splitlines()]
    summary=read(ROOT/'metrics/load-summary.json')
    for row in summary:
        batch=[r for r in attempts if r['concurrency']==row['concurrency']]
        good=[r for r in batch if r['status']==200 and r['error'] is None]
        if row['n']!=8 or len(batch)!=row['n'] or len(good)!=row['successes'] or len(batch)-len(good)!=row['errors']:
            raise ValueError('Load attempt counts inconsistent')
        if row['successful_req_per_second']!=len(good)/row['batch_seconds']:
            raise ValueError('Inconsistent throughput')
        if row['mean_client_seconds']!=sum(r['client_seconds'] for r in good)/len(good):
            raise ValueError('Inconsistent latency')
        if any(r['model_id']!=manifest['model_id'] or r['runtime_sha256']!=manifest['runtime_sha256'] for r in good):
            raise ValueError('Mixed models during load')
    if [r['id'] for r in attempts if r['concurrency']==1]!=[r['id'] for r in attempts if r['concurrency']==2]:
        raise ValueError('Different request sets')
    publication=read(ROOT/'metrics/real-publication.json')
    rejected=publication[-1]
    if rejected['exit_code']!=1 or rejected['before_sha256']!=rejected['after_sha256']:
        raise ValueError('Candidate rejection changed accepted pointer')
    flow=read(ROOT/'metrics/flow-experiments.json')
    if len(flow['recovered_events'])!=1:
        raise ValueError('Duplicate release')
    if any(r['exit_code']!=1 for r in read(ROOT/'metrics/before-tests.json')):
        raise ValueError('Starter tests were not red')
    print('PASS: raw outputs, full gate, runtime binding, API restart, invalid requests, 8+8 load, publication and recovery')


if __name__=='__main__':
    main()
