"""Local ASGI checks. These do not count as Kubernetes evidence."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

import httpx
from fastapi.testclient import TestClient
from app.backend import SimulatedBackend, StubBackend
from app.main import create_app, Settings
from scripts.load import attempt, classify, schedule


def settings(**kwargs):
    return Settings(warmup=kwargs.pop('warmup', 0), hit_cpu=.001,
                    miss_cpu=.003, chunk_cpu=.001, chunk_interval=.001, **kwargs)


class ServiceTests(unittest.TestCase):
    def test_previous_backend_interface_and_checksums(self):
        root = Path(__file__).resolve().parents[1] / 'artifacts'
        hashes = json.loads((root / 'sha256.json').read_text())
        b = SimulatedBackend(root / 'A.json', hashes['A'], 'A')
        self.assertIsInstance(b, StubBackend)
        self.assertEqual(b.generate('marker', 2)['model_id'], 'A')
        with self.assertRaises(ValueError):
            SimulatedBackend(root / 'A.json', '0' * 64, 'A')
        with self.assertRaises(ValueError):
            SimulatedBackend(root / 'A.json', hashes['A'], 'B')

    def test_red_then_green_warmup(self):
        evidence = []
        for broken in (True, False):
            with TestClient(create_app(settings(warmup=.15, broken_probes=broken))) as c:
                startup = c.get('/startup')
                ready = c.get('/ready')
                response = c.post('/generate', json={'prompt': 'hello'})
                self.assertEqual(startup.status_code, 200 if broken else 503)
                self.assertEqual(ready.status_code, 200 if broken else 503)
                self.assertEqual(response.status_code, 503)
                evidence.append({'broken_probes': broken, 'startup_status': startup.status_code,
                                 'ready_status': ready.status_code, 'direct_request_status': response.status_code,
                                 'scope': 'in-process ASGI; does not prove Service routing'})
                time.sleep(.17)
                self.assertEqual(c.get('/ready').status_code, 200)
                self.assertEqual(c.post('/generate', json={'prompt': 'hello'}).status_code, 200)
        path = Path('results/local/warmup.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2))

    def test_bad_artifact_stays_unready_without_liveness_failure(self):
        with TestClient(create_app(settings(version='BAD'))) as c:
            time.sleep(.01)
            self.assertEqual(c.get('/live').status_code, 200)
            self.assertEqual(c.get('/ready').status_code, 503)
            self.assertEqual(c.post('/generate', json={'prompt': 'x'}).status_code, 503)

    def test_restart_recovers_same_version_and_client_history(self):
        hashes = []
        for _ in range(2):
            with TestClient(create_app(settings())) as c:
                time.sleep(.01)
                body = c.post('/generate', json={'prompt': 'continue',
                    'messages': [{'role': 'user', 'content': 'RECOVER-MARKER'}]}).json()
                self.assertIn('RECOVER-MARKER', body['answer'])
                self.assertEqual(body['version'], 'A')
                hashes.append(body['artifact_sha256'])
        self.assertEqual(hashes[0], hashes[1])

    def test_dialogue_isolation_and_old_api_contract(self):
        with TestClient(create_app(settings())) as c:
            time.sleep(.01)
            for own, other in [('ALPHA-PRIVATE', 'BETA-PRIVATE'), ('BETA-PRIVATE', 'ALPHA-PRIVATE')]:
                out = c.post('/generate', json={'prompt': 'continue', 'messages': [{'role': 'user', 'content': own}]}).json()
                self.assertIn(own, out['answer'])
                self.assertNotIn(other, out['answer'])
                self.assertEqual(out['backend_mode'], 'simulated')
            for payload in [{'prompt': ''}, {'prompt': 'x', 'max_new_tokens': True}, {'prompt': 'x', 'unknown': 1}]:
                self.assertEqual(c.post('/generate', json=payload).status_code, 400)
            self.assertEqual(c.post('/generate', content=b'x' * 65537).status_code, 413)

    def test_stream_has_terminal_frame_and_identity(self):
        with TestClient(create_app(settings())) as c:
            time.sleep(.01)
            response = c.post('/generate', json={'prompt': 'stream marker', 'stream': True, 'max_new_tokens': 3},
                              headers={'X-Request-ID': 'controlled-id'})
            rows = [json.loads(line) for line in response.text.splitlines()]
            self.assertEqual([r['index'] for r in rows[:-1]], [0, 1, 2])
            self.assertTrue(rows[-1]['done'])
            self.assertTrue(all(r['request_id'] == 'controlled-id' for r in rows))
            m = c.get('/metrics').text
            self.assertIn('hw11_requests_total{outcome="completed"} 1.0', m)
            self.assertNotIn('controlled-id', m)
            self.assertIn('hw11_ttfc_seconds_count 1.0', m)

    def test_drain_refuses_new_requests_and_preserves_liveness(self):
        with TestClient(create_app(settings())) as c:
            time.sleep(.01)
            c.post('/drain')
            self.assertEqual(c.get('/ready').status_code, 503)
            self.assertEqual(c.get('/live').status_code, 200)
            self.assertEqual(c.post('/generate', json={'prompt': 'x'}).status_code, 503)

    def test_client_accounting_catches_truncated_and_timed_out(self):
        self.assertEqual(classify(200, False, True), 'aborted')
        self.assertEqual(classify(200, True, True), 'completed')
        self.assertEqual(classify(429, False, False), 'rejected')
        self.assertEqual(classify(None, False, False, timeout=True), 'timed_out')
        self.assertEqual(classify(None, False, False), 'transport_error')

    def test_open_loop_schedule_reproducible_and_has_four_ips(self):
        a = schedule(120, 5, 'skew', 11)
        self.assertEqual(a, schedule(120, 5, 'skew', 11))
        counts = [sum(r['client'] == i for r in a) for i in range(4)]
        self.assertEqual(counts, [480, 40, 40, 40])
        self.assertEqual(len({r['offset'] for r in a}), len(a))


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_bound_rejects_and_recovers(self):
        app = create_app(settings(queue_capacity=0))
        async with app.router.lifespan_context(app):
            await asyncio.sleep(.01)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as c:
                first = asyncio.create_task(c.post('/generate', json={'prompt': 'a', 'max_new_tokens': 64}))
                await asyncio.sleep(.01)
                second = await c.post('/generate', json={'prompt': 'b'})
                self.assertEqual(second.status_code, 429)
                self.assertEqual((await first).status_code, 200)
                self.assertEqual(app.state.engine.running, 0)
                self.assertEqual(app.state.engine.waiting, 0)
                metrics = (await c.get('/metrics')).text
                self.assertIn('hw11_requests_total{outcome="rejected"} 1.0', metrics)

    async def test_queue_timeout_is_rejection_and_slot_is_not_leaked(self):
        app = create_app(settings(queue_timeout=.01))
        async with app.router.lifespan_context(app):
            await asyncio.sleep(.01)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as c:
                first = asyncio.create_task(c.post('/generate', json={'prompt': 'a', 'max_new_tokens': 64}))
                await asyncio.sleep(.005)
                result = await c.post('/generate', json={'prompt': 'b'})
                self.assertEqual(result.status_code, 429)
                self.assertEqual(result.json()['error'], 'queue_timeout')
                await first
                self.assertEqual(app.state.engine.waiting, 0)
                self.assertEqual(app.state.engine.running, 0)

    async def test_disconnect_counts_aborted_and_releases_admission(self):
        app = create_app(settings())
        async with app.router.lifespan_context(app):
            await asyncio.sleep(.01)
            body = json.dumps({'prompt': 'cancel', 'stream': True, 'max_new_tokens': 64}).encode()
            sent_body = False
            disconnected = asyncio.Event()
            response_chunks = []
            async def receive():
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {'type': 'http.request', 'body': body, 'more_body': False}
                await disconnected.wait()
                return {'type': 'http.disconnect'}
            async def send(message):
                if message['type'] == 'http.response.body' and message.get('body'):
                    response_chunks.append(message['body'])
                    disconnected.set()
            scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.0'},
                     'http_version': '1.1', 'method': 'POST', 'scheme': 'http',
                     'path': '/generate', 'raw_path': b'/generate', 'query_string': b'',
                     'headers': [(b'content-type', b'application/json')],
                     'client': ('127.0.0.1', 50000), 'server': ('local', 80)}
            await app(scope, receive, send)
            self.assertGreaterEqual(len(response_chunks), 1)
            self.assertEqual(app.state.engine.running, 0)
            text = app.state.engine.outcomes.labels('aborted')._value.get()
            self.assertEqual(text, 1)
            self.assertEqual(app.state.engine.ttfc._sum.get(), 0)

    async def test_response_start_failure_does_not_leak_slot(self):
        app = create_app(settings())
        async with app.router.lifespan_context(app):
            await asyncio.sleep(.01)
            sent = False
            async def receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {'type':'http.request','body':b'{"prompt":"x","stream":true}','more_body':False}
                await asyncio.sleep(10)
                return {'type':'http.disconnect'}
            async def send(message):
                raise OSError('client socket closed before response headers')
            scope = {'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},
                     'http_version':'1.1','method':'POST','scheme':'http','path':'/generate',
                     'raw_path':b'/generate','query_string':b'','headers':[],
                     'client':('127.0.0.1',12345),'server':('local',80)}
            try:
                await app(scope, receive, send)
            except Exception:
                pass
            self.assertEqual(app.state.engine.running,0)
            self.assertEqual(app.state.engine.waiting,0)
            self.assertEqual(app.state.engine.outcomes.labels('aborted')._value.get(),1)

    async def test_real_client_parser_detects_missing_terminal_frame(self):
        async def handler(request):
            return httpx.Response(200, headers={'x-pod': 'pod-a', 'x-model-version': 'A'},
                text=json.dumps({'request_id': 'r', 'index': 0, 'text': 'partial'})+'\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            now = time.perf_counter()
            result = await attempt(c, 'http://local', {'prompt': 'a', 'max_new_tokens': 2}, 'r', now, time.time(), 1)
        self.assertEqual(result['outcome'], 'aborted')
        self.assertEqual(result['chunks'], 1)
        self.assertIsNone(result['inter_chunk_seconds'][0] if result['inter_chunk_seconds'] else None)


if __name__ == '__main__':
    unittest.main()
