"""All controller settings, read from environment variables (with sane defaults)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def parse_servers(spec: str) -> dict[str, str]:
    """'app1=http://app1:8000,app2=http://app2:8000' -> {'app1': 'http://app1:8000', ...}"""
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, url = part.split("=", 1)
        out[name.strip()] = url.strip().rstrip("/")
    return out


@dataclass
class Config:
    # --- detection ---
    probe_interval_s: float = 1.0      # how often we check every server
    probe_timeout_s: float = 1.0       # HTTP timeout per probe
    fail_threshold: int = 3            # consecutive failed probes before a server is DOWN
    not_ready_grace_s: float = 15.0    # alive-but-not-ready this long -> restart anyway

    # --- recovery ---
    startup_grace_s: float = 30.0      # how long we wait for servers when the controller starts
    ready_timeout_s: float = 30.0      # after a restart, must be ready within this time
    ramp_weights: list[int] = field(default_factory=lambda: [10, 25, 50, 100])
    ramp_step_s: float = 3.0           # time between ramp steps

    # --- restart limits ---
    max_restarts: int = 3              # max restarts per server ...
    restart_window_s: float = 120.0    # ... within this window, else QUARANTINED
    backoff_base_s: float = 2.0        # wait before retrying a failed restart: base * 2^(n-1)
    backoff_max_s: float = 30.0
    quarantine_cooldown_s: float = 300.0  # after this, try one more round automatically

    # --- emergency mode ---
    emergency_min_serving: int = 1     # enter emergency if fewer servers than this are serving
    exit_min_serving: int = 2          # leave emergency when at least this many are serving ...
    exit_stable_s: float = 10.0        # ... continuously for this long (and nothing quarantined)

    # --- early warnings (from Person 3's anomaly detector) ---
    warn_weight: int = 50              # traffic weight for a server that got a warning
    warn_duration_s: float = 30.0

    @classmethod
    def from_env(cls) -> "Config":
        c = cls()
        c.probe_interval_s = _f("PROBE_INTERVAL_S", c.probe_interval_s)
        c.probe_timeout_s = _f("PROBE_TIMEOUT_S", c.probe_timeout_s)
        c.fail_threshold = _i("FAIL_THRESHOLD", c.fail_threshold)
        c.not_ready_grace_s = _f("NOT_READY_GRACE_S", c.not_ready_grace_s)
        c.startup_grace_s = _f("STARTUP_GRACE_S", c.startup_grace_s)
        c.ready_timeout_s = _f("READY_TIMEOUT_S", c.ready_timeout_s)
        if os.getenv("RAMP_WEIGHTS"):
            c.ramp_weights = [int(x) for x in os.environ["RAMP_WEIGHTS"].split(",")]
        c.ramp_step_s = _f("RAMP_STEP_S", c.ramp_step_s)
        c.max_restarts = _i("MAX_RESTARTS", c.max_restarts)
        c.restart_window_s = _f("RESTART_WINDOW_S", c.restart_window_s)
        c.backoff_base_s = _f("BACKOFF_BASE_S", c.backoff_base_s)
        c.backoff_max_s = _f("BACKOFF_MAX_S", c.backoff_max_s)
        c.quarantine_cooldown_s = _f("QUARANTINE_COOLDOWN_S", c.quarantine_cooldown_s)
        c.emergency_min_serving = _i("EMERGENCY_MIN_SERVING", c.emergency_min_serving)
        c.exit_min_serving = _i("EXIT_MIN_SERVING", c.exit_min_serving)
        c.exit_stable_s = _f("EXIT_STABLE_S", c.exit_stable_s)
        c.warn_weight = _i("WARN_WEIGHT", c.warn_weight)
        c.warn_duration_s = _f("WARN_DURATION_S", c.warn_duration_s)
        if c.ramp_weights[-1] != 100:
            raise ValueError("RAMP_WEIGHTS must end with 100")
        return c
