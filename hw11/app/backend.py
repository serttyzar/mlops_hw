"""Reuse the previous homework backend contract without loading real weights."""
import hashlib
import json
from pathlib import Path
import sys

PREVIOUS = Path(__file__).resolve().parents[2] / 'hw8910'
sys.path.insert(0, str(PREVIOUS))
from backends import StubBackend  # noqa: E402


class SimulatedBackend(StubBackend):
    label = 'simulated'

    def __init__(self, artifact: Path, expected_hash: str, expected_version: str):
        raw = artifact.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_hash:
            raise ValueError('Artifact checksum mismatch')
        spec = json.loads(raw)
        if spec['version'] != expected_version:
            raise ValueError('Artifact version mismatch')
        super().__init__({'model_id': spec['version'], 'runtime_sha256': actual,
                          'runtime': {'base_revision': 'fixture-only',
                                      'generation': {'max_new_tokens': 64}}})
        self.valid = spec['ready'] is True

    def generate(self, prompt, limit):
        # Keep hw8910's injectable generate(prompt, limit) contract. Chunking,
        # admission and simulated compute are owned by the serving layer.
        self.calls += 1
        return {'answer': 'SIMULATED: ' + prompt, 'output_chunks': limit,
                'backend_mode': self.label, 'model_id': self.loaded_id}
