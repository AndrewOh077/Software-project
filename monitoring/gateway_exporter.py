"""Gateway -> Prometheus (Person 3, monitoring). Standard library only.

The gateway's own /metrics is JSON (the e2e tests read it), which Prometheus can't scrape.
This tiny server reads that JSON plus /admin/backends and re-serves it in Prometheus text:

    python monitoring/gateway_exporter.py          # http://localhost:9101/metrics
    GATEWAY_URL=http://gateway:8080 PORT=9101       # (defaults, set in docker-compose.yml)
"""

import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def get(path: str):
    with urllib.request.urlopen(f"{GATEWAY}{path}", timeout=2) as r:
        return json.load(r)


def render() -> str:
    try:
        m, b = get("/metrics"), get("/admin/backends")
    except (OSError, ValueError):  # gateway down: say so, Grafana shows it red
        return "gateway_up 0\n"
    lines = [
        "gateway_up 1",
        f"gateway_emergency_mode {int(m['mode'] == 'emergency')}",
        f"gateway_enabled_backends {m['enabled_backends']}",
        f"gateway_concurrency_limit {m['concurrency_limit']}",
        f"gateway_concurrency_max {m['concurrency_max']}",
        f"gateway_inflight {m['inflight']}",
    ]
    for name, item in b["backends"].items():
        lines.append(f'gateway_backend_weight{{server="{name}"}} {item["weight"] * item["enabled"]}')
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # Prometheus scrapes every 2 s; keep the log quiet
        pass

    def do_GET(self):
        body = render().encode() if self.path == "/metrics" else b"see /metrics\n"
        self.send_response(200 if self.path == "/metrics" else 404)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "9101"))
    print(f"gateway exporter: {GATEWAY} -> http://0.0.0.0:{port}/metrics", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
