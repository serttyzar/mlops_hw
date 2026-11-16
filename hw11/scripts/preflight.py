"""Save environment constraints before building or loading images."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def probe(command):
    try:
        r = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return {'command': command, 'returncode': r.returncode, 'stdout': r.stdout[:8000], 'stderr': r.stderr[:2000]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'command': command, 'returncode': -1, 'stderr': str(exc)}


def inspect(output):
    usage = shutil.disk_usage('.')
    commands = [['docker', 'version', '--format', '{{.Client.Version}} / {{.Server.Version}}'],
                ['docker', 'info', '--format', '{"cpu":{{.NCPU}},"memory":{{.MemTotal}},"os":"{{.OperatingSystem}}"}'],
                ['docker', 'system', 'df'], ['kubectl', 'version', '--client', '-o', 'json'],
                ['helm', 'version', '--short'], ['kind', 'version']]
    result = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
              'python': sys.version, 'disk': dict(zip(['total', 'used', 'free'], usage)),
              'required_budget': {'vm_cpu': 4, 'vm_memory_gib': 6, 'free_disk_gib': 8},
              'probes': [probe(command) for command in commands]}
    result['cluster_available'] = all(p['returncode'] == 0 for p in result['probes'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('results/local/environment.json'))
    args = parser.parse_args()
    result = inspect(args.output)
    print(json.dumps({'cluster_available': result['cluster_available'], 'saved': str(args.output),
                      'free_disk_gib': round(result['disk']['free'] / 2**30, 1)}, ensure_ascii=False))
    raise SystemExit(0 if result['cluster_available'] else 2)
