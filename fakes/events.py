"""Write one incident event per line to events.jsonl (see docs/contracts.md, "Incident log line").

The MTTR calculator (Week 4) reads this file, so every part of the system writes the same shape.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path


def events_path() -> Path:
    return Path(os.getenv("EVENTS_FILE", "experiments/results/raw/events.jsonl"))


def write_event(source: str, event: str, server_id: str | None = None, **detail) -> dict:
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "source": source,
        "event": event,
        "server_id": server_id,
        "detail": detail,
    }
    path = events_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # Logging must never crash the server.
        pass
    return record
