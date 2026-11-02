"""Общее состояние для stdlib адаптера и FastAPI; вычисления строго последовательно."""
import json
from pathlib import Path
import threading
import time
import uuid
from backends import StubBackend
from release import verify_artifact


class ServiceUnavailable(Exception):
    pass


class ServiceState:
    def __init__(self, root, warmup_seconds, backend_factory=StubBackend):
        self.root = Path(root)
        self.ready = False
        self.backend = None
        self.error = None
        self.warmup_finished = threading.Event()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        # Snapshot manifest exactly once. Updating accepted.json cannot change advertised metadata.
        self.manifest = json.loads((self.root / 'accepted.json').read_text())
        def load():
            try:
                artifact = verify_artifact(self.manifest, self.root)
                if self.manifest['runtime']['base_revision'] != 'fixture-only':
                    from gate_bridge import verify_release_report
                    report_path = self.root / self.manifest['release_report_uri']
                    from release import digest
                    if digest(report_path.read_bytes()) != self.manifest['release_report_sha256']:
                        raise ValueError('Release report checksum mismatch')
                    verify_release_report(self.manifest, json.loads(report_path.read_text()), self.root)
                backend = backend_factory(self.manifest, artifact)
                # Actual warmup is distinct from artificial delay used in the HTTP tests.
                if backend.label != 'STUB ONLY':
                    backend.generate('Product review: works well. Claim: works well. Choices: SUPPORTED, NOT_STATED.',
                                     backend.max_new_tokens)
                self.backend = backend
                if self.stop.wait(warmup_seconds):
                    return
                self.warmup_finished.set()
                self.ready = True
            except Exception as exc:
                self.error = f'{type(exc).__name__}: {exc}'
                import logging
                logging.exception('Model startup failed')
        self.thread = threading.Thread(target=load, daemon=True)
        self.thread.start()

    def validate(self, request):
        if not isinstance(request, dict) or set(request) - {'prompt', 'max_new_tokens'}:
            raise ValueError('Expected prompt and optional max_new_tokens')
        prompt = request.get('prompt')
        limit = request.get('max_new_tokens', self.manifest['runtime']['generation']['max_new_tokens'])
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16384:
            raise ValueError('prompt must contain 1..16384 characters')
        maximum = self.manifest['runtime']['generation']['max_new_tokens']
        if type(limit) is not int or not 1 <= limit <= maximum:
            raise ValueError(f'max_new_tokens must be integer in 1..{maximum}')
        # Real service serves exactly the evaluated limit; stub exercises the declared range.
        if self.manifest['runtime']['base_revision'] != 'fixture-only' and limit != maximum:
            raise ValueError('Real backend requires the evaluated max_new_tokens')
        if self.ready and self.manifest['runtime']['base_revision'] != 'fixture-only':
            settings = self.manifest['runtime']['generation']
            text = self.backend.tokenizer.apply_chat_template(
                [{'role': 'system', 'content': settings['system']}, {'role': 'user', 'content': prompt}],
                tokenize=False, add_generation_prompt=True, enable_thinking=settings['enable_thinking'])
            if len(self.backend.tokenizer.encode(text)) > self.manifest['runtime']['max_input_tokens']:
                raise ValueError('Input exceeds evaluated token limit')
        return prompt, limit

    def generate(self, request):
        started = time.perf_counter()
        prompt, limit = self.validate(request)
        if not self.ready:
            raise ServiceUnavailable('Model is loading or warming up')
        # At most one backend call, bounded wait. Queue timing is reported separately.
        if not self.lock.acquire(timeout=120):
            raise ServiceUnavailable('Generation queue timeout')
        try:
            generation_started = time.perf_counter()
            result = self.backend.generate(prompt, limit)
            result.update(model_id=self.backend.loaded_id, runtime_sha256=self.backend.runtime_sha256,
                          request_id=str(uuid.uuid4()), generation_seconds=time.perf_counter()-generation_started,
                          queue_seconds=generation_started-started, total_seconds=time.perf_counter()-started)
            return result
        finally:
            self.lock.release()

    def debug(self):
        return {'loaded_id': self.backend.loaded_id if self.backend else None,
                'calls': self.backend.calls if self.backend else 0,
                'warmup_finished': self.warmup_finished.is_set()}

    def close(self):
        self.stop.set()
