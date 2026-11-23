"""Measure gateway response capacity against a running local or staging gateway."""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx


async def main() -> None:
    parser = argparse.ArgumentParser(description="ResiliGuard gateway capacity measurement")
    parser.add_argument("--url", default="http://localhost:8080/work")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--output", default="gateway-capacity-results.json")
    args = parser.parse_args()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def request(client: httpx.AsyncClient) -> int:
        async with semaphore:
            try:
                return (await client.get(args.url, headers={"X-Priority": "normal"})).status_code
            except httpx.RequestError:
                return 0

    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=10) as client:
        statuses = await asyncio.gather(*(request(client) for _ in range(args.requests)))
    elapsed = time.perf_counter() - started
    result = {
        "url": args.url,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(args.requests / elapsed, 2),
        "status_counts": {str(code): statuses.count(code) for code in sorted(set(statuses))},
    }
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
