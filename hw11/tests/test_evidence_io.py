import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evidence_io import read_jsonl, jsonl_exists


class EvidenceIOTests(unittest.TestCase):
    def test_compressed_checkout_preserves_records(self):
        rows = [{'event': 'snapshot', 'value': 'проверка'}, {'event': 'experiments_complete'}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            data = ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows)
            with gzip.open(str(path)+'.gz', 'wt', encoding='utf-8') as stream:
                stream.write(data)
            self.assertFalse(path.exists())
            self.assertTrue(jsonl_exists(path))
            self.assertEqual(list(read_jsonl(path)), rows)
            self.assertEqual(list(read_jsonl(Path(str(path)+'.gz'))), rows)

    def test_live_uncompressed_log_remains_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            path.write_text('{"event":"live"}\n')
            self.assertEqual(list(read_jsonl(path)), [{'event':'live'}])
