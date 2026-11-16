"""Real Kubernetes experiment runner. Never synthesizes cluster evidence.

Use a dedicated kind cluster and namespace; every kubectl call specifies both.
The host only orchestrates. Requests originate in the four client Pods.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
os.environ['PATH'] = str(ROOT / '.tools') + os.pathsep + os.environ['PATH']
os.environ['KUBECONFIG'] = str(ROOT / '.state/kubeconfig')
CTX = 'kind-mlops-hw11'
NS = 'hw11'
RELEASE = 'hw11'
IMAGE = 'hw11-api:0.1.0'


class Lab:
    def __init__(self, output):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.phase = 'setup'
        self.collector = None
        self.forward = None
        self.log_followers = {}

    def event(self, event, **values):
        with self.lock:
            with (self.output / 'events.jsonl').open('a') as f:
                f.write(json.dumps({'wall': time.time(), 'event': event, 'phase': self.phase, **values}) + '\n')

    def command(self, args, check=True, timeout=300):
        started = time.time()
        result = subprocess.run([str(a) for a in args], text=True, capture_output=True, timeout=timeout, cwd=ROOT)
        self.event('command', command=[str(a) for a in args], started=started,
                   returncode=result.returncode, stdout=result.stdout[-16000:], stderr=result.stderr[-4000:])
        if check and result.returncode:
            raise RuntimeError(f'Command failed ({result.returncode}): {args}\n{result.stderr[-2000:]}')
        return result

    def kube(self, *args, namespace=NS, **kw):
        return self.command(['kubectl', '--context', CTX, '-n', namespace, *args], **kw)

    def configure(self, wait=True, **values):
        args = ['helm', 'upgrade', '--install', RELEASE, str(ROOT / 'charts/hw11'),
                '--kube-context', CTX, '--namespace', NS, '--create-namespace', '--reuse-values', '--timeout', '240s']
        for k, v in values.items():
            args += ['--set', k.replace('__', '.') + '=' + (str(v).lower() if isinstance(v, bool) else str(v))]
        if wait:
            args += ['--wait']
        self.command(args)

    def baseline(self, **values):
        config = dict(replicas=2, modelVersion='A', warmupSeconds=20, brokenProbes=False, concurrency=1,
                      queueCapacity=24, queueTimeout=12, cacheSize=16, hitCpu=.025,
                      missCpu=.2, chunkCpu=.008, chunkInterval=.02, hpa__enabled=False,
                      faultsEnabled=False)
        config.update(values)
        self.configure(**config)
        self.wait_ready()

    def wait_ready(self):
        self.kube('rollout', 'status', 'deployment/hw11-api', '--timeout=240s')

    def pods(self):
        data = json.loads(self.kube('get', 'pods', '-l', 'app=hw11-api', '-o', 'json').stdout)
        return [p for p in data['items'] if not p['metadata'].get('deletionTimestamp')]

    def exec(self, client, *args, **kw):
        return self.kube('exec', f'hw11-client-{client}', '--', *args, **kw)

    def get(self, client=0, path='/info', url='http://hw11-plain:8000'):
        code = 'import urllib.request; print(urllib.request.urlopen(' + repr(url + path) + ',timeout=5).read().decode())'
        return json.loads(self.exec(client, 'python', '-c', code).stdout)

    def post(self, payload, client=0, url='http://hw11-plain:8000'):
        code = ('import json,urllib.request; r=urllib.request.Request(' + repr(url + '/generate') +
                ',data=' + repr(json.dumps(payload).encode()) +
                ',headers={"Content-Type":"application/json"}); print(urllib.request.urlopen(r,timeout=65).read().decode())')
        return json.loads(self.exec(client, 'python', '-c', code, timeout=75).stdout)

    def start_collecting(self):
        self.forward = subprocess.Popen(['kubectl', '--context', CTX, '-n', NS, 'port-forward',
                   'service/hw11-prometheus', '19090:9090'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
        self.collector = threading.Thread(target=self.collect, daemon=True)
        self.collector.start()

    def collect(self):
        while not self.stop.is_set():
            try:
                snap = self.kube('get', 'pods,deployments,hpa,endpointslices', '-o', 'json', check=False, timeout=15)
                if snap.returncode == 0:
                    snapshot = json.loads(snap.stdout)
                    self.event('snapshot', data=snapshot)
                    for pod in snapshot['items']:
                        if pod.get('kind') != 'Pod' or pod['metadata'].get('labels',{}).get('app') != 'hw11-api':
                            continue
                        containers = pod.get('status',{}).get('containerStatuses',[])
                        if not containers or 'running' not in containers[0].get('state',{}):
                            continue
                        name = pod['metadata']['name']
                        restart = containers[0].get('restartCount',0)
                        key = (name,restart)
                        if key not in self.log_followers:
                            file = (self.output/f'pod-{name}-restart{restart}.log').open('w')
                            proc = subprocess.Popen(['kubectl','--context',CTX,'-n',NS,'logs',name,
                                 '--follow','--timestamps'],stdout=file,stderr=subprocess.STDOUT,cwd=ROOT)
                            self.log_followers[key] = proc,file
                            if restart:
                                previous = self.kube('logs',name,'--previous','--timestamps',check=False).stdout
                                (self.output/f'pod-{name}-previous{restart}.log').write_text(previous)
                prom = {}
                for name, query in [('alerts', 'ALERTS'), ('running', 'hw11_running'), ('waiting', 'hw11_waiting'),
                                    ('cpu', 'sum by(pod)(rate(container_cpu_usage_seconds_total{namespace="hw11",container="api"}[30s]))'),
                                    ('ttfc', 'histogram_quantile(0.95,sum by(le,pod)(rate(hw11_ttfc_seconds_bucket[1m])))')]:
                    import urllib.parse
                    url = 'http://127.0.0.1:19090/api/v1/query?' + urllib.parse.urlencode({'query': query})
                    try:
                        with urllib.request.urlopen(url, timeout=2) as response:
                            prom[name] = json.load(response)
                    except OSError as exc:
                        prom[name] = {'error': str(exc)}
                self.event('prometheus', data=prom)
            except Exception as exc:
                self.event('collector_error', error=str(exc))
            self.stop.wait(2)

    def start_load(self, name, duration=120, rate=5, traffic='uniform', policy='plain', seed=11, chunks=12):
        if name.startswith('B-'):
            self.metrics_snapshot(name, 'before')
        start = time.time() + 5
        group = {'name': name, 'processes': [], 'start': start, 'duration': duration,
                 'rate': rate, 'traffic': traffic, 'policy': policy, 'seed': seed, 'chunks': chunks}
        for client in range(4):
            cmd = ['kubectl', '--context', CTX, '-n', NS, 'exec', f'hw11-client-{client}', '--',
                   'python', 'scripts/load.py', '--url', f'http://hw11-{policy}:8000', '--client', str(client),
                   '--duration', str(duration), '--rate', str(rate), '--traffic', traffic, '--seed', str(seed),
                   '--start', str(start), '--chunks', str(chunks), '--output', f'/tmp/{name}.jsonl', '--run', name]
            logfile = (self.output / f'{name}-client{client}.log').open('w')
            proc = subprocess.Popen(cmd, stdout=logfile, stderr=subprocess.STDOUT, cwd=ROOT)
            group['processes'].append((client, proc, logfile))
        self.event('load_start', **{k:v for k,v in group.items() if k != 'processes'})
        return group

    def finish_load(self, group):
        name = group['name']
        rows = []
        for client, process, logfile in group['processes']:
            try:
                result = process.wait(timeout=group['duration'] + 100)
            finally:
                logfile.close()
            if result:
                raise RuntimeError(f'Load generator {name}/{client} exited {result}')
            raw = self.exec(client, 'cat', f'/tmp/{name}.jsonl').stdout
            (self.output / f'{name}-client{client}.jsonl').write_text(raw)
            schedule = self.exec(client, 'cat', f'/tmp/{name}.schedule.json').stdout
            (self.output / f'{name}-client{client}.schedule.json').write_text(schedule)
            rows.extend(json.loads(line) for line in raw.splitlines() if line)
        with (self.output / 'requests.jsonl').open('a') as f:
            for row in sorted(rows, key=lambda r:r['index']):
                f.write(json.dumps(row) + '\n')
        if name.startswith('B-'):
            time.sleep(3)
            self.metrics_snapshot(name, 'after')
        self.event('load_end', name=name, observations=len(rows))
        return rows

    def load(self, name, **kwargs):
        return self.finish_load(self.start_load(name, **kwargs))

    def logs(self, label):
        # Poll while experiments run as well as here; deleted Pods are preserved
        # by follow-logs subprocesses started in collect_logs below.
        for p in self.pods():
            name = p['metadata']['name']
            text = self.kube('logs', name, '--timestamps', check=False).stdout
            (self.output / f'{label}-{name}.log').write_text(text)

    def metrics_snapshot(self, name, moment):
        import urllib.parse
        cohort = sorted(p['metadata']['name'] for p in self.pods())
        data = {'wall':time.time(),'queries':{},'cohort':cohort}
        selector = '|'.join(cohort)
        for metric in ['hw11_ttfc_seconds_bucket','hw11_ttfc_seconds_count',
                       'hw11_response_seconds_bucket','hw11_response_seconds_count','hw11_requests_total']:
            url = 'http://127.0.0.1:19090/api/v1/query?' + urllib.parse.urlencode({'query':metric+'{pod=~"'+selector+'"}'})
            with urllib.request.urlopen(url,timeout=5) as response:
                result = json.load(response)
            if result.get('status') != 'success' or not result['data']['result']:
                raise AssertionError('Prometheus has no per-Pod samples for '+metric)
            data['queries'][metric] = result
        (self.output/f'{name}-metrics-{moment}.json').write_text(json.dumps(data,indent=2))

    def start_stream(self, name, pod):
        ip = pod['status']['podIP']
        self.exec(0, 'rm', '-f', f'/tmp/{name}.jsonl.started')
        cmd = ['kubectl', '--context', CTX, '-n', NS, 'exec', 'hw11-client-0', '--',
               'python', 'scripts/one.py', '--url', f'http://{ip}:8000', '--output', f'/tmp/{name}.jsonl', '--id', name]
        log = (self.output / f'{name}.log').open('w')
        process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        self.until(lambda: self.exec(0, 'test', '-f', f'/tmp/{name}.jsonl.started', check=False).returncode == 0,
                   20, 'long stream did not start')
        self.event('long_stream_started', name=name, pod=pod['metadata']['name'])
        return name, process, log

    def finish_stream(self, stream):
        name, process, log = stream
        try:
            if process.wait(timeout=75):
                raise RuntimeError('Long-stream client failed')
        finally:
            log.close()
        raw = self.exec(0, 'cat', f'/tmp/{name}.jsonl').stdout
        (self.output / f'{name}.jsonl').write_text(raw)
        row = json.loads(raw)
        row.update(run=name, index=0, client=0)
        with (self.output / 'requests.jsonl').open('a') as f:
            f.write(json.dumps(row) + '\n')
        return row

    def until(self, condition, seconds, reason):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(2)
        raise AssertionError(reason)

    def check(self, name, condition, **detail):
        result = {'name': name, 'passed': bool(condition), 'wall': time.time(), **detail}
        with (self.output / 'checks.jsonl').open('a') as f:
            f.write(json.dumps(result) + '\n')
        if not condition:
            raise AssertionError(name + ': ' + json.dumps(detail))

    def close(self):
        self.stop.set()
        if self.collector:
            self.collector.join(timeout=20)
        for process, file in self.log_followers.values():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            file.close()
        if self.forward:
            self.forward.terminate()
            self.forward.wait(timeout=10)


def install_metrics_server(lab):
    lab.kube('apply', '-f', 'https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml', namespace='kube-system')
    # Local kind self-signed kubelet certificates only; not a production setting.
    lab.command(['kubectl', '--context', CTX, '-n', 'kube-system', 'patch', 'deployment', 'metrics-server', '--type=json',
                 '-p', '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'])
    lab.command(['kubectl', '--context', CTX, '-n', 'kube-system', 'rollout', 'status', 'deployment/metrics-server', '--timeout=180s'])


def setup(lab):
    sys.path.insert(0, str(ROOT / 'scripts'))
    from preflight import inspect
    environment = inspect(lab.output / 'environment-before.json')
    if not environment['cluster_available']:
        raise RuntimeError('Preflight failed. See environment-before.json. No cluster results were produced.')
    if environment['disk']['free'] < 8 * 2**30:
        raise RuntimeError('Need at least 8 GiB free before creating the stand')
    docker = json.loads(environment['probes'][1]['stdout'])
    if docker['cpu'] < 4 or docker['memory'] < 5.5 * 2**30:
        raise RuntimeError('Container environment needs >=4 CPUs and >=5.5 GiB usable RAM (6 GiB VM; see README)')
    (ROOT / '.state').mkdir(exist_ok=True)
    clusters = lab.command(['kind', 'get', 'clusters']).stdout.split()
    if 'mlops-hw11' not in clusters:
        lab.command(['kind', 'create', 'cluster', '--name', 'mlops-hw11', '--image', 'kindest/node:v1.32.2',
                     '--config', ROOT / 'kind.yaml', '--kubeconfig', ROOT / '.state/kubeconfig'], timeout=360)
    else:
        lab.command(['kind', 'export', 'kubeconfig', '--name', 'mlops-hw11', '--kubeconfig', ROOT / '.state/kubeconfig'])
    lab.command(['docker', 'build', '-f', ROOT / 'Dockerfile', '-t', IMAGE, str(ROOT.parent)], timeout=600)
    lab.command(['kind', 'load', 'docker-image', IMAGE, '--name', 'mlops-hw11'], timeout=300)
    lab.command(['helm', 'lint', ROOT / 'charts/hw11'])
    rendered = lab.command(['helm', 'template', RELEASE, ROOT / 'charts/hw11', '--namespace', NS]).stdout
    (lab.output / 'rendered.yaml').write_text(rendered)
    install_metrics_server(lab)
    lab.configure()
    for client in range(4):
        lab.kube('wait', '--for=condition=Ready', f'pod/hw11-client-{client}', '--timeout=180s')
    lab.wait_ready()
    lab.kube('get', '--raw', '/apis/metrics.k8s.io/v1beta1/nodes')
    ips = json.loads(lab.kube('get', 'pod', '-l', 'app=hw11-client', '-o', 'json').stdout)
    lab.check('four_distinct_client_ips', len({p['status']['podIP'] for p in ips['items']}) == 4)
    for args, file in [(('get', 'nodes', '-o', 'json'), 'nodes.json'),
                       (('get', 'services', '-o', 'yaml'), 'services.yaml')]:
        (lab.output / file).write_text(lab.kube(*args).stdout)
    net = lab.command(['kubectl', '--context', CTX, '-n', 'kube-system', 'get', 'configmap', 'kube-proxy', '-o', 'yaml']).stdout
    (lab.output / 'kube-proxy.yaml').write_text(net)
    (lab.output / 'image.json').write_text(lab.command(['docker', 'image', 'inspect', IMAGE,
                                    '--format', '{{json .}}']).stdout)


def experiment_a(lab):
    for broken in (True, False):
        lab.phase = 'A-warmup-' + ('before' if broken else 'after')
        # Identical fresh-start workload with both old replicas removed first.
        lab.kube('scale', 'deployment/hw11-api', '--replicas=0')
        lab.until(lambda: not lab.pods(), 90, 'old replicas did not terminate')
        lab.configure(wait=False, brokenProbes=broken, warmupSeconds=20, modelVersion='A', concurrency=1)
        lab.kube('scale', 'deployment/hw11-api', '--replicas=2')
        rows = lab.load(lab.phase, duration=40, rate=2, chunks=2)
        lab.wait_ready()
        lab.logs(lab.phase)
        unready = sum('backend_unready' in (r.get('error') or '') for r in rows)
        lab.check('warmup_defect_reproduced' if broken else 'warmup_fixed_no_unready_requests',
                  unready > 0 if broken else unready == 0, unready_responses=unready)

    lab.phase = 'A-rollout'
    lab.baseline(concurrency=2, chunkInterval=.5, chunkCpu=.001, faultsEnabled=True)
    old = lab.pods()[0]
    lab.kube('annotate', 'pod', old['metadata']['name'], 'controller.kubernetes.io/pod-deletion-cost=-1000', '--overwrite')
    stream = lab.start_stream('A-long-rollout', old)
    group = lab.start_load('A-rollout', duration=75, rate=1, chunks=2)
    lab.event('rollout_begin', version='B')
    lab.configure(modelVersion='B')
    lab.wait_ready()
    long_row = lab.finish_stream(stream)
    rows = lab.finish_load(group)
    lab.check('graceful_rollout_zero_5xx_and_incomplete',
              all(r['outcome'] == 'completed' for r in rows + [long_row]),
              outcomes={o:sum(r['outcome']==o for r in rows+[long_row]) for o in ['completed','rejected','aborted','timed_out','transport_error']})
    lab.check('version_B_actually_loaded', lab.post({'prompt':'which version'})['version'] == 'B')

    lab.phase = 'A-bad-release-rollback'
    lab.configure(wait=False, modelVersion='BAD')
    group = lab.start_load('A-bad-release', duration=40, rate=1, chunks=2)
    time.sleep(27)
    bad = [p for p in lab.pods() if p['status'].get('phase') == 'Running' and
           next((x for x in p['spec']['containers'][0]['envFrom'] if 'configMapRef' in x), None)]
    # Running pods include the old B and the unready BAD; per-Pod /info identifies them.
    observed_bad = []
    for p in bad:
        info = lab.get(url='http://' + p['status']['podIP'] + ':8000')
        if info['version'] == 'BAD':
            observed_bad.append(p['metadata']['name'])
            lab.check('bad_pod_not_ready', not info['warm'])
    lab.check('bad_release_created', bool(observed_bad))
    rows = lab.finish_load(group)
    lab.check('bad_release_excluded_from_answers', all(r.get('version') != 'BAD' for r in rows))
    lab.logs('A-bad')
    # A was the previous good configuration before B; explicitly restore A,
    # instead of rolling back to the immediately preceding B accidentally.
    history = json.loads(lab.command(['helm', 'history', RELEASE, '--kube-context', CTX, '-n', NS, '-o', 'json']).stdout)
    revision = None
    for item in reversed(history):
        raw = lab.command(['helm', 'get', 'values', RELEASE, '--kube-context', CTX, '-n', NS,
                           '--revision', str(item['revision']), '-o', 'json']).stdout
        value = json.loads(raw)
        if value.get('modelVersion') == 'A' and value.get('brokenProbes') is False:
            revision = item['revision']
            break
    if revision is None:
        raise AssertionError('No known-good A revision')
    lab.command(['helm', 'rollback', RELEASE, str(revision), '--kube-context', CTX, '-n', NS, '--wait', '--timeout', '240s'])
    rows = lab.load('A-after-rollback', duration=10, rate=2, chunks=2)
    lab.check('loaded_A_after_rollback', bool(rows) and all(r['outcome']=='completed' and r['version']=='A' for r in rows))

    lab.phase = 'A-forced-process-exit'
    target = lab.pods()[0]
    stream = lab.start_stream('A-long-killed', target)
    group = lab.start_load('A-forced-process-exit', duration=55, rate=1, chunks=2)
    pod_name = target['metadata']['name']
    lab.event('force_exit', pod=pod_name)
    lab.kube('exec', pod_name, '--', 'python', '-c',
             "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/fault/exit',data=b'',method='POST')).read()")
    killed = lab.finish_stream(stream)
    rows = lab.finish_load(group)
    lab.check('forced_exit_recorded_incomplete_stream', killed['outcome']=='aborted', observed=killed['outcome'])
    lab.wait_ready()
    lab.check('service_recovered_after_forced_exit', lab.post({'prompt':'recovered'})['version']=='A')

    lab.phase = 'A-replace-pod-dialogue'
    payload = {'prompt': 'continue', 'messages': [{'role':'user','content':'PERSIST-AFTER-REPLACEMENT'}]}
    before = lab.post(payload, url='http://' + lab.pods()[0]['status']['podIP'] + ':8000')
    lab.kube('delete', 'pod', before['pod'], '--wait=true')
    lab.wait_ready()
    after = lab.post(payload)
    lab.check('replacement_restores_version_and_dialogue',
              after['version']=='A' and after['artifact_sha256']==before['artifact_sha256'] and
              'PERSIST-AFTER-REPLACEMENT' in after['answer'])
    a = lab.post({'prompt':'continue', 'messages':[{'role':'user','content':'PRIVATE-ALPHA'}]})
    b = lab.post({'prompt':'continue', 'messages':[{'role':'user','content':'PRIVATE-BETA'}]}, client=1)
    lab.check('two_dialogues_isolated', 'PRIVATE-ALPHA' in a['answer'] and 'PRIVATE-BETA' not in a['answer'] and
              'PRIVATE-BETA' in b['answer'] and 'PRIVATE-ALPHA' not in b['answer'])


def experiment_b(lab):
    lab.baseline()
    for traffic in ['uniform', 'skew']:
        for repeat in range(1,4):
            # Alternate policy order to reduce drift. Exactly the same seed,
            # prompts, lengths, response limits and offsets within each pair.
            policies = ['plain','affinity'] if repeat % 2 else ['affinity','plain']
            for policy in policies:
                name = f'B-{traffic}-{policy}-{repeat}'
                lab.phase = name
                lab.kube('rollout', 'restart', 'deployment/hw11-api')
                lab.wait_ready()
                states = [lab.get(url='http://' + p['status']['podIP'] + ':8000') for p in lab.pods()]
                lab.check(name + '-cold-cache', all(s['cache_entries']==0 for s in states))
                lab.event('case_parameters', policy=policy, traffic=traffic, repeat=repeat, states=states)
                rows = lab.load(name, duration=120, rate=5, traffic=traffic, policy=policy, seed=11+repeat, chunks=12)
                lab.logs(name)
                distribution = {}
                for row in rows:
                    distribution[row.get('pod') or 'unknown'] = distribution.get(row.get('pod') or 'unknown',0)+1
                # Preserve even a result without the expected skew. Never retry
                # until a conveniently skewed assignment appears.
                lab.event('distribution', name=name, counts=distribution,
                          expected_skew_observed=max(distribution.values(),default=0)/max(1,len(rows)) > .7)


def experiment_c(lab):
    lab.phase = 'C-HPA'
    lab.baseline(hpa__enabled=True, missCpu=.5, hitCpu=.15, chunkCpu=.03,
                 chunkInterval=.01, cacheSize=0)
    lab.until(lambda: len(lab.pods())==2, 90, 'HPA minimum did not settle at two Pods')
    lab.kube('get', '--raw', '/apis/metrics.k8s.io/v1beta1/namespaces/hw11/pods')
    group = lab.start_load('C-scale-up', duration=180, rate=12, chunks=8)
    begin = time.time()
    lab.until(lambda: len(lab.pods()) == 3, 160, 'HPA did not reach 3 replicas')
    lab.event('hpa_3_pods_created', seconds=time.time()-begin)
    lab.until(lambda: all(any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[]))
                         for p in lab.pods()), 90, 'Third replica not ready')
    lab.event('hpa_3_pods_ready', seconds=time.time()-begin)
    lab.finish_load(group)
    dropped = group['start'] + group['duration']
    lab.event('load_reduced', wall_reduced=dropped)
    lab.until(lambda: len(lab.pods()) == 2, 300, 'HPA did not return to 2 replicas')
    lab.event('hpa_back_to_2', seconds=time.time()-dropped)
    lab.check('hpa_2_to_3_to_2', len(lab.pods())==2)

    lab.phase = 'C-hot-affinity'
    lab.load('C-hot-affinity', duration=120, rate=8, traffic='skew', policy='affinity', chunks=8)
    lab.kube('describe','hpa','hw11-api')
    # Separate high-latency and rejection phases, with explicit baseline recovery.
    lab.phase = 'C-alerts'
    lab.configure(hpa__enabled=False, replicas=2, concurrency=1, queueCapacity=48,
                  queueTimeout=12, hitCpu=.2, missCpu=.2, chunkCpu=.008, chunkInterval=.02)
    lab.load('C-alert-latency', duration=120, rate=12, chunks=12)
    lab.configure(queueCapacity=0)
    lab.load('C-alert-errors', duration=120, rate=30, chunks=12)
    lab.baseline()
    lab.load('C-alert-recovery', duration=100, rate=1, chunks=2)
    # Assert transitions using the Prometheus samples captured by the collector.
    states = {'HighTTFC':set(), 'HighFailureRatio':set()}
    for line in (lab.output/'events.jsonl').read_text().splitlines():
        event = json.loads(line)
        if event['event']=='prometheus':
            for item in event['data'].get('alerts',{}).get('data',{}).get('result',[]):
                metric=item['metric']
                if metric.get('alertname') in states:
                    states[metric['alertname']].add(metric.get('alertstate'))
    for name, found in states.items():
        lab.check(name+'-pending-firing', {'pending','firing'} <= found, states=sorted(found))
    alerts = lab.command(['kubectl','--context',CTX,'-n',NS,'exec','deployment/hw11-prometheus','--',
                        'wget','-qO-','http://127.0.0.1:9090/api/v1/alerts']).stdout
    active = json.loads(alerts)['data']['alerts']
    lab.check('alerts_returned_to_normal', not active)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action', choices=['setup','run','A','B','C','cleanup'])
    p.add_argument('--output', default='results/cluster-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    args=p.parse_args()
    lab=Lab(args.output)
    try:
        if args.action=='cleanup':
            lab.command(['kind','delete','cluster','--name','mlops-hw11'])
            lab.command(['docker','image','rm',IMAGE], check=False)
            return
        if args.action in ['setup','run']:
            setup(lab)
        if args.action!='setup':
            lab.start_collecting()
            for name, fn in [('A',experiment_a),('B',experiment_b),('C',experiment_c)]:
                if args.action in ['run',name]:
                    fn(lab)
            lab.logs('final')
            (lab.output/'size-after.txt').write_text(lab.command(['docker','system','df']).stdout)
            lab.event('experiments_complete')
    except Exception as exc:
        lab.event('failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        lab.close()
    print('Results:',lab.output)


if __name__=='__main__':
    main()
