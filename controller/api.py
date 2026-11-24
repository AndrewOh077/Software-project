"""
Small HTTP API for the controller (standard library only).

GET  /status                 -> every server's state, weight, restarts; emergency flag
GET  /metrics                -> Prometheus metrics
GET  /events?limit=100       -> recent events
GET  /incidents              -> current + recently closed incidents (Week 10)
POST /warning                -> early warning from the anomaly detector
                                body: {"server": "app2", "score": 0.93, "reason": "p99 rising"}
POST /admin/release/{server} -> operator: un-quarantine a server / clear its restart history
GET  /healthz                -> controller itself is alive
"""

from __future__ import annotations

import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("controller")


def make_server(controller, host: str = "0.0.0.0", port: int = 9100) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep logs quiet (Prometheus scrapes every 2s)
            pass

        def _send(self, code: int, body, content_type="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body, indent=2).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json_body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n))

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/healthz":
                return self._send(200, {"ok": True})
            if u.path == "/status":
                # server_time: the container's clock, so tests can compare against event times
                return self._send(200, {**controller.status(), "server_time": round(time.time(), 3)})
            if u.path == "/metrics":
                return self._send(200, controller.metrics(), "text/plain; version=0.0.4")
            if u.path == "/events":
                limit = int(parse_qs(u.query).get("limit", ["100"])[0])
                return self._send(200, list(controller.events.recent)[-limit:])
            if u.path == "/incidents":
                inc = controller.incidents
                return self._send(200, {"current": inc.current, "closed": list(inc.closed)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            try:
                body = self._json_body()
            except json.JSONDecodeError:
                return self._send(400, {"error": "invalid JSON"})

            if u.path == "/warning":
                server = body.get("server")
                if not server:
                    return self._send(400, {"error": "'server' is required"})
                result = controller.warn(server, body.get("score"), body.get("reason", ""),
                                         body.get("source", "anomaly-detector"))
                return self._send(200 if result.get("accepted") else 409, result)

            if u.path.startswith("/admin/release/"):
                result = controller.release(u.path.rsplit("/", 1)[-1])
                return self._send(200 if result["ok"] else 409, result)

            self._send(404, {"error": "not found"})

    return ThreadingHTTPServer((host, port), Handler)
