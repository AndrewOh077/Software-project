"""Builds monitoring/grafana/dashboards/resiliguard-overview.json.

Why a script instead of clicking in Grafana?
  - Every panel's PromQL is readable here in one place (easy to review in a PR).
  - tests/test_monitoring_config.py checks every query uses metric names from docs/contracts.md.

Run after changing a panel:
    python monitoring/grafana/build_dashboard.py
"""

import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards" / "resiliguard-overview.json"
DS = {"type": "prometheus", "uid": "prometheus"}
W = "[30s]"  # rate window: 6 scrapes at 5s. Short = reacts fast when a server dies.

GW = 'service="gateway"'
APP = 'service="app"'

_next_id = 0


def _id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def target(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref}


def row(title: str, y: int) -> dict:
    return {
        "type": "row",
        "title": title,
        "id": _id(),
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }


def stat(title, expr, x, y, unit="short", thresholds=None, desc="", w=6, decimals=None):
    steps = thresholds or [{"color": "green", "value": None}]
    panel = {
        "type": "stat",
        "title": title,
        "id": _id(),
        "datasource": DS,
        "description": desc,
        "gridPos": {"h": 4, "w": w, "x": x, "y": y},
        "targets": [target(expr)],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": steps},
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background",
            "graphMode": "area",
            "textMode": "value",
        },
    }
    if decimals is not None:
        panel["fieldConfig"]["defaults"]["decimals"] = decimals
    return panel


def timeseries(title, targets, x, y, w=12, h=8, unit="short", desc="", stack=False):
    return {
        "type": "timeseries",
        "title": title,
        "id": _id(),
        "datasource": DS,
        "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {
                    "drawStyle": "line",
                    "lineWidth": 2,
                    "fillOpacity": 10,
                    "showPoints": "never",
                    "spanNulls": True,
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def up_down_timeline(title, expr, legend, x, y, w=12, h=6, desc=""):
    """Green UP / red DOWN bar per server over time."""
    return {
        "type": "state-timeline",
        "title": title,
        "id": _id(),
        "datasource": DS,
        "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(expr, legend)],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}],
                },
                "mappings": [
                    {
                        "type": "value",
                        "options": {
                            "0": {"text": "DOWN", "color": "red"},
                            "1": {"text": "UP", "color": "green"},
                        },
                    }
                ],
            },
            "overrides": [],
        },
        "options": {
            "showValue": "auto",
            "mergeValues": True,
            "rowHeight": 0.8,
            "legend": {"showLegend": False},
        },
    }


def p_latency(service_sel: str, q: str, by: str = "") -> str:
    group = f"le, {by}" if by else "le"
    return (
        f"histogram_quantile({q}, sum by ({group}) "
        f"(rate(http_request_duration_seconds_bucket{{{service_sel}}}{W})))"
    )


