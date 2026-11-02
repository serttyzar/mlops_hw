"""Additional filesystem fault-injection tests, beyond the seven starter regressions."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab import initialize
from publication import events, publish_once, read, recover
from release import verify_artifact


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'lab'
        with contextlib.redirect_stdout(io.StringIO()):
            initialize(self.root)
        self.m = read(self.root / 'manifest-B.json')
        self.r = read(self.root / 'report-B.json')

    def test_crash_after_pointer_recovery_is_idempotent(self):
        with self.assertRaises(RuntimeError):
            publish_once(self.root, self.m, self.r, fail_after_pointer=True)
        self.assertEqual(read(self.root / 'accepted.json')['model_id'], 'fixture-B')
        self.assertEqual(events(self.root), [])
        recover(self.root)
        recover(self.root)
        publish_once(self.root, self.m, self.r)
        self.assertEqual(len(events(self.root)), 1)
        self.assertFalse((self.root / 'pending-publication.json').exists())

    def test_journal_write_failure_can_recover(self):
        from release import atomic_write
        def broken(path, data):
            if Path(path).name == 'publications.jsonl':
                raise OSError('journal write failed')
            atomic_write(path, data)
        with patch('publication.atomic_write', side_effect=broken):
            with self.assertRaises(OSError):
                publish_once(self.root, self.m, self.r)
        recover(self.root)
        publish_once(self.root, self.m, self.r)
        self.assertEqual(len(events(self.root)), 1)

    def test_artifact_tamper_does_not_publish(self):
        before = (self.root / 'accepted.json').read_bytes()
        (self.root / self.m['artifact_uri']).write_text('corrupted weights')
        with self.assertRaises(ValueError):
            publish_once(self.root, self.m, self.r)
        self.assertEqual(before, (self.root / 'accepted.json').read_bytes())
        self.assertEqual(events(self.root), [])

    def test_changed_evaluation_is_another_release_identity(self):
        publish_once(self.root, self.m, self.r)
        other = {**self.r, 'evaluation_run_id': 'fixture-new-evaluation'}
        publish_once(self.root, self.m, other)
        self.assertEqual(len(events(self.root)), 2)
        self.assertNotEqual(events(self.root)[0]['release_id'], events(self.root)[1]['release_id'])

    def test_rejected_report_creates_no_pending_intent(self):
        with self.assertRaises(ValueError):
            publish_once(self.root, self.m, {**self.r, 'passed': False})
        self.assertFalse((self.root / 'pending-publication.json').exists())
        self.assertEqual(events(self.root), [])
