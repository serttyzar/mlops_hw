"""Actual local HTTP smoke + 8 identical prompts at concurrency 1 and 2; all attempts kept."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from publication import read, write
from release import canonical


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', type=Path, default=ROOT / '.real')
    p.add_argument('--n', type=int, default=8)
    p.add_argument('--smoke-only', action='store_true')
    args = p.parse_args()
    workspace = args.workspace.resolve()
    manifest = read(workspace / 'accepted.json')
    metrics = ROOT / 'metrics'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'

    def http(path, body=None):
        request = Request(url + path, data=None if body is None else canonical(body),
                          headers={'Content-Type': 'application/json'})
        try:
            response = urlopen(request, timeout=180)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    with (ROOT / ('docs/logs/real-api-restart.log' if args.smoke_only else 'docs/logs/real-api-server.log')).open('w') as log:
        process = subprocess.Popen([sys.executable, str(ROOT / 'api.py'), '--workspace', str(workspace),
                                    '--backend', 'transformers', '--port', str(port), '--debug'],
                                   stdout=log, stderr=log)
        try:
            trace = []
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                try:
                    health, healthy = http('/health')
                    status, ready = http('/ready')
                    trace.append({'health': health, 'ready': status})
                    if status == 200:
                        break
                except (URLError, ConnectionError):
                    pass
                if process.poll() is not None:
                    raise RuntimeError('Real API exited; see real-api-server.log')
                time.sleep(.1)
            else:
                raise RuntimeError('Real API not ready; see server log')
            rows = [json.loads(s) for s in (ROOT.parent / 'hw67/data/eval/golden.jsonl').read_text().splitlines()][:args.n]
            limit = manifest['runtime']['generation']['max_new_tokens']
            before = http('/debug')[1]['calls']
            invalid = []
            for body in ({'prompt': ''}, {'prompt': 'test', 'max_new_tokens': 0},
                         {'prompt': 'test', 'max_new_tokens': 100000},
                         {'prompt': 'test', 'max_new_tokens': '32'},
                         {'prompt': 'word ' * 3000, 'max_new_tokens': limit}):
                status, reply = http('/generate', body)
                invalid.append({'status': status, 'reply': reply})
                if not 400 <= status < 500:
                    raise RuntimeError('Invalid real request was accepted')
            after = http('/debug')[1]['calls']
            if before != after:
                raise RuntimeError('Invalid requests called backend')
            status, smoke = http('/generate', {'prompt': rows[0]['input'], 'max_new_tokens': limit})
            if status != 200 or smoke['model_id'] != manifest['model_id'] or smoke['runtime_sha256'] != manifest['runtime_sha256']:
                raise RuntimeError('Real smoke failed')
            write(metrics / ('api-smoke-restart.json' if args.smoke_only else 'api-smoke.json'), {'http_status': status, 'prompt': rows[0]['input'], 'response': smoke,
                                             'readiness_trace': trace, 'invalid_requests': invalid,
                                             'calls_before_invalid': before, 'calls_after_invalid': after})
            attempts, summary = [], []
            for concurrency in (() if args.smoke_only else (1, 2)):
                def one(row):
                    started = time.perf_counter()
                    try:
                        status, body = http('/generate', {'prompt': row['input'], 'max_new_tokens': limit})
                        result = {'id': row['id'], 'concurrency': concurrency, 'status': status,
                                  'error': None if status == 200 else body.get('error'), **body}
                    except Exception as exc:
                        result = {'id': row['id'], 'concurrency': concurrency, 'status': None,
                                  'error': f'{type(exc).__name__}: {exc}'}
                    result['client_seconds'] = time.perf_counter() - started
                    return result
                start = time.perf_counter()
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    batch = list(pool.map(one, rows))
                duration = time.perf_counter() - start
                attempts.extend(batch)
                good = [r for r in batch if r['status'] == 200 and r['error'] is None]
                errors = [r for r in batch if r not in good]
                def average(items, key):
                    return sum(r[key] for r in items) / len(items) if items else None
                summary.append(dict(concurrency=concurrency, n=len(batch), successes=len(good), errors=len(errors),
                                    batch_seconds=duration, successful_req_per_second=len(good)/duration,
                                    mean_client_seconds=average(good,'client_seconds'),
                                    max_client_seconds=max((r['client_seconds'] for r in good),default=None),
                                    mean_generation_seconds=average(good,'generation_seconds'),
                                    mean_queue_seconds=average(good,'queue_seconds'),
                                    mean_error_seconds=average(errors,'client_seconds')))
                print(json.dumps(summary[-1],ensure_ascii=False), flush=True)
            if args.smoke_only:
                print('RESTART_SMOKE', json.dumps(smoke,ensure_ascii=False),flush=True)
                return
            (metrics / 'load-attempts.jsonl').write_bytes(b''.join(canonical(r)+b'\n' for r in attempts))
            write(metrics / 'load-summary.json', summary)
            import csv
            with (metrics / 'load-summary.csv').open('w') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
                writer.writeheader()
                writer.writerows(summary)
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()
