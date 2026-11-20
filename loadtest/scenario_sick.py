"""Scenario 4 (Week 8–9) — A server gets sick but does NOT die.

    users  20 |______________________________
              0      60s         120s       180s
    app-3     |  OK  | 30% errors, still   | OK
              |      | says "I'm ready"    |

Purpose: test the AI early warning (ai/anomaly.py). Health checks only ask "/ready?", and
app-3 keeps answering "yes", so the gateway keeps sending it traffic and users see errors.
Only something watching the NUMBERS (error ratio, latency) can notice.

Events written to events.jsonl:
  chaos: errors_injected {error_rate, duration_sec}   <- ground truth for the AI evaluation

Run (fakes):  CHAOS_TARGET=http://localhost:8003 CHAOS_SERVER_ID=app-3 \
                locust -f loadtest/scenario_sick.py --headless --host http://localhost:8000
"""

import os

import gevent
import requests
import stages
from common import ResiliGuardUser, write_event
from locust import LoadTestShape, events
from locust.runners import WorkerRunner

CHAOS_TARGET = os.getenv("CHAOS_TARGET", "http://app-3:8000")
CHAOS_SERVER_ID = os.getenv("CHAOS_SERVER_ID", "app-3")
ERROR_RATE = float(os.getenv("SICK_ERROR_RATE", "0.3"))


class SickUser(ResiliGuardUser):
    pass


class SickShape(LoadTestShape):
    def tick(self):
        return stages.users_at(stages.failure(), self.get_run_time())


def _chaos(mode: str, **params) -> None:
    try:
        requests.post(f"{CHAOS_TARGET}/chaos/{mode}", params=params, timeout=2)
    except requests.RequestException as e:
        print(f"[chaos] could not reach {CHAOS_TARGET}: {e}")


def _sick_timeline() -> None:
    start, end = stages.scaled(stages.FAILURE_KILL_AT), stages.scaled(stages.FAILURE_RESTORE_AT)
    gevent.sleep(start)
    print(f"[chaos] making {CHAOS_SERVER_ID} fail {ERROR_RATE:.0%} of requests")
    write_event(
        "chaos", "errors_injected", CHAOS_SERVER_ID, error_rate=ERROR_RATE, duration_sec=end - start
    )
    _chaos("error", error_rate=ERROR_RATE)
    gevent.sleep(end - start)
    print(f"[chaos] {CHAOS_SERVER_ID} healthy again")
    _chaos("ok")


@events.test_start.add_listener
def start_chaos(environment, **kwargs):
    if CHAOS_TARGET and not isinstance(environment.runner, WorkerRunner):
        gevent.spawn(_sick_timeline)
