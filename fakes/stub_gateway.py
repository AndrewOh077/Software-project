"""Stub gateway — a TEST DOUBLE, not the real gateway.

Person 2 owns the real gateway (gateway/). This stub exists only so Person 3 can build
dashboards and load tests in Week 1–3 before the real one is ready. It speaks the same
contracts (docs/contracts.md: Contract 4 metric names + Contract 5 errors / admin status),
so when the real gateway arrives, Prometheus/Grafana/Locust keep working unchanged.

What it does (deliberately simple):
  - round robin over backends that passed their last health checks
  - background health check on /ready every HEALTH_CHECK_INTERVAL_SEC
  - optional fixed per-second rate limit (RATE_LIMIT_RPS, 0 = off) to produce 429s
  - logs backend_marked_unhealthy / backend_marked_healthy to events.jsonl

Run locally:
    BACKENDS="app-1=http://localhost:8001,app-2=http://localhost:8002" \
        uvicorn fakes.stub_gateway:app --port 8000
"""

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from fakes.events import write_event

SERVICE = "gateway"
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


@dataclass
class Backend:
    server_id: str
    url: str
    healthy: bool = True
    ready: bool = True
    consecutive_failures: int = 0

    @property
    def usable(self) -> bool:
        return self.healthy and self.ready


def parse_backends(spec: str) -> list[Backend]:
    """'app-1=http://a:8000,app-2=http://b:8000' -> [Backend(...), Backend(...)]"""
    backends = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        server_id, url = part.split("=", 1)
        backends.append(Backend(server_id.strip(), url.strip().rstrip("/")))
    return backends


class FixedWindowLimiter:
    """At most `rps` requests per wall-clock second. rps <= 0 means unlimited.

    The real gateway will use a token bucket (Week 4, Person 2). This is the simplest
    thing that produces realistic 429s for the spike scenario.
    """

    def __init__(self, rps: int):
        self.rps = rps
        self.window = int(time.time())
        self.count = 0

    def allow(self) -> bool:
        if self.rps <= 0:
            return True
        now = int(time.time())
        if now != self.window:
            self.window, self.count = now, 0
        if self.count >= self.rps:
            return False
        self.count += 1
        return True


def rejection(status: int, error: str, message: str, retry_after: int = 1) -> JSONResponse:
    """Contract 5a."""
    return JSONResponse(
        {"error": error, "message": message, "retry_after_sec": retry_after},
        status_code=status,
        headers={"Retry-After": str(retry_after)},
    )


