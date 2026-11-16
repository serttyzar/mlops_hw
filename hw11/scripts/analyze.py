"""Analyze only supplied raw records. No synthetic replacement for absent data."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path


def quantile(values, probability):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    return values[low] + (values[high]-values[low]) * (position-low)


def summary(rows):
    completed = [r for r in rows if r['outcome']=='completed']
    counts = Counter(r['outcome'] for r in rows)
    return {'planned':len(rows), 'sent':sum(r.get('sent_mono') is not None for r in rows),
            'completed':len(completed), 'outcomes':dict(counts),
            'error_fraction':1-len(completed)/len(rows) if rows else None,
            'timeout_fraction':counts['timed_out']/len(rows) if rows else None,
            **{f'{metric}_{p}':quantile([r[metric] for r in completed if r.get(metric) is not None], q)
               for metric in ['ttfc_seconds','total_seconds','queue_seconds'] for p,q in [('p50',.5),('p95',.95)]},
            'send_lag_seconds_p95':quantile([r['send_lag_seconds'] for r in rows if r.get('send_lag_seconds') is not None],.95),
            'inter_chunk_seconds_p95':quantile([v for r in completed for v in r.get('inter_chunk_seconds',[])],.95)}


def histogram_quantile(buckets, probability):
    """Classic cumulative histogram interpolation, including +Inf handling."""
    points = sorted((float(edge), count) for edge, count in buckets.items())
    if not points or points[-1][1] <= 0:
        return None
    target = points[-1][1]*probability
    previous_edge, previous_count = 0., 0.
    for edge, count in points:
        if count >= target:
            if math.isinf(edge):
                return previous_edge
            if count == previous_count:
                return edge
            return previous_edge + (edge-previous_edge)*(target-previous_count)/(count-previous_count)
        previous_edge, previous_count = edge, count
    return None


def histogram_comparison(directory, name, rows):
    before_path=directory/f'{name}-metrics-before.json'
    after_path=directory/f'{name}-metrics-after.json'
    if not before_path.exists() or not after_path.exists():
        return {'available':False,'reason':'Missing Prometheus counter snapshots'}
    before=json.loads(before_path.read_text())
    after=json.loads(after_path.read_text())
    def values(snapshot, metric):
        return {(item['metric']['pod'],item['metric'].get('le','')):float(item['value'][1])
                for item in snapshot['queries'][metric]['data']['result']}
    old=values(before,'hw11_ttfc_seconds_bucket')
    new=values(after,'hw11_ttfc_seconds_bucket')
    # Pod sets must match: resetting a counter invalidates direct subtraction.
    if set(old)!=set(new):
        return {'available':False,'reason':'Pod cohort changed; cannot compare identical counter interval'}
    differences={key:new[key]-old[key] for key in new}
    if any(v<0 for v in differences.values()):
        return {'available':False,'reason':'Counter reset during the run'}
    summed=defaultdict(float)
    pods=defaultdict(dict)
    for (pod,le),value in differences.items():
        summed[le]+=value
        pods[pod][le]=value
    successes=[r for r in rows if r['outcome']=='completed']
    observed=len(successes)
    server_count=summed.get('+Inf',0)
    return {'available':True,'same_success_count':observed==server_count,
            'client_n':observed,'server_n':server_count,
            'client_p95_seconds':quantile([r['ttfc_seconds'] for r in successes],.95),
            'server_bucket_p95_seconds':histogram_quantile(summed,.95),
            'buckets':dict(summed),'per_pod':{pod: {'n':b.get('+Inf',0),'p95_seconds':histogram_quantile(b,.95)} for pod,b in pods.items()},
            'note':'Same isolated run, no other generate callers. Bucket interpolation and HTTP delivery can differ.'}


def analyze(directory):
    path=directory/'requests.jsonl'
    rows=[json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []
    groups=defaultdict(list)
    by_pod=defaultdict(list)
    for row in rows:
        groups[row.get('run','unknown')].append(row)
        by_pod[(row.get('run','unknown'),row.get('pod') or 'unknown')].append(row)
    result={'available':bool(rows),'scope':'real Kubernetes records only' if rows else 'no Kubernetes measurements',
            'overall':summary(rows), 'runs':{k:summary(v) for k,v in groups.items()},
            'per_pod':[{ 'run':run,'pod':pod,**summary(data)} for (run,pod),data in by_pod.items()],
            'histogram_comparison':{name:histogram_comparison(directory,name,data) for name,data in groups.items() if name.startswith('B-')}}
    (directory/'summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);args=p.parse_args()
    result=analyze(args.directory)
    print(json.dumps({'available':result['available'],'requests':result['overall']['planned']}))
