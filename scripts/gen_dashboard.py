"""Generates grafana/provisioning/dashboards/overview.json. Run: python3 scripts/gen_dashboard.py"""

import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}


def panel(pid, title, exprs, x, y, w=12, h=8, unit="short", kind="timeseries"):
    p = {
        "id": pid,
        "type": kind,
        "title": title,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "targets": [
            {"refId": chr(65 + i), "datasource": DS, "expr": e, "legendFormat": lf}
            for i, (e, lf) in enumerate(exprs)
        ],
    }
    if kind == "stat":
        p["fieldConfig"]["defaults"]["mappings"] = [
            {"type": "value", "options": {"0": {"text": "DOWN", "color": "red"},
                                          "1": {"text": "UP", "color": "green"}}}
        ]
        p["fieldConfig"]["defaults"]["thresholds"] = {
            "mode": "absolute",
            "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}],
        }
        p["options"] = {"colorMode": "background", "graphMode": "none", "textMode": "value_and_name"}
    return p


panels = [
    panel(1, "Server status", [("up{job=\"apps\"}", "{{instance}}")], 0, 0, w=24, h=4, kind="stat"),
    panel(2, "Requests / sec (per server)",
          [("sum by (server) (rate(app_requests_total{path=\"/work\"}[30s]))", "{{server}}")],
          0, 4, unit="reqps"),
    panel(3, "Error rate (5xx, per server)",
          [("sum by (server) (rate(app_requests_total{path=\"/work\",status=~\"5..\"}[30s]))"
            " / clamp_min(sum by (server) (rate(app_requests_total{path=\"/work\"}[30s])), 1e-9)",
            "{{server}}")],
          12, 4, unit="percentunit"),
    panel(4, "Latency p50 / p95 / p99 (/work, all servers)",
          [(f"histogram_quantile({q}, sum by (le) (rate(app_request_latency_seconds_bucket{{path=\"/work\"}}[30s])))",
            f"p{int(q * 100)}") for q in (0.5, 0.95, 0.99)],
          0, 12, unit="s"),
    panel(5, "In-flight requests",
          [("app_in_flight_requests", "{{server}}")], 12, 12),
    panel(6, "Injected faults",
          [("app_fault_latency_ms", "latency_ms {{server}}"),
           ("app_fault_error_rate * 100", "error_% {{server}}")],
          0, 20, w=24),
    # ---- Self-Healing Controller (Week 4–5)
    panel(7, "Controller: serving servers / emergency mode",
          [("controller_serving_servers", "serving"),
           ("controller_emergency_mode", "emergency (1=on)")],
          0, 28),
    panel(8, "Controller: traffic weight per server (0 = removed)",
          [("controller_server_weight", "{{server}}")], 12, 28),
    panel(9, "Controller: restarts (last 5m)",
          [("increase(controller_restarts_total[5m])", "{{server}}")], 0, 36),
    panel(10, "Controller: last recovery time",
          [("controller_last_recovery_seconds", "{{server}}")], 12, 36, unit="s"),
    # ---- Early warning (Week 8)
    panel(11, "Early warnings received (last 5m)",
          [("increase(controller_warnings_total[5m])", "{{server}}")], 0, 44, w=24),
]

dashboard = {
    "uid": "shp-overview",
    "title": "Self-Healing Platform — Overview",
    "schemaVersion": 39,
    "refresh": "5s",
    "time": {"from": "now-15m", "to": "now"},
    "panels": panels,
}

out = Path(__file__).resolve().parent.parent / "grafana/provisioning/dashboards/overview.json"
out.write_text(json.dumps(dashboard, indent=2))
print(f"wrote {out}")
