"""Introduce a real cache defect in a disposable copy; preserve failed test exit code."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    target = Path(directory)
    for source in root.glob('*.py'):
        shutil.copyfile(source, target / source.name)
    with (target / 'release.py').open('a') as stream:
        stream.write('\n\ndef cache_key(data_path, config):\n    return digest(str(data_path).encode())\n')
    result = subprocess.run([sys.executable, '-m', 'unittest',
                             'tests_integration.test_process.ProcessTests.test_cache_recovery_and_changed_input', '-v'],
                            cwd=root, env={**os.environ, 'LAB_CODE_DIR': str(target)})
    print('Intentional cache mutation exit code:', result.returncode, flush=True)
    sys.exit(result.returncode)
