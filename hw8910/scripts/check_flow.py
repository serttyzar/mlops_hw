"""Automated Prefect cross-process evidence with fresh workspaces and exact exit codes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from publication import read, write
from release import digest


def main():
    records = []
    logs = ROOT / 'docs/logs'
    with tempfile.TemporaryDirectory(prefix='hw8910-flows-') as temporary:
        base = Path(temporary)
        def command(name, root, entry, *arguments, expected=0, marker=None):
            cmd = [sys.executable, str(ROOT / entry), '--workspace', str(root), *arguments]
            result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=120)
            (logs / (name+'.log')).write_text(result.stdout + result.stderr)
            before_after = {'command':cmd, 'exit_code':result.returncode,'log':'docs/logs/'+name+'.log'}
            if (root/'accepted.json').exists():
                before_after['pointer_sha256'] = digest((root/'accepted.json').read_bytes())
            records.append(before_after)
            if result.returncode != expected:
                raise RuntimeError(f'{name}: expected {expected}, got {result.returncode}; see log')
            if marker and marker not in result.stdout + result.stderr:
                raise RuntimeError(f'{name}: no {marker}')
            print(name, result.returncode, marker, flush=True)
        recovery=base/'recovery'
        command('flow-init',recovery,'lab.py','init')
        command('flow-failure',recovery,'flow.py','--fail-after-prepare',expected=1,marker='PREPARATION_STATE Completed')
        command('flow-recovery',recovery,'flow.py',marker='PREPARATION_STATE Cached')
        (recovery/'input.txt').write_text('Different data at same path\n')
        command('flow-invalidation',recovery,'flow.py',marker='PREPARATION_STATE Completed')
        negative=base/'negative'
        command('flow-negative-init',negative,'lab.py','init')
        before=digest((negative/'accepted.json').read_bytes())
        for name in ('report-incomplete.json','report-A.json','report-reject.json'):
            command('flow-negative-'+name[:-5],negative,'flow.py','--report',name,expected=1)
            if digest((negative/'accepted.json').read_bytes()) != before:
                raise RuntimeError('Rejection modified pointer')
        crash=base/'crash'
        command('flow-crash-init',crash,'lab.py','init')
        command('flow-crash',crash,'flow.py','--fail-after-pointer',expected=1)
        command('flow-journal-recovery',crash,'flow.py',marker='RECOVERED')
        command('flow-repeat',crash,'flow.py')
        events=(crash/'publications.jsonl').read_text().splitlines()
        if len(events)!=1:
            raise RuntimeError('Duplicate release after pointer/ledger recovery')
        write(ROOT/'metrics/flow-experiments.json', {'commands':records,'recovered_events':[json.loads(s) for s in events]})


if __name__=='__main__':
    main()
