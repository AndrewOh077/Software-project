"""Shared pieces for every Locust scenario.

A Locust "user" is a fake person in a loop: send a request, wait, send another.
Each scenario file imports ResiliGuardUser and adds a *shape* (how many users over time).
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from locust import HttpUser, constant_pacing, task


def write_event(source: str, event: str, server_id: str | None = None, **detail) -> None:
    """Same shape as fakes/events.py (docs/contracts.md, "Incident log line")."""
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "source": source,
        "event": event,
        "server_id": server_id,
        "detail": detail,
    }
    path = Path(os.getenv("EVENTS_FILE", "experiments/results/raw/events.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


REJECTION_CODES = {429, 503}


class ResiliGuardUser(HttpUser):
    """One fake user = about 1 request per second.

    constant_pacing(1): each loop takes 1 s total, so N users ≈ N requests/sec.
    That makes "users" and "requests per second" the same number, which keeps the
    scenarios easy to reason about.
    """

    abstract = True  # scenario files make concrete subclasses
    wait_time = constant_pacing(1)

    def _call(self, priority: str) -> None:
        with self.client.get(
            "/work",
            headers={"X-Priority": priority},
            name=f"/work [{priority}]",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                r.success()
            else:
                # Contract 5a: a refusal ON PURPOSE has an "error" field (rate_limited, ...).
                # A 503/500 without it came from a broken app server — that's a real error.
                try:
                    reason = r.json().get("error")
                except (ValueError, AttributeError):
                    reason = None
                if r.status_code in REJECTION_CODES and reason:
                    r.failure(f"rejected:{r.status_code}:{reason}")
                else:
                    r.failure(f"error:{r.status_code}")

    @task(9)
    def normal_request(self):
        self._call("normal")

    @task(1)
    def critical_request(self):
        """10% of traffic is 'critical' (e.g. checkout). Week 4 priority queue protects these."""
        self._call("critical")
