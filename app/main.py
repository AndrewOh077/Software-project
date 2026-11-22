"""
Backend app server.

The same image runs as app1, app2 and app3 (SERVER_ID tells them apart).
Exposes Prometheus metrics, plus a fault-injection API used for chaos tests later.
"""

import asyncio
import os
import random
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field

SERVER_ID = os.getenv("SERVER_ID", "unknown")
# Simulated warm-up: /ready returns 503 for this many seconds after start
# (like loading caches / connecting to a DB in a real service).
READY_DELAY_S = float(os.getenv("READY_DELAY_S", "3"))
STARTED_AT = time.monotonic()
# A "broken for good" fault that survives restarts (like a bad config file or a full disk).
# chaos.py `break` creates this file; `fix` deletes it. Restarting does NOT help while it exists.
BROKEN_FILE = os.path.join(os.getenv("FAULT_DIR", "/faults"), f"{SERVER_ID}.broken")


def broken_for_good() -> bool:
    return os.path.exists(BROKEN_FILE)

app = FastAPI(title=f"app-{SERVER_ID}")

# ---------- Metrics ----------
REQUESTS = Counter(
    "app_requests_total",
    "Total HTTP requests",
    ["server", "path", "status"],
)
LATENCY = Histogram(
    "app_request_latency_seconds",
    "Request latency in seconds",
    ["server", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
IN_FLIGHT = Gauge(
    "app_in_flight_requests",
    "Requests currently being processed",
    ["server"],
)
FAULT_LATENCY = Gauge("app_fault_latency_ms", "Injected extra latency (ms)", ["server"])
FAULT_ERROR_RATE = Gauge("app_fault_error_rate", "Injected error rate (0-1)", ["server"])


# ---------- Fault injection state ----------
class FaultConfig(BaseModel):
    latency_ms: int = Field(0, ge=0, le=10_000, description="Extra delay added to /work")
    error_rate: float = Field(0.0, ge=0.0, le=1.0, description="Fraction of /work calls that return 500")
    unhealthy: bool = Field(False, description="If true, /health and /ready return 503")
    not_ready: bool = Field(False, description="If true, only /ready returns 503 (alive but can't serve)")


fault = FaultConfig()


def _publish_fault_metrics() -> None:
    FAULT_LATENCY.labels(SERVER_ID).set(fault.latency_ms)
    FAULT_ERROR_RATE.labels(SERVER_ID).set(fault.error_rate)


_publish_fault_metrics()


# ---------- Middleware: record metrics for every request ----------
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/metrics":
        return await call_next(request)

    IN_FLIGHT.labels(SERVER_ID).inc()
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        LATENCY.labels(SERVER_ID, path).observe(time.perf_counter() - start)
        REQUESTS.labels(SERVER_ID, path, str(status)).inc()
        IN_FLIGHT.labels(SERVER_ID).dec()


# ---------- Endpoints ----------
@app.get("/")
async def root():
    return {"server": SERVER_ID, "message": "hello"}


@app.get("/health")
async def health():
    """Liveness: is the process alive? If this fails, restarting helps."""
    if broken_for_good():
        raise HTTPException(status_code=503, detail="broken (persistent fault, restart won't help)")
    if fault.unhealthy:
        raise HTTPException(status_code=503, detail="unhealthy (injected)")
    return {"server": SERVER_ID, "status": "ok"}


@app.get("/ready")
async def ready():
    """Readiness: can this server take traffic right now? If this fails, stop sending it requests."""
    if broken_for_good() or fault.unhealthy:
        raise HTTPException(status_code=503, detail="unhealthy (injected)")
    warming = time.monotonic() - STARTED_AT < READY_DELAY_S
    if warming:
        raise HTTPException(status_code=503, detail="warming up")
    if fault.not_ready:
        raise HTTPException(status_code=503, detail="not ready (injected)")
    return {"server": SERVER_ID, "status": "ready"}


@app.get("/work")
async def work(ms: int = 50, priority: str = "normal"):
    """
    Simulated workload. `ms` = base processing time.
    `priority` is just echoed for now; the gateway will use it in week 3.
    """
    ms = max(0, min(ms, 10_000))
    # base work + a little jitter + injected fault latency
    delay = (ms + random.uniform(0, ms * 0.2) + fault.latency_ms) / 1000
    await asyncio.sleep(delay)

    if random.random() < fault.error_rate:
        raise HTTPException(status_code=500, detail="error (injected)")

    return {"server": SERVER_ID, "priority": priority, "took_ms": round(delay * 1000, 1)}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------- Admin: fault injection ----------
@app.get("/admin/fault")
async def get_fault():
    return {"server": SERVER_ID, **fault.model_dump()}


@app.post("/admin/fault")
async def set_fault(cfg: FaultConfig):
    global fault
    fault = cfg
    _publish_fault_metrics()
    return {"server": SERVER_ID, **fault.model_dump()}


@app.delete("/admin/fault")
async def clear_fault():
    global fault
    fault = FaultConfig()
    _publish_fault_metrics()
    return {"server": SERVER_ID, **fault.model_dump()}
