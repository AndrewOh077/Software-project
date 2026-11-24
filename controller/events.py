"""
Event log + incident tracker (Week 10: 장애 로그 수집기).

events.jsonl    — one line per thing that happened (server_down, restart, recovered, ...)
incidents.jsonl — one line per incident: when it started/ended, which servers, how long (MTTR)

Person 3 reads these for MTTR and as labels for anomaly detection.
"""

from __future__ import annotations

import itertools
import json
import logging
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("controller")


class EventLog:
    def __init__(self, path: str | None = None, wall=time.time, keep: int = 500):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.wall = wall
        self.recent: deque[dict] = deque(maxlen=keep)
        self.listeners = []

    def emit(self, type_: str, **fields) -> dict:
        ev = {"time": round(self.wall(), 3), "type": type_, **fields}
        self.recent.append(ev)
        log.info("event %s", json.dumps(ev))
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(ev) + "\n")
        for fn in self.listeners:
            fn(ev)
        return ev


class IncidentTracker:
    """An incident opens when something breaks and closes when everything is healthy again."""

    def __init__(self, events: EventLog, path: str | None = None, wall=time.time):
        self.events = events
        self.path = Path(path) if path else None
        self.wall = wall
        self.current: dict | None = None
        self.closed: deque[dict] = deque(maxlen=100)
        self._ids = itertools.count(1)
        self.total = 0
        events.listeners.append(self._on_event)

    def open(self, reason: str) -> None:
        if self.current is not None:
            return
        self.total += 1
        self.current = {
            "id": f"INC-{int(self.wall())}-{next(self._ids)}",
            "started": round(self.wall(), 3),
            "reason": reason,
            "servers": [],
            "restarts": 0,
            "emergency": False,
            "timeline": [],
        }
        self.events.emit("incident_opened", incident=self.current["id"], reason=reason)

    def close(self) -> dict | None:
        inc = self.current
        if inc is None:
            return None
        inc["ended"] = round(self.wall(), 3)
        inc["duration_s"] = round(inc["ended"] - inc["started"], 3)
        self.current = None  # before emit, so the close event isn't added to its own timeline
        self.closed.append(inc)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(inc) + "\n")
        self.events.emit("incident_closed", incident=inc["id"], duration_s=inc["duration_s"])
        return inc

    def _on_event(self, ev: dict) -> None:
        inc = self.current
        if inc is None or ev["type"] == "incident_opened":
            return
        inc["timeline"].append(ev)
        server = ev.get("server")
        if server and server not in inc["servers"] and ev["type"] in ("server_down", "quarantined"):
            inc["servers"].append(server)
        if ev["type"] == "restart":
            inc["restarts"] += 1
        if ev["type"] == "emergency_on":
            inc["emergency"] = True
