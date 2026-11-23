"""
Event log: data/events.jsonl, one line per thing that happened
(server_down, restart, recovered, quarantined, emergency_on, ...).
Person 3 can compute recovery times (MTTR) from this file.
"""

from __future__ import annotations

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
