"""Fake app server — a stand-in for Person 1's real app servers.

It answers in EXACTLY the shapes from docs/contracts.md (Contracts 1–4), so the gateway,
Prometheus, Grafana and Locust can all be built and tested before the real servers exist.

Extra: a /chaos endpoint lets tests make this server slow, broken, or "dead" on purpose.

Run locally:
    SERVER_ID=app-1 uvicorn fakes.fake_app:app --port 8001
"""

import asyncio
import os
import random
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

SERVICE = "app"
CHAOS_MODES = {"ok", "slow", "error", "down"}

# Buckets in seconds. Small ones for normal requests, big ones so "slow" mode is visible.
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class FakeServerState:
    """Everything that can change while the server runs."""

    def __init__(self, server_id: str, ready_delay_sec: float, base_latency_ms: float):
        self.server_id = server_id
        self.ready_delay_sec = ready_delay_sec
        self.base_latency_ms = base_latency_ms
        self.mode = "ok"
        self.slow_latency_ms = 800.0
        self.error_rate = 1.0
        self.started_at = time.monotonic()

    def restart_clock(self) -> None:
        """Pretend we just restarted: not ready until warm-up finishes."""
        self.started_at = time.monotonic()

    @property
    def warming_up(self) -> bool:
        return time.monotonic() - self.started_at < self.ready_delay_sec


def create_app(
    server_id: str | None = None,
    ready_delay_sec: float | None = None,
    base_latency_ms: float | None = None,
) -> FastAPI:
    state = FakeServerState(
        server_id=server_id or os.getenv("SERVER_ID", "app-1"),
        ready_delay_sec=float(
            ready_delay_sec if ready_delay_sec is not None else os.getenv("READY_DELAY_SEC", "3")
        ),
        base_latency_ms=float(
            base_latency_ms if base_latency_ms is not None else os.getenv("BASE_LATENCY_MS", "20")
        ),
    )

    # Each app gets its own registry so tests can create many apps without name clashes.
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
    in_flight = Gauge(
        "app_in_flight_requests",
        "Requests being handled right now",
        ["server_id"],
        registry=registry,
    )

    app = FastAPI(title=f"Fake app server ({state.server_id})")
    app.state.fake = state

    # ---------- Contract 1: normal work ----------
    async def do_work(request: Request) -> JSONResponse:
        priority = request.headers.get("X-Priority", "normal").lower()
        if priority not in {"critical", "normal"}:
            priority = "normal"

        start = time.perf_counter()
        in_flight.labels(state.server_id).inc()
        try:
            if state.mode == "down":
                status, body = (
                    503,
                    {
                        "server_id": state.server_id,
                        "status": "error",
                        "data": None,
                        "processed_ms": 0.0,
                    },
                )
            else:
                # Random jitter (±50%) so graphs look like real traffic, not a flat line.
                delay_ms = state.base_latency_ms * random.uniform(0.5, 1.5)
                if state.mode == "slow":
                    delay_ms += state.slow_latency_ms
                await asyncio.sleep(delay_ms / 1000)

                if state.mode == "error" and random.random() < state.error_rate:
                    status, data, result = 500, None, "error"
                else:
                    status, data, result = 200, "hello", "ok"
                body = {
                    "server_id": state.server_id,
                    "status": result,
                    "data": data,
                    "processed_ms": round((time.perf_counter() - start) * 1000, 1),
                }
        finally:
            in_flight.labels(state.server_id).dec()

        requests_total.labels(SERVICE, state.server_id, str(status), priority).inc()
        request_duration.labels(SERVICE, state.server_id).observe(time.perf_counter() - start)
        return JSONResponse(body, status_code=status)

    app.add_api_route("/", do_work, methods=["GET"])
    app.add_api_route("/work", do_work, methods=["GET"])

    # ---------- Contract 2: liveness ----------
    @app.get("/health")
    async def health():
        if state.mode == "down":
            return JSONResponse({"server_id": state.server_id, "status": "dead"}, status_code=503)
        return {"server_id": state.server_id, "status": "alive"}

    # ---------- Contract 3: readiness ----------
    @app.get("/ready")
    async def ready():
        if state.mode == "down":
            reason = "shutting_down"
        elif state.warming_up:
            reason = "warming_up"
        elif state.mode == "slow":
            reason = "overloaded"
        else:
            reason = None
        body = {"server_id": state.server_id, "ready": reason is None, "reason": reason}
        return JSONResponse(body, status_code=200 if reason is None else 503)

    # ---------- Contract 4: metrics ----------
    @app.get("/metrics")
    async def metrics():
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    # ---------- Test-only: chaos switch ----------
    @app.get("/chaos")
    async def chaos_status():
        return {
            "server_id": state.server_id,
            "mode": state.mode,
            "slow_latency_ms": state.slow_latency_ms,
            "error_rate": state.error_rate,
        }

    @app.post("/chaos/{mode}")
    async def set_chaos(
        mode: str, latency_ms: float | None = None, error_rate: float | None = None
    ):
        if mode not in CHAOS_MODES:
            raise HTTPException(400, f"mode must be one of {sorted(CHAOS_MODES)}")
        was_down = state.mode == "down"
        state.mode = mode
        if latency_ms is not None:
            state.slow_latency_ms = latency_ms
        if error_rate is not None:
            state.error_rate = max(0.0, min(1.0, error_rate))
        if was_down and mode == "ok":
            state.restart_clock()  # coming back from "dead" = a restart, so warm up again
        return await chaos_status()

    return app


app = create_app()
