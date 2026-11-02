"""FastAPI entry point. Same CLI and HTTP contracts as the investigation adapter."""
import argparse
from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from backends import StubBackend, TransformersBackend
from service_state import ServiceState, ServiceUnavailable


def create_app(root, warmup_seconds=0, backend_factory=StubBackend, debug=False):
    @asynccontextmanager
    async def lifespan(app):
        state = ServiceState(Path(root), warmup_seconds, backend_factory)
        app.state.model = state
        try:
            yield
        finally:
            state.close()
    app = FastAPI(lifespan=lifespan)

    @app.get('/health')
    def health():
        return {'alive': True, 'backend': backend_factory.label}

    @app.get('/ready')
    def ready():
        state = app.state.model
        return JSONResponse({'ready': state.ready}, status_code=200 if state.ready else 503)

    if debug:
        @app.get('/debug')
        def debug_state():
            return app.state.model.debug()

    @app.post('/generate')
    async def generate(request: Request):
        # Bounded streamed read; never buffer an arbitrarily large body.
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 65536:
                return JSONResponse({'error': 'body too large'}, status_code=413)
        try:
            payload = json.loads(data)
            result = await run_in_threadpool(app.state.model.generate, payload)
            return JSONResponse(result)
        except ServiceUnavailable as exc:
            return JSONResponse({'error': str(exc)}, status_code=503)
        except (ValueError, TypeError, KeyError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, default=Path('.lab'))
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--warmup-seconds', type=float, default=0)
    parser.add_argument('--backend', choices=['stub', 'transformers'], default='stub')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()
    factory = StubBackend if args.backend == 'stub' else TransformersBackend
    # /debug is available automatically only on the synthetic test stand.
    debug = args.debug or args.backend == 'stub'
    uvicorn.run(create_app(args.workspace, args.warmup_seconds, factory, debug),
                host='127.0.0.1', port=args.port, log_level='info')


if __name__ == '__main__':
    main()
