"""Run unchanged tests against exact starter commit, not a hand reconstructed defect."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STARTER = '732ddae'
records = []
with tempfile.TemporaryDirectory(prefix='hw8910-starter-') as directory:
    target = Path(directory)
    for name in ('release.py', 'lab.py', 'service.py'):
        result = subprocess.run(['git','show',f'{STARTER}:hw8910/{name}'],cwd=ROOT.parent,check=True,capture_output=True)
        (target/name).write_bytes(result.stdout)
    for name, source in [('contracts-before', ROOT/'tests'), ('integration-before', ROOT/'tests_integration')]:
        cmd=[sys.executable,'-m','unittest','discover','-s',str(source),'-p',
             'test_release.py' if name.startswith('contracts') else 'test_process.py','-v']
        result=subprocess.run(cmd,cwd=target,env={**os.environ,'LAB_CODE_DIR':str(target),'PYTHONPATH':str(target)},
                              capture_output=True,text=True,timeout=120)
        (ROOT/'docs/logs'/f'{name}.log').write_text(result.stdout+result.stderr)
        records.append({'starter_commit':STARTER,'test':name,'exit_code':result.returncode,'command':cmd})
        if result.returncode==0 or 'ERROR:' in result.stderr:
            raise RuntimeError('Expected behavior failures, not import/CLI/socket errors')
        print(name,'exit_code',result.returncode,flush=True)
(ROOT/'metrics/before-tests.json').write_text(json.dumps(records,indent=2)+'\n')
