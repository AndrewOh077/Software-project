"""Stub gateway: round robin, failover, Contract 5 bodies, and the metrics Grafana reads.

No network: httpx.MockTransport plays the three app servers.
"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes.stub_gateway import Backend, create_app


class FakeBackends:
    """Pretend app servers. Set .down = {"app-2"} to make app-2 refuse connections."""

    def __init__(self):
        self.down: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        server_id = request.url.host  # we use http://app-1 etc.
        if server_id in self.down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/ready":
            return httpx.Response(200, json={"server_id": server_id, "ready": True, "reason": None})
        return httpx.Response(
            200, json={"server_id": server_id, "status": "ok", "data": "hello", "processed_ms": 1.0}
        )


@pytest.fixture
def fake_backends():
    return FakeBackends()


def make_client(fake_backends, **kwargs):
    backends = [Backend(f"app-{i}", f"http://app-{i}") for i in (1, 2, 3)]
    app = create_app(
        backends=backends,
        transport=httpx.MockTransport(fake_backends.handler),
        run_health_checks=False,
        fail_threshold=3,
        **kwargs,
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def events_file(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("EVENTS_FILE", str(path))
    return path


def served_by(client, n):
    return [client.get("/work").json()["server_id"] for _ in range(n)]


def run_health_check(client):
    asyncio.run(client.app.state.check_once(client.app.state.client))


def test_round_robin(fake_backends):
    with make_client(fake_backends) as c:
        assert served_by(c, 6) == ["app-1", "app-2", "app-3", "app-1", "app-2", "app-3"]


def test_dead_backend_is_skipped_by_health_check(fake_backends, events_file):
    with make_client(fake_backends) as c:
        fake_backends.down = {"app-2"}
        run_health_check(c)
        assert served_by(c, 4) == ["app-1", "app-3", "app-1", "app-3"]

        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        assert events[-1]["event"] == "backend_marked_unhealthy"
        assert events[-1]["server_id"] == "app-2"


def test_request_retries_on_another_backend_when_one_fails(fake_backends):
    with make_client(fake_backends) as c:
        fake_backends.down = {"app-1"}  # health check hasn't noticed yet
        r = c.get("/work")
        assert r.status_code == 200
        assert r.json()["server_id"] == "app-2"


def test_backend_comes_back(fake_backends, events_file):
    with make_client(fake_backends) as c:
        fake_backends.down = {"app-2"}
        for _ in range(3):
            run_health_check(c)
        status = c.get("/admin/status").json()
        app2 = next(b for b in status["backends"] if b["server_id"] == "app-2")
        assert app2["healthy"] is False and app2["consecutive_failures"] == 3

        fake_backends.down = set()
        run_health_check(c)
        assert "app-2" in served_by(c, 3)
        assert (
            json.loads(events_file.read_text().splitlines()[-1])["event"]
            == "backend_marked_healthy"
        )


def test_contract5a_no_backend(fake_backends):
    with make_client(fake_backends) as c:
        fake_backends.down = {"app-1", "app-2", "app-3"}
        run_health_check(c)
        r = c.get("/work")
        assert r.status_code == 503
        assert r.json() == {
            "error": "no_backend",
            "message": "No ready app servers",
            "retry_after_sec": 1,
        }
        assert r.headers["Retry-After"] == "1"


def test_contract5a_rate_limited(fake_backends):
    with make_client(fake_backends, rate_limit_rps=2) as c:
        codes = [c.get("/work").status_code for _ in range(5)]
        assert codes.count(200) == 2  # same second -> only 2 allowed
        assert 429 in codes
        r = c.get("/work")
        assert r.json()["error"] == "rate_limited"


def test_contract5b_admin_status_shape(fake_backends):
    with make_client(fake_backends) as c:
        s = c.get("/admin/status").json()
        assert set(s) == {"mode", "emergency_mode", "rate_limit_current", "backends", "queue"}
        assert set(s["backends"][0]) == {
            "server_id",
            "url",
            "healthy",
            "ready",
            "consecutive_failures",
            "circuit",
        }
        assert s["queue"] == {"critical": 0, "normal": 0}


def test_gateway_metrics_for_grafana(fake_backends):
    with make_client(fake_backends, rate_limit_rps=1) as c:
        c.get("/work", headers={"X-Priority": "critical"})
        c.get("/work")  # rejected
        text = c.get("/metrics").text
    assert (
        'http_requests_total{priority="critical",server_id="app-1",service="gateway",'
        'status_code="200"} 1.0'
    ) in text
    assert 'gateway_rejected_total{reason="rate_limited"} 1.0' in text
    assert 'gateway_backend_healthy{server_id="app-3"} 1.0' in text
    assert "gateway_rate_limit_current 1.0" in text
    assert 'gateway_queue_length{priority="normal"} 0.0' in text


def test_warming_up_pauses_traffic_without_logging_a_failure(fake_backends, events_file):
    original = fake_backends.handler

    def warming(request):
        if request.url.host == "app-3" and request.url.path == "/ready":
            return httpx.Response(
                503, json={"server_id": "app-3", "ready": False, "reason": "warming_up"}
            )
        return original(request)

    fake_backends.handler = warming
    backends = [Backend(f"app-{i}", f"http://app-{i}") for i in (1, 2, 3)]
    app = create_app(
        backends=backends,
        transport=httpx.MockTransport(lambda r: fake_backends.handler(r)),
        run_health_checks=False,
    )
    with TestClient(app) as c:
        run_health_check(c)
        assert "app-3" not in served_by(c, 4)
    assert not events_file.exists() or events_file.read_text() == ""
