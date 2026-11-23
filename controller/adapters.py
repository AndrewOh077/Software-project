"""
Everything the controller touches in the outside world, behind small classes.
Tests swap these for fakes, so the core logic runs without Docker or a network.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("controller")


# ---------------------------------------------------------------- probes
@dataclass
class Probe:
    live: bool   # /health OK  -> process is alive
    ready: bool  # /ready OK   -> can take traffic


def _http_ok(url: str, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


class HttpProber:
    def __init__(self, timeout_s: float):
        self.timeout = timeout_s

    def check(self, name: str, url: str) -> Probe:
        live = _http_ok(f"{url}/health", self.timeout)
        ready = live and _http_ok(f"{url}/ready", self.timeout)
        return Probe(live=live, ready=ready)


# ---------------------------------------------------------------- docker
class DockerCLI:
    """Uses the `docker` command line (needs /var/run/docker.sock mounted in the container)."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)

    def restart(self, name: str) -> bool:
        r = self._run("restart", "-t", "2", name)
        if r.returncode != 0 and "paused" in (r.stderr or "").lower():
            # a frozen (paused) container must be unpaused before it can restart
            self._run("unpause", name)
            r = self._run("restart", "-t", "2", name)
        if r.returncode != 0:
            log.error("docker restart %s failed: %s", name, r.stderr.strip())
        return r.returncode == 0


# ---------------------------------------------------------------- gateway
class HttpGateway:
    """
    Client for Person 2's Gateway admin API (see docs/interfaces.md).
    Every method returns True on success. Failures are logged, never raised:
    the controller must keep restarting servers even if the gateway is down.
    """

    def __init__(self, base_url: str, timeout_s: float = 1.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout_s

    def _post(self, path: str, body: dict | None = None) -> bool:
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return 200 <= r.status < 300
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            log.warning("gateway POST %s failed: %s", path, e)
            return False

    def disable(self, server: str) -> bool:
        return self._post(f"/admin/backends/{server}/disable")

    def enable(self, server: str) -> bool:
        return self._post(f"/admin/backends/{server}/enable")

    def set_weight(self, server: str, weight: int) -> bool:
        return self._post(f"/admin/backends/{server}/weight", {"weight": weight})

    def set_mode(self, mode: str) -> bool:
        return self._post("/admin/mode", {"mode": mode})


class NullGateway:
    """Used when GATEWAY_URL is empty: just logs what it would have done."""

    def disable(self, server): log.info("[no gateway] disable %s", server); return True
    def enable(self, server): log.info("[no gateway] enable %s", server); return True
    def set_weight(self, server, weight): log.info("[no gateway] weight %s=%s", server, weight); return True
    def set_mode(self, mode): log.info("[no gateway] mode=%s", mode); return True


# ---------------------------------------------------------------- alerts
class Alerter:
    """Logs alerts; optionally POSTs them to a Discord/Slack webhook (ALERT_WEBHOOK_URL)."""

    def __init__(self, webhook_url: str = ""):
        self.webhook = webhook_url

    def send(self, message: str) -> None:
        log.warning("ALERT: %s", message)
        if not self.webhook:
            return
        body = json.dumps({"content": message, "text": message}).encode()
        req = urllib.request.Request(self.webhook, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=3).close()
        except Exception as e:  # alerts must never crash the controller
            log.warning("alert webhook failed: %s", e)
