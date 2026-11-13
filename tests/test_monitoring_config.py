"""Prometheus + Grafana config stays in sync with docs/contracts.md."""

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "monitoring" / "grafana"))

import build_dashboard  # noqa: E402

DASHBOARD = ROOT / "monitoring/grafana/dashboards/resiliguard-overview.json"

# Contract 4 metric names + Prometheus' own "up".
CONTRACT_METRICS = {
    "http_requests_total",
    "http_request_duration_seconds",
    "app_in_flight_requests",
    "gateway_backend_healthy",
    "gateway_rejected_total",
    "gateway_queue_length",
    "gateway_rate_limit_current",
    "controller_restarts_total",
    "controller_emergency_mode",
    "up",
}
PROMQL_FUNCS = {
    "sum",
    "rate",
    "histogram_quantile",
    "by",
    "le",
    "server_id",
    "status_code",
    "reason",
    "priority",
    "service",
    "job",
    "instance",
}


def all_exprs(dashboard: dict) -> list[str]:
    return [t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])]


def test_prometheus_scrapes_gateway_and_all_apps():
    cfg = yaml.safe_load((ROOT / "monitoring/prometheus/prometheus.yml").read_text())
    targets = {
        job["job_name"]: job["static_configs"][0]["targets"] for job in cfg["scrape_configs"]
    }
    assert targets["gateway"] == ["gateway:8000"]
    assert targets["app"] == ["app-1:8000", "app-2:8000", "app-3:8000"]


def test_dashboard_json_is_up_to_date():
    """If this fails: run `make dashboard` and commit the JSON."""
    assert json.loads(DASHBOARD.read_text()) == build_dashboard.build()


def test_dashboard_only_uses_contract_metrics():
    for expr in all_exprs(build_dashboard.build()):
        without_strings_and_windows = re.sub(r'"[^"]*"|\[[^\]]*\]', "", expr)
        names = set(re.findall(r"[a-z_][a-z0-9_]*", without_strings_and_windows))
        metrics = {n.removesuffix("_bucket") for n in names} - PROMQL_FUNCS
        unknown = {m for m in metrics if not m.isdigit()} - CONTRACT_METRICS
        assert not unknown, f"{unknown} not in docs/contracts.md (query: {expr})"


def test_dashboard_has_gateway_and_app_sections():
    titles = [p["title"] for p in build_dashboard.build()["panels"]]
    for must in (
        "Backend health (gateway's view)",
        "Rejected by reason",
        "Traffic sent to each backend",
        "Requests / sec per app server",
    ):
        assert must in titles


def test_grafana_datasource_uid_matches_dashboard():
    ds = yaml.safe_load(
        (ROOT / "monitoring/grafana/provisioning/datasources/prometheus.yml").read_text()
    )
    uid = ds["datasources"][0]["uid"]
    for p in build_dashboard.build()["panels"]:
        if p["type"] != "row":
            assert p["datasource"]["uid"] == uid
