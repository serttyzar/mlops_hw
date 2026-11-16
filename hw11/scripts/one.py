"""One long stream with a first-chunk marker for rollout/failure coordination."""
import argparse
import asyncio
import json
from pathlib import Path
import time
import httpx
from load import attempt


async def run(args):
    async with httpx.AsyncClient(trust_env=False) as c:
        row = await attempt(c, args.url, {'prompt': args.prompt, 'stream': True,
                            'max_new_tokens': args.chunks}, args.id, time.perf_counter(), time.time(), 65,
                            on_first=lambda: Path(args.output + '.started').write_text(str(time.time())))
    Path(args.output).write_text(json.dumps(row) + '\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--url', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--prompt', default='LONG-STREAM')
    p.add_argument('--chunks', type=int, default=64)
    p.add_argument('--id', required=True)
    asyncio.run(run(p.parse_args()))
