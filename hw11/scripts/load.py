"""Open-loop workload. Runs inside each of four Kubernetes client Pods.

One absolute wall epoch synchronizes Pods; all request durations and scheduling
lag use a local monotonic clock. Raw records distinguish intent from attempts.
No retries; use another attempt ID if retries are added.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import random
import socket
import time

import httpx


def schedule(duration=120, rate=5, traffic='uniform', seed=11, chunks=12):
    randomizer = random.Random(seed)
    rows = []
    for i in range(int(duration * rate)):
        # Exact 80% over complete groups of 15; remaining clients get 1/15 each.
        owner = i % 4 if traffic == 'uniform' else ([0] * 12 + [1, 2, 3])[i % 15]
        prefix = randomizer.choice(['catalogue', 'support', 'returns', 'warranty'])
        rows.append({'index': i, 'offset': i / rate, 'client': owner,
                     'payload': {'prompt': prefix * 80 + f' question-{i}',
                                 'session_id': f'dialogue-{i % 16}', 'messages': [],
                                 'max_new_tokens': chunks, 'stream': True}})
    return rows


def client_ip():
    if os.getenv('POD_IP'):
        return os.environ['POD_IP']
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return 'unresolved-local-host'


def classify(status, done, started, timeout=False, error=None):
    if timeout:
        return 'timed_out'
    if status is not None and status >= 400:
        return 'rejected'
    if done and status == 200 and error is None:
        return 'completed'
    return 'aborted' if started or status == 200 else 'transport_error'


async def attempt(client, url, payload, request_id, planned_mono, planned_wall, timeout, inflight=None, on_first=None):
    sent = time.perf_counter()
    row = {'request_id': request_id, 'attempt': 1, 'planned_wall': planned_wall,
           'planned_mono': planned_mono, 'sent_mono': sent, 'sent_wall': time.time(),
           'send_lag_seconds': sent - planned_mono, 'first_mono': None, 'end_mono': None,
           'status': None, 'pod': None, 'version': None, 'backend_mode': None,
           'chunks': 0, 'done': False, 'error': None, 'client_ip': client_ip(),
           'queue_seconds': None, 'cache_hit': None, 'chunk_times': [],
           'inflight_at_send': inflight() if inflight else None}
    timed_out = False
    try:
        async with asyncio.timeout(timeout):
            async with client.stream('POST', url + '/generate', json=payload,
                                     headers={'X-Request-ID': request_id}) as response:
                row['status'] = response.status_code
                row['pod'] = response.headers.get('x-pod')
                row['version'] = response.headers.get('x-model-version')
                row['backend_mode'] = response.headers.get('x-backend-mode')
                if response.status_code >= 400:
                    row['error'] = (await response.aread()).decode()[:1024]
                else:
                    expected = 0
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        part = json.loads(line)
                        if part.get('request_id') != request_id:
                            raise ValueError('Request identity mismatch')
                        if part.get('done'):
                            if part['chunks'] != expected or expected != payload['max_new_tokens']:
                                raise ValueError('Incomplete terminal frame')
                            row['done'] = True
                        else:
                            if part.get('index') != expected:
                                raise ValueError('Chunk order mismatch')
                            expected += 1
                            received = time.perf_counter()
                            row['chunk_times'].append(received)
                            if row['first_mono'] is None:
                                row['first_mono'] = received
                                if on_first:
                                    on_first()
                            row['chunks'] += 1
                            row['queue_seconds'] = part.get('queue_seconds')
                            row['cache_hit'] = part.get('cache_hit')
    except (TimeoutError, httpx.TimeoutException):
        timed_out = True
        row['error'] = 'deadline_exceeded'
    except (httpx.HTTPError, ValueError, OSError) as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
    row['end_mono'] = time.perf_counter()
    row['end_wall'] = time.time()
    row['outcome'] = classify(row['status'], row['done'], row['chunks'] > 0, timed_out, row['error'])
    row['ttfc_seconds'] = None if row['first_mono'] is None else row['first_mono'] - sent
    row['total_seconds'] = row['end_mono'] - sent
    times = row['chunk_times']
    row['inter_chunk_seconds'] = [(b - a) for a, b in zip(times, times[1:])]
    return row


async def run(args):
    rows = schedule(args.duration, args.rate, args.traffic, args.seed, args.chunks)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    selected = [r for r in rows if r['client'] == args.client]
    epoch = args.start or time.time() + 1
    origin = time.perf_counter() + epoch - time.time()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix('.schedule.json').write_text(json.dumps({'sha256': digest, 'start_wall': epoch,
              'client': args.client, 'rows': selected, 'keepalive': args.keepalive,
              'duration': args.duration, 'rate': args.rate, 'traffic': args.traffic,
              'clock': 'perf_counter durations; wall clock only for cross-Pod epoch'}, indent=2))
    tasks = []
    # Default disables connection reuse so plain Service selects at connection setup.
    limits = httpx.Limits(max_connections=256, max_keepalive_connections=32 if args.keepalive else 0)
    with path.open('w', buffering=1) as output:
        async with httpx.AsyncClient(timeout=httpx.Timeout(args.timeout), limits=limits, trust_env=False) as client:
            async def launch(item):
                row = await attempt(client, args.url, item['payload'], f'{args.run}-{item["index"]}',
                                    origin + item['offset'], epoch + item['offset'], args.timeout,
                                    lambda: sum(not t.done() for t in tasks))
                row.update(run=args.run, client=args.client, schedule_sha256=digest, index=item['index'])
                output.write(json.dumps(row) + '\n')
            for item in selected:
                await asyncio.sleep(max(0, origin + item['offset'] - time.perf_counter()))
                if sum(not t.done() for t in tasks) >= 192:
                    # Keep the schedule open-loop; never silently wait for a slot.
                    output.write(json.dumps({'request_id': f'{args.run}-{item["index"]}', 'run': args.run,
                          'index': item['index'], 'client': args.client, 'outcome': 'generator_dropped',
                          'planned_wall': epoch + item['offset'], 'planned_mono': origin + item['offset'],
                          'sent_mono': None, 'schedule_sha256': digest}) + '\n')
                else:
                    tasks.append(asyncio.create_task(launch(item)))
            if tasks:
                await asyncio.gather(*tasks)


def parser():
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='http://hw11-plain:8000')
    p.add_argument('--client', type=int, choices=range(4), default=0)
    p.add_argument('--duration', type=float, default=120)
    p.add_argument('--rate', type=float, default=5)
    p.add_argument('--traffic', choices=['uniform', 'skew'], default='uniform')
    p.add_argument('--seed', type=int, default=11)
    p.add_argument('--chunks', type=int, default=12)
    p.add_argument('--start', type=float)
    p.add_argument('--timeout', type=float, default=60)
    p.add_argument('--keepalive', action='store_true')
    p.add_argument('--output', default='/tmp/requests.jsonl')
    p.add_argument('--run', default='manual')
    return p


if __name__ == '__main__':
    args = parser().parse_args()
    if args.duration <= 0 or args.rate <= 0 or not 1 <= args.chunks <= 64:
        raise SystemExit('Invalid load parameters')
    asyncio.run(run(args))