def build() -> dict:
    global _next_id
    _next_id = 0
    p = []

    # ---------------- Row 1: headline numbers ----------------
    p.append(row("Gateway — the front door (what users experience)", 0))
    p.append(
        stat(
            "Requests / sec",
            f"sum(rate(http_requests_total{{{GW}}}{W}))",
            0,
            1,
            unit="reqps",
            decimals=1,
            desc="All requests that reached the gateway, accepted or not.",
        )
    )
    p.append(
        stat(
            "Server error rate",
            f'sum(rate(http_requests_total{{{GW},status_code=~"5..",server_id!="none"}}{W}))'
            f" / sum(rate(http_requests_total{{{GW}}}{W}))",
            6,
            1,
            unit="percentunit",
            decimals=1,
            thresholds=[
                {"color": "green", "value": None},
                {"color": "orange", "value": 0.01},
                {"color": "red", "value": 0.05},
            ],
            desc="Requests an app server failed (5xx). Does NOT include requests the gateway "
            "rejected on purpose (429/503 with server_id=none) — those are on the right.",
        )
    )
    p.append(
        stat(
            "Rejected on purpose",
            f"sum(rate(gateway_rejected_total{W})) / sum(rate(http_requests_total{{{GW}}}{W}))",
            12,
            1,
            unit="percentunit",
            decimals=1,
            thresholds=[
                {"color": "green", "value": None},
                {"color": "orange", "value": 0.05},
                {"color": "red", "value": 0.3},
            ],
            desc="Share of requests the gateway said no to (rate limited, no backend, ...).",
        )
    )
    p.append(
        stat(
            "p99 latency",
            p_latency(GW, "0.99"),
            18,
            1,
            unit="s",
            decimals=3,
            thresholds=[
                {"color": "green", "value": None},
                {"color": "orange", "value": 0.25},
                {"color": "red", "value": 1},
            ],
            desc="99% of requests were faster than this.",
        )
    )

    # ---------------- Row 1b: gateway graphs ----------------
    p.append(
        timeseries(
            "Gateway traffic by status code",
            [
                target(
                    f"sum by (status_code) (rate(http_requests_total{{{GW}}}{W}))",
                    "{{status_code}}",
                )
            ],
            0,
            5,
            unit="reqps",
            stack=True,
            desc="200 = served, 429 = rate limited, 503 = no backend / queue full, "
            "500 = app broke.",
        )
    )
    p.append(
        timeseries(
            "Gateway latency (p50 / p95 / p99)",
            [
                target(p_latency(GW, "0.50"), "p50", "A"),
                target(p_latency(GW, "0.95"), "p95", "B"),
                target(p_latency(GW, "0.99"), "p99", "C"),
            ],
            12,
            5,
            unit="s",
        )
    )
    p.append(
        up_down_timeline(
            "Backend health (gateway's view)",
            "gateway_backend_healthy",
            "{{server_id}}",
            0,
            13,
            desc="Does the gateway currently send traffic to this server? Watch it turn red "
            "when a server is killed and green when it comes back.",
        )
    )
    p.append(
        timeseries(
            "Traffic sent to each backend",
            [
                target(
                    f'sum by (server_id) (rate(http_requests_total{{{GW},server_id!="none"}}{W}))',
                    "{{server_id}}",
                )
            ],
            12,
            13,
            h=6,
            unit="reqps",
            desc="Round robin = three equal lines. When one dies the other two go up.",
        )
    )
    p.append(
        timeseries(
            "Rejected by reason",
            [target("sum by (reason) (rate(gateway_rejected_total" + W + "))", "{{reason}}")],
            0,
            19,
            unit="reqps",
        )
    )
    p.append(
        timeseries(
            "Rate limit & queue",
            [
                target("gateway_rate_limit_current", "rate limit (req/s, 0 = off)", "A"),
                target("sum by (priority) (gateway_queue_length)", "queue {{priority}}", "B"),
            ],
            12,
            19,
            desc="Week 4–5: token bucket, queue and AIMD (Person 2) will move these lines.",
        )
    )

    # ---------------- Row 2: app servers ----------------
    p.append(row("App servers — the workers", 27))
    p.append(
        timeseries(
            "Requests / sec per app server",
            [
                target(
                    f"sum by (server_id) (rate(http_requests_total{{{APP}}}{W}))", "{{server_id}}"
                )
            ],
            0,
            28,
            unit="reqps",
        )
    )
    p.append(
        timeseries(
            "p99 latency per app server",
            [target(p_latency(APP, "0.99", "server_id"), "{{server_id}}")],
            12,
            28,
            unit="s",
        )
    )
    p.append(
        timeseries(
            "Error rate per app server",
            [
                target(
                    f'sum by (server_id) (rate(http_requests_total{{{APP},status_code=~"5.."}}{W}))'
                    f" / sum by (server_id) (rate(http_requests_total{{{APP}}}{W}))",
                    "{{server_id}}",
                )
            ],
            0,
            36,
            unit="percentunit",
        )
    )
    p.append(
        timeseries(
            "In-flight requests",
            [target("sum by (server_id) (app_in_flight_requests)", "{{server_id}}")],
            12,
            36,
            desc="Requests being worked on right now. Rising = server is falling behind.",
        )
    )
    p.append(
        up_down_timeline(
            "Can Prometheus reach each service? (up)",
            "up",
            "{{job}} {{instance}}",
            0,
            44,
            w=24,
            desc="1 = Prometheus scraped /metrics successfully. "
            "A killed container shows DOWN here.",
        )
    )

    return {
        "uid": "resiliguard-overview",
        "title": "ResiliGuard — Overview",
        "tags": ["resiliguard"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "5s",
        "time": {"from": "now-15m", "to": "now"},
        "editable": True,
        "graphTooltip": 1,
        "panels": p,
        "templating": {"list": []},
        "annotations": {"list": []},
    }


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {OUT} ({len(build()['panels'])} panels)")
