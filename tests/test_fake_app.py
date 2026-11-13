"""The fake app server must answer in exactly the shapes from docs/contracts.md (1–4)."""

import time

import pytest
from fastapi.testclient import TestClient

from fakes.fake_app import create_app


@pytest.fixture
def client():
    return TestClient(create_app("app-7", ready_delay_sec=0, base_latency_ms=1))


def test_contract1_work_shape(client):
    r = client.get("/work", headers={"X-Priority": "critical"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"server_id", "status", "data", "processed_ms"}
    assert body["server_id"] == "app-7"
    assert body["status"] == "ok"
    assert isinstance(body["processed_ms"], float)


def test_contract2_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"server_id": "app-7", "status": "alive"}


def test_contract3_ready_after_warmup(client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"server_id": "app-7", "ready": True, "reason": None}


def test_contract3_not_ready_while_warming_up():
    c = TestClient(create_app("app-1", ready_delay_sec=60, base_latency_ms=1))
    r = c.get("/ready")
    assert r.status_code == 503
    assert r.json() == {"server_id": "app-1", "ready": False, "reason": "warming_up"}


def test_contract4_metric_names(client):
    client.get("/work")
    text = client.get("/metrics").text
    assert (
        'http_requests_total{priority="normal",server_id="app-7",service="app",'
        'status_code="200"} 1.0' in text
    )
    assert "http_request_duration_seconds_bucket" in text
    assert 'app_in_flight_requests{server_id="app-7"}' in text


def test_health_checks_are_not_counted_as_traffic(client):
    client.get("/health")
    client.get("/ready")
    assert "http_requests_total{" not in client.get("/metrics").text


def test_chaos_down_and_back(client):
    client.post("/chaos/down")
    assert client.get("/health").status_code == 503
    assert client.get("/ready").json()["reason"] == "shutting_down"
    assert client.get("/work").status_code == 503

    client.post("/chaos/ok")
    assert client.get("/health").status_code == 200
    assert client.get("/work").status_code == 200


def test_coming_back_from_down_warms_up_again():
    app = create_app("app-2", ready_delay_sec=0.2, base_latency_ms=1)
    c = TestClient(app)
    time.sleep(0.25)
    assert c.get("/ready").status_code == 200
    c.post("/chaos/down")
    c.post("/chaos/ok")
    assert c.get("/ready").json()["reason"] == "warming_up"  # just "restarted"
    time.sleep(0.25)
    assert c.get("/ready").status_code == 200


def test_chaos_error_mode(client):
    client.post("/chaos/error?error_rate=1.0")
    assert client.get("/work").status_code == 500


def test_chaos_slow_mode_is_not_ready(client):
    client.post("/chaos/slow?latency_ms=1")
    assert client.get("/ready").json() == {
        "server_id": "app-7",
        "ready": False,
        "reason": "overloaded",
    }


def test_chaos_rejects_unknown_mode(client):
    assert client.post("/chaos/explode").status_code == 400
