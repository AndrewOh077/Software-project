"""ResiliGuard's controller-compatible traffic gateway."""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel


REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "3"))
BACKENDS = {
    "app1": os.getenv("APP1_URL", "http://app1:8000"),
    "app2": os.getenv("APP2_URL", "http://app2:8000"),
    "app3": os.getenv("APP3_URL", "http://app3:8000"),
}
RATE_LIMIT_RPS = float(os.getenv("RATE_LIMIT_RPS", "50"))
RATE_LIMIT_BURST = float(os.getenv("RATE_LIMIT_BURST", "100"))
LOW_PRIORITY_RESERVE = float(os.getenv("LOW_PRIORITY_RESERVE", "0.5"))
CONCURRENCY_PER_BACKEND = float(os.getenv("CONCURRENCY_PER_BACKEND", "30"))
AIMD_INCREASE_PER_S = float(os.getenv("AIMD_INCREASE_PER_S", "5"))
AIMD_DECREASE_FACTOR = float(os.getenv("AIMD_DECREASE_FACTOR", "0.5"))


class TokenBucket:
    """Rate limit. critical requests are never limited; low is shed first."""

    def __init__(self, rate: float, capacity: float) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.updated: float | None = None

    def allow(self, priority: str = "normal", now: float | None = None) -> bool:
        if priority == "critical":
            return True
        now = time.monotonic() if now is None else now
        if self.updated is not None:
            self.tokens = min(self.capacity, self.tokens + max(0.0, now - self.updated) * self.rate)
        self.updated = now
        reserve = LOW_PRIORITY_RESERVE * self.capacity if priority == "low" else 0.0
        if self.tokens - 1 >= reserve:
            self.tokens -= 1
            return True
        return False


