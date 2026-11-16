import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import time
import uuid

import anyio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, ConfigDict, Field
from .backend import SimulatedBackend

log = logging.getLogger('hw11')
logging.basicConfig(level=logging.INFO, format='%(message)s')


@dataclass
class Settings:
    pod: str = 'local'
    version: str = 'A'
    artifact_dir: str = str(Path(__file__).resolve().parents[1] / 'artifacts')
    warmup: float = 20
    broken_probes: bool = False
    faults_enabled: bool = False
    concurrency: int = 1
    queue_capacity: int = 24
    queue_timeout: float = 12
    cache_size: int = 16
    hit_cpu: float = .025
    miss_cpu: float = .2
    chunk_cpu: float = .008
    chunk_interval: float = .02

    @classmethod
    def from_env(cls):
        s = cls()
        for name in s.__dataclass_fields__:
            value = os.getenv('HW11_' + name.upper())
            if value is not None:
                current = getattr(s, name)
                setattr(s, name, value.lower() == 'true' if type(current) is bool else type(current)(value))
        if s.concurrency < 1 or s.queue_capacity < 0 or s.cache_size < 0:
            raise ValueError('Invalid capacity')
        for name in ['warmup', 'queue_timeout', 'hit_cpu', 'miss_cpu', 'chunk_cpu', 'chunk_interval']:
            value = getattr(s, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError('Invalid ' + name)
        if s.queue_timeout > 12:
            raise ValueError('Queue timeout exceeds graceful shutdown budget')
        if max(s.hit_cpu, s.miss_cpu) > 2 or s.chunk_cpu > .1 or s.chunk_interval > .5:
            raise ValueError('Request duration exceeds graceful shutdown budget')
        return s


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: str = Field(pattern='^(system|user|assistant)$')
    content: str = Field(max_length=4096)


class Generation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    prompt: str = Field(min_length=1, max_length=16384)
    max_new_tokens: int = Field(default=8, ge=1, le=64)
    messages: list[Message] = Field(default_factory=list, max_length=12)
    stream: bool = False
    session_id: str = Field(default='', max_length=128)


def compute(seconds):
    """Actual CPU work measured by the worker's CPU clock, not sleeping."""
    deadline = time.thread_time() + seconds
    data = b'cpu-simulator'
    while time.thread_time() < deadline:
        data = hashlib.sha256(data).digest()
    return data


async def spend_cpu(seconds):
    # Cancellation of asyncio.to_thread does not stop the underlying worker.
    # Retain the admission slot until that worker really finishes.
    task = asyncio.create_task(asyncio.to_thread(compute, seconds))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class ManagedStreamingResponse(StreamingResponse):
    def __init__(self, *args, on_close, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_close = on_close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also handles disconnect before the body iterator ever starts.
            try:
                with anyio.CancelScope(shield=True):
                    await self.body_iterator.aclose()
            finally:
                self.on_close()


class State:
    def __init__(self, settings):
        self.s = settings
        self.backend = None
        self.warm = False
        self.draining = False
        self.error = None
        self.running = 0
        self.waiting = 0
        self.cache = OrderedDict()
        self.semaphore = asyncio.Semaphore(settings.concurrency)
        self.registry = CollectorRegistry(auto_describe=True)
        buckets = (.01, .025, .05, .1, .2, .4, .8, 1.6, 3.2, 6.4, 12.8, 25.6, 51.2)
        self.ttfc = Histogram('hw11_ttfc_seconds', 'TTFC of completed successful responses', buckets=buckets, registry=self.registry)
        self.total = Histogram('hw11_response_seconds', 'Duration of completed successful responses', buckets=buckets, registry=self.registry)
        self.queue = Histogram('hw11_queue_seconds', 'Admission queue time', buckets=buckets, registry=self.registry)
        self.outcomes = Counter('hw11_requests', 'Terminal outcomes', ['outcome'], registry=self.registry)
        for outcome in ['completed', 'rejected', 'aborted', 'invalid']:
            self.outcomes.labels(outcome)
        self.cache_events = Counter('hw11_cache', 'Simulated local prefix cache', ['result'], registry=self.registry)
        self.g_running = Gauge('hw11_running', 'Active computations/streams', registry=self.registry)
        self.g_waiting = Gauge('hw11_waiting', 'Queued requests', registry=self.registry)
        self.g_ready = Gauge('hw11_backend_ready', 'Actual backend readiness', registry=self.registry)
        self.warm_requests = Counter('hw11_unready_requests', 'Requests received by an unready backend', registry=self.registry)

    async def load(self):
        try:
            root = Path(self.s.artifact_dir)
            hashes = json.loads((root / 'sha256.json').read_text())
            self.backend = SimulatedBackend(root / f'{self.s.version}.json', hashes[self.s.version], self.s.version)
            await asyncio.sleep(self.s.warmup)
            self.warm = self.backend.valid
            self.g_ready.set(self.warm)
            log.info(json.dumps({'event': 'warmup_finished', 'pod': self.s.pod,
                                 'version': self.backend.loaded_id, 'ready': self.warm, 'wall': time.time()}))
        except Exception as exc:
            self.error = str(exc)
            log.exception('Artifact loading failed')

    def identity(self, request_id):
        return {'request_id': request_id, 'pod': self.s.pod,
                'version': self.backend.loaded_id if self.backend else None,
                'artifact_sha256': self.backend.runtime_sha256 if self.backend else None,
                'backend_mode': 'simulated'}


def create_app(settings=None):
    settings = settings or Settings.from_env()
    state = State(settings)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(state.load())
        try:
            yield
        finally:
            state.draining = True
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)
    app.state.engine = state

    @app.get('/health')
    @app.get('/live')
    async def live():
        # Event-loop responsiveness, deliberately independent of queue and warmup.
        return {'alive': True, 'backend_mode': 'simulated'}

    @app.get('/startup')
    async def startup():
        started = settings.broken_probes or state.warm
        return JSONResponse({'started': started, 'error': state.error}, status_code=200 if started else 503)

    @app.get('/ready')
    async def ready():
        value = not state.draining and (settings.broken_probes or state.warm)
        return JSONResponse({'ready': value, 'backend_warm': state.warm}, status_code=200 if value else 503)

    @app.post('/drain')
    async def drain(request: Request):
        if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
            return Response(status_code=403)
        state.draining = True
        state.g_ready.set(0)
        return {'draining': True, 'running': state.running}

    @app.post('/fault/exit')
    async def forced_exit(request: Request):
        if not settings.faults_enabled or not request.client or request.client.host not in ('127.0.0.1', '::1', 'testclient'):
            return Response(status_code=403)
        # A real abrupt process exit, no lifespan cleanup or graceful drain.
        asyncio.get_running_loop().call_later(.1, os._exit, 137)
        return {'exit_scheduled': True}

    @app.get('/info')
    async def info():
        return {**state.identity(''), 'warm': state.warm, 'draining': state.draining,
                'running': state.running, 'waiting': state.waiting,
                'cache_entries': len(state.cache), 'settings': vars(settings)}

    @app.get('/metrics')
    async def metrics():
        return Response(generate_latest(state.registry), headers={'Content-Type': CONTENT_TYPE_LATEST})

    @app.post('/generate')
    async def generate(request: Request):
        started = time.perf_counter()
        request_id = request.headers.get('x-request-id') or str(uuid.uuid4())
        if len(request_id) > 128 or not all(32 <= ord(c) < 127 for c in request_id):
            request_id = str(uuid.uuid4())
        identity = state.identity(request_id)
        headers = {'X-Request-ID': request_id, 'X-Pod': settings.pod,
                   'X-Model-Version': str(identity['version']), 'X-Backend-Mode': 'simulated'}

        def terminal(outcome, **extra):
            state.outcomes.labels(outcome).inc()
            log.info(json.dumps({'event': 'request', **identity, 'outcome': outcome,
                                 'wall': time.time(), 'duration': time.perf_counter() - started,
                                 'client_ip': request.client.host if request.client else None, **extra}))

        def reject(status, reason):
            terminal('rejected' if status != 400 else 'invalid', reason=reason)
            return JSONResponse({**identity, 'error': reason}, status_code=status, headers=headers)

        raw = bytearray()
        async for data in request.stream():
            raw.extend(data)
            if len(raw) > 65536:
                return reject(413, 'body_too_large')
        try:
            payload = Generation.model_validate_json(raw)
        except ValueError:
            return reject(400, 'invalid_request')
        if not state.warm or state.draining:
            if not state.warm:
                state.warm_requests.inc()
            return reject(503, 'backend_unready' if not state.warm else 'draining')
        # No await between admission check and accounting: atomic on this loop.
        if state.running + state.waiting >= settings.concurrency + settings.queue_capacity:
            return reject(429, 'queue_full')
        state.waiting += 1
        state.g_waiting.set(state.waiting)
        try:
            await asyncio.wait_for(state.semaphore.acquire(), settings.queue_timeout)
        except asyncio.TimeoutError:
            return reject(429, 'queue_timeout')
        except asyncio.CancelledError:
            terminal('aborted', reason='cancelled_in_queue')
            raise
        finally:
            state.waiting -= 1
            state.g_waiting.set(state.waiting)
        state.running += 1
        state.g_running.set(state.running)
        queued = time.perf_counter() - started
        state.queue.observe(queued)
        session_id = payload.session_id
        context = '\n'.join(m.role + ':' + m.content for m in payload.messages)
        prompt = context + '\nuser:' + payload.prompt
        key = hashlib.sha256(prompt[:256].encode()).hexdigest()
        hit = key in state.cache
        state.cache_events.labels('hit' if hit else 'miss').inc()
        parts = []
        first = None
        finished = False
        released = False

        def release():
            nonlocal released
            if not released:
                released = True
                state.running -= 1
                state.g_running.set(state.running)
                state.semaphore.release()

        async def stream():
            nonlocal first, finished
            try:
                await spend_cpu(settings.hit_cpu if hit else settings.miss_cpu)
                result = state.backend.generate(prompt, payload.max_new_tokens)
                if settings.cache_size:
                    state.cache[key] = True
                    state.cache.move_to_end(key)
                    while len(state.cache) > settings.cache_size:
                        state.cache.popitem(last=False)
                answer = result['answer']
                width = max(1, math.ceil(len(answer) / payload.max_new_tokens))
                for i in range(payload.max_new_tokens):
                    if await request.is_disconnected():
                        return
                    await spend_cpu(settings.chunk_cpu)
                    if i:
                        await asyncio.sleep(settings.chunk_interval)
                    now = time.perf_counter()
                    if first is None:
                        first = now - started
                    part = answer[i * width:(i + 1) * width]
                    parts.append(part)
                    yield json.dumps({**identity, 'index': i, 'text': part,
                                      'queue_seconds': queued, 'cache_hit': hit}) + '\n'
                elapsed = time.perf_counter() - started
                finished = True
                state.ttfc.observe(first)
                state.total.observe(elapsed)
                terminal('completed', session_id=session_id, chunks=len(parts), ttfc_seconds=first,
                         queue_seconds=queued, cache_hit=hit)
                yield json.dumps({**identity, 'done': True, 'chunks': len(parts),
                                  'ttfc_seconds': first, 'queue_seconds': queued,
                                  'total_seconds': elapsed}) + '\n'
            finally:
                if not finished:
                    terminal('aborted', session_id=session_id, chunks=len(parts), queue_seconds=queued)
                release()

        if payload.stream:
            def on_close():
                if not released:
                    if not finished:
                        terminal('aborted', session_id=session_id, chunks=len(parts), queue_seconds=queued)
                    release()
            return ManagedStreamingResponse(stream(), media_type='application/x-ndjson', headers=headers,
                                            on_close=on_close)
        try:
            async for _ in stream():
                pass
            if not finished:
                return Response(status_code=499, headers=headers)
            return JSONResponse({**identity, 'answer': ''.join(parts), 'output_chunks': len(parts),
                                 'generation_seconds': time.perf_counter() - started - queued,
                                 'queue_seconds': queued, 'total_seconds': time.perf_counter() - started}, headers=headers)
        finally:
            release()

    return app
