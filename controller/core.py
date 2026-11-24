"""
Self-Healing Controller — the core logic.

Every tick (default 1s) it probes each server and moves it through this state machine:

    STARTING ──ready──▶ HEALTHY ◀──────────────── WARMING (weight 10→25→50→100)
                         │  ▲                         ▲
                 1 fail  ▼  │ ok                      │ ready
                        SUSPECT                       │
                         │ N fails in a row           │
                         ▼                            │
                        DOWN ──restart──▶ RESTARTING ─┘
                         │ ▲                  │ not ready in time
                         │ └──────────────────┘ (backoff, try again)
                         │ too many restarts
                         ▼
                     QUARANTINED  (stop restarting, alert, emergency mode)

On top of that, a system-level EMERGENCY mode turns on when no server is serving
or a server is quarantined, and tells the Gateway to serve critical requests only.

No I/O happens here directly: probes, docker, gateway, alerts and the clock are all
injected, so tests can drive it step by step with fakes (tests/unit/test_core.py).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .adapters import Probe
from .config import Config
from .events import EventLog, IncidentTracker


class State(str, Enum):
    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    SUSPECT = "SUSPECT"
    DOWN = "DOWN"
    RESTARTING = "RESTARTING"
    WARMING = "WARMING"
    QUARANTINED = "QUARANTINED"


# States in which the gateway may send the server traffic
SERVING = {State.HEALTHY, State.SUSPECT, State.WARMING}


@dataclass
class Server:
    name: str
    url: str
    state: State = State.STARTING
    state_since: float = 0.0
    consecutive_fail: int = 0
    last_probe: Probe | None = None
    # recovery bookkeeping
    down_since: float | None = None
    failure_started: float | None = None   # first moment of this outage (for recovery time)
    ready_deadline: float = 0.0
    next_restart_at: float = 0.0
    restart_times: deque = field(default_factory=deque)
    restarts_total: int = 0
    quarantined_at: float | None = None
    ramp_idx: int = 0
    ramp_next_at: float = 0.0
    last_recovery_s: float | None = None
    # early warning
    warned_until: float = 0.0
    warnings_total: int = 0
    # what we last told the gateway (None = unknown, will be re-sent)
    gw_enabled: bool | None = None
    gw_weight: int | None = None


class Controller:
    def __init__(self, cfg: Config, servers: dict[str, str], prober, docker, gateway,
                 events: EventLog, alerter, incidents: IncidentTracker | None = None,
                 clock=time.monotonic, async_restart: bool = True):
        self.cfg = cfg
        self.prober = prober
        self.docker = docker
        self.gateway = gateway
        self.events = events
        self.alerter = alerter
        self.incidents = incidents or IncidentTracker(events)
        self.clock = clock
        # True (real use): `docker restart` runs in a background thread so the controller
        # keeps answering its API and can restart several servers at once.
        # False (unit tests): restart happens inline, so tests are deterministic.
        self.async_restart = async_restart
        now = clock()
        self.started_at = now
        self.servers = {
            n: Server(n, u, state_since=now, ready_deadline=now + cfg.startup_grace_s)
            for n, u in servers.items()
        }
        self.emergency = False
        self._calm_since: float | None = None
        self.lock = threading.RLock()
        self.ticks = 0

    # ================================================================ main loop
    def tick(self) -> None:
        # Probing is slow network I/O, so do it WITHOUT holding the lock.
        # Otherwise /status, /warning and /admin/release would hang.
        probes = self._probe_all()
        with self.lock:
            now = self.clock()
            for s in self.servers.values():
                s.last_probe = probes[s.name]
                self._step(s, probes[s.name], now)
            self._update_emergency(now)
            self._update_incident()
            self.ticks += 1

    def _probe_all(self) -> dict:
        """
        Unit tests (async_restart=False): probe one by one, deterministic.
        Real use: probe all servers AT THE SAME TIME, each with a hard deadline.
        Why: on Docker (esp. Windows), looking up the name of a *stopped* container can take
        several seconds, and the HTTP timeout doesn't cover that. Probing one by one, with
        2-3 servers dead, one tick took 10+ seconds and failures were detected far too late.
        A probe that hasn't answered by the deadline simply counts as a failure.
        """
        servers = list(self.servers.values())
        if not self.async_restart:
            return {s.name: self.prober.check(s.name, s.url) for s in servers}

        results: dict = {}

        def run(srv):
            try:
                results[srv.name] = self.prober.check(srv.name, srv.url)
            except Exception:
                results[srv.name] = Probe(live=False, ready=False)

        threads = [threading.Thread(target=run, args=(srv,), daemon=True) for srv in servers]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 2 * self.cfg.probe_timeout_s + 0.5
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
        return {srv.name: results.get(srv.name, Probe(live=False, ready=False)) for srv in servers}

    # ================================================================ per server
    def _step(self, s: Server, p: Probe, now: float) -> None:
        st = s.state

        if st in (State.HEALTHY, State.SUSPECT):
            if p.ready:
                s.consecutive_fail = 0
                if st == State.SUSPECT:
                    self._set(s, State.HEALTHY, now, "probe ok again")
                self._gw_enable(s)
                self._gw_weight(s, self._desired_weight(s, now))
            else:
                s.consecutive_fail += 1
                if s.consecutive_fail >= self.cfg.fail_threshold:
                    self._mark_down(s, p, now)
                elif st == State.HEALTHY:
                    self._set(s, State.SUSPECT, now, "probe failed")

        elif st == State.STARTING:
            if p.ready:
                self._set(s, State.HEALTHY, now, "initial ready")
                self._gw_enable(s)
                self._gw_weight(s, 100)
            elif now >= s.ready_deadline:
                self._mark_down(s, p, now, reason="never became ready at startup")

        elif st == State.DOWN:
            if p.ready:
                self._start_warming(s, now, "came back on its own")
            elif self._should_restart(s, p, now):
                self._try_restart(s, now)

        elif st == State.RESTARTING:
            if p.ready:
                self._start_warming(s, now, "ready after restart")
            elif now >= s.ready_deadline:
                s.next_restart_at = now + self._backoff(len(s.restart_times))
                self._set(s, State.DOWN, now, "not ready in time after restart")

        elif st == State.WARMING:
            if not p.ready:
                s.consecutive_fail += 1
                if s.consecutive_fail >= self.cfg.fail_threshold:
                    self._mark_down(s, p, now, reason="failed during warm-up")
                return
            s.consecutive_fail = 0
            if now >= s.ramp_next_at:
                if s.ramp_idx >= len(self.cfg.ramp_weights) - 1:
                    self._finish_recovery(s, now)
                    return
                s.ramp_idx += 1
                weight = self.cfg.ramp_weights[s.ramp_idx]
                self._gw_weight(s, weight)
                if s.ramp_idx >= len(self.cfg.ramp_weights) - 1:
                    self._finish_recovery(s, now)
                else:
                    s.ramp_next_at = now + self.cfg.ramp_step_s
                    self.events.emit("ramp", server=s.name, weight=weight)

        elif st == State.QUARANTINED:
            if p.ready:
                self._start_warming(s, now, "recovered while quarantined")
            elif now >= (s.quarantined_at or now) + self.cfg.quarantine_cooldown_s:
                s.restart_times.clear()
                s.next_restart_at = now
                s.quarantined_at = None
                self._set(s, State.DOWN, now, "quarantine cooldown over, retrying")

    # ================================================================ transitions
    def _set(self, s: Server, new: State, now: float, reason: str) -> None:
        old = s.state
        s.state, s.state_since = new, now
        self.events.emit("state_change", server=s.name, old=old.value, new=new.value, reason=reason)

    def _mark_down(self, s: Server, p: Probe, now: float, reason: str | None = None) -> None:
        reason = reason or ("not alive" if not p.live else "alive but not ready")
        self._set(s, State.DOWN, now, reason)
        s.down_since = now
        if s.failure_started is None:
            s.failure_started = now
        s.consecutive_fail = 0
        s.next_restart_at = now  # first restart attempt right away
        self._gw_disable(s)
        self.incidents.open(reason=f"{s.name}: {reason}")
        self.events.emit("server_down", server=s.name, live=p.live, ready=p.ready, reason=reason)
        if self._should_restart(s, p, now):  # dead process: don't wait a tick, restart now
            self._try_restart(s, now)

    def _should_restart(self, s: Server, p: Probe, now: float) -> bool:
        if now < s.next_restart_at:
            return False
        if not p.live:
            return True
        # Alive but not ready: maybe just overloaded. Give it a grace period before restarting.
        return now - (s.down_since or now) >= self.cfg.not_ready_grace_s

    def _try_restart(self, s: Server, now: float) -> None:
        window_start = now - self.cfg.restart_window_s
        while s.restart_times and s.restart_times[0] < window_start:
            s.restart_times.popleft()

        if len(s.restart_times) >= self.cfg.max_restarts:
            self._quarantine(s, now)
            return

        s.restart_times.append(now)
        s.restarts_total += 1
        self.events.emit("restart", server=s.name, attempt=len(s.restart_times))
        if self.async_restart:
            # Mark RESTARTING now; if the restart fails, the server simply won't become ready
            # before ready_deadline, and the normal "not ready in time" path retries with backoff.
            s.ready_deadline = now + self.cfg.ready_timeout_s
            self._set(s, State.RESTARTING, now, f"restart #{len(s.restart_times)} issued")
            threading.Thread(target=self._restart_in_background, args=(s.name,), daemon=True).start()
        elif self.docker.restart(s.name):
            s.ready_deadline = now + self.cfg.ready_timeout_s
            self._set(s, State.RESTARTING, now, f"restart #{len(s.restart_times)} issued")
        else:
            s.next_restart_at = now + self._backoff(len(s.restart_times))
            self.events.emit("restart_failed", server=s.name, retry_in_s=s.next_restart_at - now)

    def _restart_in_background(self, name: str) -> None:
        if not self.docker.restart(name):
            with self.lock:
                self.events.emit("restart_failed", server=name)

    def _quarantine(self, s: Server, now: float) -> None:
        s.quarantined_at = now
        self._set(s, State.QUARANTINED, now,
                  f"{self.cfg.max_restarts} restarts in {self.cfg.restart_window_s:.0f}s")
        self.events.emit("quarantined", server=s.name)
        self.alerter.send(f"{s.name} keeps failing after {self.cfg.max_restarts} restarts — "
                          f"stopped restarting it. Needs a human.")

    def _start_warming(self, s: Server, now: float, reason: str) -> None:
        self._set(s, State.WARMING, now, reason)
        s.consecutive_fail = 0
        s.ramp_idx = 0
        s.ramp_next_at = now + self.cfg.ramp_step_s
        s.quarantined_at = None
        self._gw_enable(s)
        self._gw_weight(s, self.cfg.ramp_weights[0])
        self.events.emit("ramp", server=s.name, weight=self.cfg.ramp_weights[0])

    def _finish_recovery(self, s: Server, now: float) -> None:
        self._set(s, State.HEALTHY, now, "warm-up complete")
        if s.failure_started is not None:
            s.last_recovery_s = round(now - s.failure_started, 3)
            self.events.emit("recovered", server=s.name, recovery_s=s.last_recovery_s,
                             restarts=len(s.restart_times))
        s.failure_started = None
        s.down_since = None

    def _backoff(self, attempts: int) -> float:
        return min(self.cfg.backoff_base_s * 2 ** max(attempts - 1, 0), self.cfg.backoff_max_s)

    # ================================================================ gateway sync
    def _desired_weight(self, s: Server, now: float) -> int:
        return self.cfg.warn_weight if now < s.warned_until else 100

    def _gw_enable(self, s: Server) -> None:
        if s.gw_enabled is not True:
            s.gw_enabled = True if self.gateway.enable(s.name) else None

    def _gw_disable(self, s: Server) -> None:
        if s.gw_enabled is not False:
            s.gw_enabled = False if self.gateway.disable(s.name) else None
            s.gw_weight = None

    def _gw_weight(self, s: Server, w: int) -> None:
        if s.gw_weight != w:
            s.gw_weight = w if self.gateway.set_weight(s.name, w) else None

    # ================================================================ emergency mode
    def _update_emergency(self, now: float) -> None:
        states = [s.state for s in self.servers.values()]
        if State.STARTING in states:
            return  # don't judge the system while it is still booting
        serving = sum(st in SERVING for st in states)
        quarantined = State.QUARANTINED in states
        bad = quarantined or serving < self.cfg.emergency_min_serving

        if not self.emergency:
            if bad:
                self.emergency = True
                self._calm_since = None
                self.gateway.set_mode("emergency")
                why = "server quarantined" if quarantined else f"only {serving} server(s) serving"
                self.incidents.open(reason=f"emergency: {why}")
                self.events.emit("emergency_on", reason=why, serving=serving)
                self.alerter.send(f"EMERGENCY MODE ON — {why}. Gateway now serves critical requests only.")
            return

        calm = not quarantined and serving >= self.cfg.exit_min_serving
        if not calm:
            self._calm_since = None
            if self.ticks % 10 == 0:
                self.gateway.set_mode("emergency")  # re-send now and then, in case the gateway restarted
            return
        if self._calm_since is None:
            self._calm_since = now
        if now - self._calm_since >= self.cfg.exit_stable_s:
            self.emergency = False
            self._calm_since = None
            self.gateway.set_mode("normal")
            self.events.emit("emergency_off", serving=serving)
            self.alerter.send(f"Emergency mode off — {serving} servers serving again.")

    def _update_incident(self) -> None:
        """Close the open incident once every server is back to normal and emergency is off."""
        if self.incidents.current is None or self.emergency:
            return
        if all(s.state in (State.HEALTHY, State.SUSPECT) for s in self.servers.values()):
            self.incidents.close()

    # ================================================================ early warning (Week 8)
    def warn(self, server: str, score: float | None = None, reason: str = "",
             source: str = "anomaly-detector") -> dict:
        """
        Called by Person 3's anomaly detector. The only action a warning can cause is a
        temporary, reversible traffic reduction — never a restart. (AI advises, rules act.)
        """
        with self.lock:
            now = self.clock()
            s = self.servers.get(server)
            if s is None:
                return {"accepted": False, "error": f"unknown server {server}"}
            if s.state not in (State.HEALTHY, State.SUSPECT):
                self.events.emit("warning_ignored", server=server, state=s.state.value,
                                 reason=reason, source=source)
                return {"accepted": False, "error": f"server is {s.state.value}, already handled"}
            s.warned_until = now + self.cfg.warn_duration_s
            s.warnings_total += 1
            self._gw_weight(s, self.cfg.warn_weight)
            self.events.emit("warning", server=server, score=score, reason=reason, source=source,
                             action=f"weight {self.cfg.warn_weight} for {self.cfg.warn_duration_s:.0f}s")
            return {"accepted": True, "action": "reduce_weight", "weight": self.cfg.warn_weight,
                    "duration_s": self.cfg.warn_duration_s}

    # ================================================================ manual control
    def release(self, server: str) -> dict:
        """
        Operator command: "I fixed it / forgive its past failures".
        - QUARANTINED: clear the restart history and try again right away.
        - any other state: just clear the restart history (restart limit starts fresh).
        """
        with self.lock:
            now = self.clock()
            s = self.servers.get(server)
            if s is None:
                return {"ok": False, "error": f"unknown server {server}"}
            s.restart_times.clear()
            if s.state == State.QUARANTINED:
                s.next_restart_at = now
                s.quarantined_at = None
                self._set(s, State.DOWN, now, "released by operator")
                return {"ok": True, "action": "retrying"}
            self.events.emit("restart_history_cleared", server=server)
            return {"ok": True, "action": "restart history cleared"}

    # ================================================================ read-only views
    def status(self) -> dict:
        with self.lock:
            now = self.clock()
            return {
                "emergency": self.emergency,
                "uptime_s": round(now - self.started_at, 1),
                "incident": self.incidents.current and {
                    k: self.incidents.current[k] for k in ("id", "started", "reason", "servers")
                },
                "servers": {
                    s.name: {
                        "state": s.state.value,
                        "for_s": round(now - s.state_since, 1),
                        "live": s.last_probe.live if s.last_probe else None,
                        "ready": s.last_probe.ready if s.last_probe else None,
                        "weight": s.gw_weight if s.gw_enabled else 0,
                        "restarts_total": s.restarts_total,
                        "restarts_in_window": len(s.restart_times),
                        "last_recovery_s": s.last_recovery_s,
                        "warned": now < s.warned_until,
                    }
                    for s in self.servers.values()
                },
            }

    def metrics(self) -> str:
        """Prometheus text format (written by hand to avoid extra dependencies)."""
        with self.lock:
            now = self.clock()
            L = [
                "# HELP controller_server_state 1 for the state each server is currently in",
                "# TYPE controller_server_state gauge",
            ]
            for s in self.servers.values():
                for st in State:
                    L.append(f'controller_server_state{{server="{s.name}",state="{st.value}"}} '
                             f'{int(s.state == st)}')
            L += ["# TYPE controller_server_weight gauge"]
            L += [f'controller_server_weight{{server="{s.name}"}} {s.gw_weight if s.gw_enabled else 0}'
                  for s in self.servers.values()]
            L += ["# TYPE controller_restarts_total counter"]
            L += [f'controller_restarts_total{{server="{s.name}"}} {s.restarts_total}'
                  for s in self.servers.values()]
            L += ["# TYPE controller_warnings_total counter"]
            L += [f'controller_warnings_total{{server="{s.name}"}} {s.warnings_total}'
                  for s in self.servers.values()]
            L += ["# TYPE controller_last_recovery_seconds gauge"]
            L += [f'controller_last_recovery_seconds{{server="{s.name}"}} {s.last_recovery_s}'
                  for s in self.servers.values() if s.last_recovery_s is not None]
            serving = sum(s.state in SERVING for s in self.servers.values())
            L += [
                "# TYPE controller_serving_servers gauge", f"controller_serving_servers {serving}",
                "# TYPE controller_emergency_mode gauge", f"controller_emergency_mode {int(self.emergency)}",
                "# TYPE controller_incident_open gauge",
                f"controller_incident_open {int(self.incidents.current is not None)}",
                "# TYPE controller_incidents_total counter",
                f"controller_incidents_total {self.incidents.total}",
                "# TYPE controller_uptime_seconds gauge",
                f"controller_uptime_seconds {round(now - self.started_at, 1)}",
            ]
            return "\n".join(L) + "\n"