def create_app(
    backends: list[Backend] | None = None,
    rate_limit_rps: int | None = None,
    fail_threshold: int | None = None,
    check_interval_sec: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    run_health_checks: bool = True,
) -> FastAPI:
    backends = (
        backends
        if backends is not None
        else parse_backends(
            os.getenv(
                "BACKENDS",
                "app-1=http://app-1:8000,app-2=http://app-2:8000,app-3=http://app-3:8000",
            )
        )
    )
    limiter = FixedWindowLimiter(
        rate_limit_rps if rate_limit_rps is not None else int(os.getenv("RATE_LIMIT_RPS", "0"))
    )
    fail_threshold = fail_threshold or int(os.getenv("HEALTH_CHECK_FAIL_THRESHOLD", "3"))
    check_interval_sec = check_interval_sec or float(os.getenv("HEALTH_CHECK_INTERVAL_SEC", "2"))

    registry = CollectorRegistry()
    requests_total = Counter(
        "http_requests_total",
        "Requests handled",
        ["service", "server_id", "status_code", "priority"],
        registry=registry,
    )
    request_duration = Histogram(
        "http_request_duration_seconds",
        "Time spent handling a request",
        ["service", "server_id"],
        buckets=LATENCY_BUCKETS,
        registry=registry,
    )
    backend_healthy = Gauge(
        "gateway_backend_healthy",
        "1 if the backend can take traffic",
        ["server_id"],
        registry=registry,
    )
    rejected_total = Counter(
        "gateway_rejected_total",
        "Requests the gateway said no to",
        ["reason"],
        registry=registry,
    )
    queue_length = Gauge(
        "gateway_queue_length",
        "Waiting requests (stub has no queue: always 0)",
        ["priority"],
        registry=registry,
    )
    rate_limit_current = Gauge(
        "gateway_rate_limit_current",
        "Current requests/sec limit (0 = unlimited)",
        registry=registry,
    )

    for b in backends:
        backend_healthy.labels(b.server_id).set(1 if b.usable else 0)
    for p in ("critical", "normal"):
        queue_length.labels(p).set(0)
    for reason in ("rate_limited", "queue_full", "no_backend", "circuit_open"):
        rejected_total.labels(reason)  # create at 0 so Grafana shows the series right away
    rate_limit_current.set(limiter.rps)

    rr_index = 0

    def next_backend(skip: set[str]) -> Backend | None:
        """Round robin: start after the last one we used, take the first usable."""
        nonlocal rr_index
        for i in range(len(backends)):
            b = backends[(rr_index + i) % len(backends)]
            if b.usable and b.server_id not in skip:
                rr_index = (rr_index + i + 1) % len(backends)
                return b
        return None

    marked_down: set[str] = set()  # logged as unhealthy, not yet logged as recovered

    def set_backend_state(b: Backend, ok: bool, ready: bool) -> None:
        if ok:
            b.consecutive_failures = 0
            b.healthy, b.ready = True, ready
        else:
            b.consecutive_failures += 1
            b.ready = False
            if b.consecutive_failures >= fail_threshold:
                b.healthy = False

        # Events only for real failures/recoveries. "Warming up" (alive, not ready yet)
        # just pauses traffic silently, so startup doesn't pollute the MTTR log.
        if not ok and b.server_id not in marked_down:
            marked_down.add(b.server_id)
            write_event(
                "gateway",
                "backend_marked_unhealthy",
                b.server_id,
                consecutive_failures=b.consecutive_failures,
            )
        elif b.usable and b.server_id in marked_down:
            marked_down.discard(b.server_id)
            write_event("gateway", "backend_marked_healthy", b.server_id)
        backend_healthy.labels(b.server_id).set(1 if b.usable else 0)

    async def check_once(client: httpx.AsyncClient) -> None:
        async def check(b: Backend):
            try:
                r = await client.get(f"{b.url}/ready", timeout=1.0)
                # Alive-but-not-ready (warming up / overloaded) is NOT a failure:
                # stop sending traffic, but don't count it toward "dead".
                alive = r.status_code == 200 or (
                    r.status_code == 503 and r.json().get("reason") in {"warming_up", "overloaded"}
                )
                set_backend_state(b, ok=alive, ready=r.status_code == 200)
            except (httpx.HTTPError, ValueError):
                set_backend_state(b, ok=False, ready=False)

        await asyncio.gather(*(check(b) for b in backends))

    async def health_loop(client: httpx.AsyncClient) -> None:
        while True:
            await check_once(client)
            await asyncio.sleep(check_interval_sec)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(transport=transport) as client:
            app.state.client = client
            task = asyncio.create_task(health_loop(client)) if run_health_checks else None
            yield
            if task:
                task.cancel()

    app = FastAPI(title="Stub gateway (test double)", lifespan=lifespan)
    app.state.backends = backends
    app.state.check_once = check_once

    # ---------- Admin (Contract 5b) ----------
    @app.get("/admin/status")
    async def admin_status():
        return {
            "mode": "stub",
            "emergency_mode": False,
            "rate_limit_current": limiter.rps,
            "backends": [
                {
                    "server_id": b.server_id,
                    "url": b.url,
                    "healthy": b.healthy,
                    "ready": b.ready,
                    "consecutive_failures": b.consecutive_failures,
                    "circuit": "closed",
                }
                for b in backends
            ],
            "queue": {"critical": 0, "normal": 0},
        }

    @app.get("/health")
    async def health():
        return {"server_id": "gateway", "status": "alive"}

    @app.get("/metrics")
    async def metrics():
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    # ---------- Everything else is forwarded ----------
    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def forward(path: str, request: Request):
        priority = request.headers.get("X-Priority", "normal").lower()
        if priority not in {"critical", "normal"}:
            priority = "normal"
        start = time.perf_counter()

        def record(status: int, server_id: str) -> None:
            requests_total.labels(SERVICE, server_id, str(status), priority).inc()
            request_duration.labels(SERVICE, server_id).observe(time.perf_counter() - start)

        if not limiter.allow():
            rejected_total.labels("rate_limited").inc()
            record(429, "none")
            return rejection(429, "rate_limited", "Too many requests, try again shortly")

        tried: set[str] = set()
        for _ in range(2):  # original try + one retry on a different backend
            b = next_backend(skip=tried)
            if b is None:
                break
            tried.add(b.server_id)
            try:
                r = await request.app.state.client.request(
                    request.method,
                    f"{b.url}/{path}",
                    headers={"X-Priority": priority},
                    timeout=5.0,
                )
            except httpx.HTTPError:
                set_backend_state(b, ok=False, ready=False)
                continue
            record(r.status_code, b.server_id)
            return Response(
                r.content, status_code=r.status_code, media_type=r.headers.get("content-type")
            )

        rejected_total.labels("no_backend").inc()
        record(503, "none")
        return rejection(503, "no_backend", "No ready app servers")

    return app


app = create_app()
