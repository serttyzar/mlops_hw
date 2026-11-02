"""Один HTTP/subprocess контракт для starter, stdlib и FastAPI; без моков сети."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CODE = Path(os.environ.get('LAB_CODE_DIR', Path(__file__).resolve().parents[1])).resolve()
SERVICE = os.environ.get('SERVICE_ENTRY', 'service.py')


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'workspace'
        self.command('init', ok=True)

    def command(self, *args, ok=None):
        result = subprocess.run([sys.executable, str(CODE / 'lab.py'), '--workspace',
                                 str(self.root), *args], capture_output=True, text=True, timeout=30)
        print(json.dumps({'command': list(args), 'exit_code': result.returncode,
                          'stdout': result.stdout, 'stderr': result.stderr}, ensure_ascii=False))
        if ok is True:
            self.assertEqual(result.returncode, 0, result.stderr)
        elif ok is False:
            self.assertNotEqual(result.returncode, 0)
        return result

    def pointer(self):
        return hashlib.sha256((self.root / 'accepted.json').read_bytes()).hexdigest()

    def events(self):
        p = self.root / 'publications.jsonl'
        return [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []

    @contextlib.contextmanager
    def server(self, warmup=0):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        with tempfile.TemporaryFile(mode='w+') as log:
            process = subprocess.Popen([sys.executable, str(CODE / SERVICE), '--workspace',
                                        str(self.root), '--port', str(port),
                                        '--warmup-seconds', str(warmup)], stdout=log, stderr=log)
            self.url = f'http://127.0.0.1:{port}'
            try:
                end = time.monotonic() + 15
                while time.monotonic() < end:
                    try:
                        if self.http('/health')[0] == 200:
                            break
                    except (URLError, ConnectionError):
                        pass
                    if process.poll() is not None:
                        log.seek(0)
                        self.fail('Server exited: ' + log.read())
                    time.sleep(.02)
                else:
                    self.fail('Server did not start')
                yield
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def http(self, path, body=None):
        request = Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            value = json.load(response)
            print(json.dumps({'http': path, 'status': response.status, 'body': value}, ensure_ascii=False))
            return response.status, value

    def wait_ready(self):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if self.http('/ready')[0] == 200:
                return
            time.sleep(.03)
        self.fail('Not ready')

    def test_cache_recovery_and_changed_input(self):
        self.command('run', '--fail-after-prepare', ok=False)
        self.assertIn('CACHE_HIT', self.command('run', ok=True).stdout)
        (self.root / 'input.txt').write_text('Другие данные по тому же пути\n')
        self.assertIn('PREPARED', self.command('run', ok=True).stdout)
        config_path = self.root / 'config.json'
        config = json.loads(config_path.read_text())
        for key, value in [('template_sha256', 'f' * 64), ('max_length', 256),
                           ('tokenizer_revision', 'r2'), ('code_revision', 'prepare-v2')]:
            config[key] = value
            config_path.write_text(json.dumps(config))
            self.assertIn('PREPARED', self.command('run', ok=True).stdout)

    def test_incomplete_and_foreign_reports_preserve_pointer(self):
        before = self.pointer()
        for name in ('report-incomplete.json', 'report-A.json'):
            with self.subTest(report=name):
                result = self.command('run', '--report', name)
                after = self.pointer()
                print(json.dumps({'report': name, 'before_sha256': before, 'after_sha256': after}))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(before, after)

    def test_rejection_and_disk_error_preserve_pointer(self):
        before = self.pointer()
        self.command('run', '--report', 'report-reject.json', ok=False)
        self.assertEqual(self.pointer(), before)
        spec = importlib.util.spec_from_file_location('release_under_test', CODE / 'release.py')
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        m = json.loads((self.root / 'manifest-B.json').read_text())
        r = json.loads((self.root / 'report-B.json').read_text())
        with patch('os.replace', side_effect=OSError('simulated disk error')):
            with self.assertRaises(OSError):
                release.publish(m, r, self.root / 'accepted.json')
        self.assertEqual(self.pointer(), before)
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_identical_release_in_two_processes_has_one_event(self):
        self.command('run', ok=True)
        before = self.pointer()
        self.command('run', ok=True)
        self.assertEqual(before, self.pointer())
        self.assertEqual(len(self.events()), 1)

    def test_http_readiness_blocks_generation_until_warm(self):
        with self.server(warmup=1.5):
            self.assertEqual(self.http('/ready')[0], 503)
            before = self.http('/debug')[1]['calls']
            self.assertEqual(self.http('/generate', {'prompt': 'hello', 'max_new_tokens': 32})[0], 503)
            self.assertEqual(self.http('/debug')[1]['calls'], before)
            self.wait_ready()
            self.assertTrue(self.http('/debug')[1]['warmup_finished'])
            self.assertEqual(self.http('/generate', {'prompt': 'hello', 'max_new_tokens': 32})[0], 200)

    def test_http_loaded_version_restart_and_rollback(self):
        request = {'prompt': 'hello', 'max_new_tokens': 32}
        with self.server():
            self.wait_ready()
            self.assertEqual(self.http('/generate', request)[1]['model_id'], 'fixture-A')
            self.command('run', ok=True)
            reply = self.http('/generate', request)[1]
            self.assertEqual(reply['model_id'], 'fixture-A')
            self.assertIn('fixture-A', reply['answer'])
        with self.server():
            self.wait_ready()
            reply = self.http('/generate', request)[1]
            self.assertEqual(reply['model_id'], 'fixture-B')
            self.assertIn('fixture-B', reply['answer'])
        # Тот же совместимый CLI публикует ранее принятую A, без редактирования указателя.
        (self.root / 'manifest-B.json').write_bytes((self.root / 'manifest-A.json').read_bytes())
        self.command('run', '--report', 'report-A.json', ok=True)
        with self.server():
            self.wait_ready()
            reply = self.http('/generate', request)[1]
            self.assertEqual(reply['model_id'], 'fixture-A')
            self.assertIn('fixture-A', reply['answer'])

    def test_http_invalid_requests_do_not_call_backend(self):
        with self.server():
            self.wait_ready()
            calls = self.http('/debug')[1]['calls']
            for body in ({'prompt': ''}, {'prompt': 'hello', 'max_new_tokens': 0},
                         {'prompt': 'hello', 'max_new_tokens': 100000},
                         {'prompt': 'hello', 'max_new_tokens': '32'},
                         {'prompt': 'hello', 'max_new_tokens': True},
                         {'prompt': 'x' * 17000}, []):
                with self.subTest(body=str(body)[:60]):
                    status, _ = self.http('/generate', body)
                    self.assertTrue(400 <= status < 500)
                    self.assertEqual(self.http('/debug')[1]['calls'], calls)
            self.assertEqual(self.http('/generate', {'prompt': 'hello', 'max_new_tokens': 32})[0], 200)
            self.assertEqual(self.http('/debug')[1]['calls'], calls + 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
