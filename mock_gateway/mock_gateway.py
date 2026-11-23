"""
Mock Gateway — a stand-in for Person 2's real Gateway, implementing ONLY the admin API
in docs/interfaces.md. It keeps state in memory and logs every call, so you can see
exactly what the controller asked for.

When the real Gateway is ready: point the controller's GATEWAY_URL at it and delete this.

GET  /admin/backends                  -> {"mode": "...", "backends": {"app1": {"enabled": true, "weight": 100}, ...}}
POST /admin/backends/{id}/disable
POST /admin/backends/{id}/enable
POST /admin/backends/{id}/weight      body {"weight": 0-100}
POST /admin/mode                      body {"mode": "normal" | "emergency"}
GET  /admin/calls                     -> last 200 calls (for debugging / tests)
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKENDS = [b.strip() for b in os.getenv("BACKENDS", "app1,app2,app3").split(",") if b.strip()]
state = {"mode": "normal", "backends": {b: {"enabled": True, "weight": 100} for b in BACKENDS}}
calls: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/admin/backends":
            return self._send(200, state)
        if self.path == "/admin/calls":
            return self._send(200, calls[-200:])
        if self.path == "/health":
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n)) if n else {}
        except json.JSONDecodeError:
            return self._send(400, {"error": "invalid JSON"})
        parts = self.path.strip("/").split("/")

        if parts[:2] == ["admin", "mode"]:
            if body.get("mode") not in ("normal", "emergency"):
                return self._send(400, {"error": "mode must be normal|emergency"})
            state["mode"] = body["mode"]
        elif len(parts) == 4 and parts[:2] == ["admin", "backends"]:
            b, action = parts[2], parts[3]
            if b not in state["backends"]:
                return self._send(404, {"error": f"unknown backend {b}"})
            if action == "disable":
                state["backends"][b]["enabled"] = False
            elif action == "enable":
                state["backends"][b]["enabled"] = True
            elif action == "weight":
                w = body.get("weight")
                if not isinstance(w, int) or not 0 <= w <= 100:
                    return self._send(400, {"error": "weight must be an int 0-100"})
                state["backends"][b]["weight"] = w
            else:
                return self._send(404, {"error": "unknown action"})
        else:
            return self._send(404, {"error": "not found"})

        calls.append({"time": round(time.time(), 3), "path": self.path, "body": body})
        print(f"[mock-gateway] {self.path} {body or ''}", flush=True)
        self._send(200, state)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "9000"))
    print(f"[mock-gateway] listening on :{port}, backends={BACKENDS}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