class AimdLimiter:
    """Concurrency limit. Shrinks at once when capacity drops, grows back additively,
    and is cut by a factor when a backend fails (additive increase, multiplicative decrease)."""

    def __init__(self, per_backend: float, increase_per_s: float, decrease_factor: float, minimum: float = 1.0) -> None:
        self.per_backend = per_backend
        self.increase_per_s = increase_per_s
        self.decrease_factor = decrease_factor
        self.minimum = minimum
        self.maximum = per_backend * len(BACKENDS)
        self.limit = self.maximum
        self.inflight = 0
        self.updated: float | None = None

    def ceiling(self, backends: dict) -> float:
        capacity = sum(item["weight"] / 100 for item in backends.values() if item["enabled"])
        return max(self.minimum, self.per_backend * capacity)

    def refresh(self, backends: dict, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        elapsed = 0.0 if self.updated is None else max(0.0, now - self.updated)
        self.updated = now
        ceiling = self.ceiling(backends)
        if self.limit > ceiling:
            self.limit = ceiling
        else:
            self.limit = min(ceiling, self.limit + self.increase_per_s * elapsed)
        return self.limit

    def try_acquire(self, backends: dict, now: float | None = None) -> bool:
        self.refresh(backends, now)
        if self.inflight >= int(self.limit):
            return False
        self.inflight += 1
        return True

    def release(self, failed: bool = False) -> None:
        self.inflight = max(0, self.inflight - 1)
        if failed:
            self.limit = max(self.minimum, self.limit * self.decrease_factor)

class WeightUpdate(BaseModel):
    weight: int


class ModeUpdate(BaseModel):
    mode: Literal["normal", "emergency"]


class GatewayState:
    """Controller-owned routing state. Never changes itself after a backend error."""

    def __init__(self) -> None:
        self.mode = "normal"
        self.backends = {name: {"enabled": True, "weight": 100} for name in BACKENDS}
        self.calls: deque[dict] = deque(maxlen=200)
        self._pool: list[str] = []
        self._index = 0
        self._lock = asyncio.Lock()
        self._rebuild_pool()

    def _rebuild_pool(self) -> None:
        self._pool = [name for name, item in self.backends.items() if item["enabled"] for _ in range(item["weight"])]
        self._index = 0

    def payload(self) -> dict:
        # Do not add fields inside an individual backend: Controller tests compare it exactly.
        return {"mode": self.mode, "backends": {name: item.copy() for name, item in self.backends.items()}}

    async def choose(self) -> str | None:
        async with self._lock:
            if not self._pool:
                return None
            name = self._pool[self._index % len(self._pool)]
            self._index = (self._index + 1) % len(self._pool)
            return name

    async def update_backend(self, name: str, action: str, body: dict, path: str) -> dict:
        async with self._lock:
            item = self.backends.get(name)
            if item is None:
                raise HTTPException(status_code=404, detail=f"unknown backend {name}")
            if action == "disable":
                item["enabled"] = False
            elif action == "enable":
                item["enabled"] = True
            elif action == "weight":
                weight = body.get("weight")
                if isinstance(weight, bool) or not isinstance(weight, int) or not 0 <= weight <= 100:
                    raise HTTPException(status_code=400, detail="weight must be an int 0-100")
                item["weight"] = weight
            else:
                raise HTTPException(status_code=404, detail="unknown action")
            self._rebuild_pool()
            self.calls.append({"time": round(time.time(), 3), "path": path, "body": body})
            return self.payload()

    async def update_mode(self, mode: str, path: str, body: dict) -> dict:
        async with self._lock:
            self.mode = mode
            self.calls.append({"time": round(time.time(), 3), "path": path, "body": body})
            return self.payload()


state = GatewayState()
bucket = TokenBucket(RATE_LIMIT_RPS, RATE_LIMIT_BURST)
limiter = AimdLimiter(CONCURRENCY_PER_BACKEND, AIMD_INCREASE_PER_S, AIMD_DECREASE_FACTOR)
app = FastAPI(title="ResiliGuard Gateway", description="Controller-compatible traffic gateway", version="1.0.0")


@app.get("/")
async def home() -> dict[str, str]:
    return {"message": "Welcome to ResiliGuard Gateway"}


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/route")
async def choose_route() -> dict[str, str]:
    name = await state.choose()
    if name is None:
        raise HTTPException(status_code=503, detail="no enabled backend with positive weight")
    return {"selected_backend": name, "url": BACKENDS[name]}


async def _forward(request: Request) -> Response:
    tried: set[str] = set()
    while len(tried) < len(BACKENDS):
        name = await state.choose()
        if name is None or name in tried:
            break
        tried.add(name)
        url = f"{BACKENDS[name]}/work"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                backend_response = await client.get(url, params=list(request.query_params.multi_items()))
            content_type = backend_response.headers.get("content-type", "application/octet-stream")
            return Response(content=backend_response.content, status_code=backend_response.status_code, headers={"content-type": content_type})
        except httpx.RequestError:
            # A transport error may fail over, but only Controller commands alter enabled/weight.
            continue
    raise HTTPException(status_code=503, detail="no reachable enabled backend")


@app.get("/work")
async def forward_work(request: Request, priority: str = "normal") -> Response:
    critical = priority == "critical"
    if state.mode == "emergency" and not critical:
        raise HTTPException(status_code=503, detail="emergency mode accepts critical requests only")
    if not critical:
        if not bucket.allow(priority):
            raise HTTPException(status_code=429, detail="rate limit exceeded", headers={"Retry-After": "1"})
        if not limiter.try_acquire(state.backends):
            raise HTTPException(status_code=429, detail="concurrency limit reached", headers={"Retry-After": "1"})
    failed = False
    try:
        response = await _forward(request)
        failed = response.status_code >= 500
        return response
    except HTTPException:
        failed = True
        raise
    finally:
        if not critical:
            limiter.release(failed)

@app.get("/admin/backends")
async def backend_status() -> dict:
    return state.payload()


@app.post("/admin/backends/{name}/disable")
async def disable_backend(name: str, request: Request) -> dict:
    return await state.update_backend(name, "disable", {}, request.url.path)


@app.post("/admin/backends/{name}/enable")
async def enable_backend(name: str, request: Request) -> dict:
    return await state.update_backend(name, "enable", {}, request.url.path)


@app.post("/admin/backends/{name}/weight")
async def set_weight(name: str, update: WeightUpdate, request: Request) -> dict:
    return await state.update_backend(name, "weight", update.model_dump(), request.url.path)


@app.post("/admin/mode")
async def set_mode(update: ModeUpdate, request: Request) -> dict:
    return await state.update_mode(update.mode, request.url.path, update.model_dump())


@app.get("/admin/calls")
async def admin_calls() -> list[dict]:
    return list(state.calls)


@app.get("/metrics")
async def metrics() -> JSONResponse:
    return JSONResponse({
        "mode": state.mode,
        "enabled_backends": sum(1 for item in state.backends.values() if item["enabled"] and item["weight"] > 0),
        "concurrency_limit": round(limiter.refresh(state.backends), 1),
        "concurrency_max": limiter.maximum,
        "inflight": limiter.inflight,
    })