"""Scenario 3 — A server dies while traffic is flowing.

    users  20 |______________________________
              0      60s         120s       180s
    app-2     |  UP  |  DOWN (killed) | UP (restarted)

Purpose: the core "self-healing" test (Integration #1: "서버 하나를 죽여도 나머지가 계속 대답한다").
  - At 60s we kill one server (CHAOS_TARGET), at 120s we bring it back.
  - The gateway should stop sending it traffic within a few seconds, and the other two
    should keep answering. When it returns, it warms up, then gets traffic again.
Expect on the dashboard: "Backend health" bar turns red then green; "Traffic sent to each
backend" shows two lines going up while one drops to 0; a few errors at most.

Events written to events.jsonl (for the Week 4 MTTR calculator):
  chaos: server_killed  ->  gateway: backend_marked_unhealthy  ->  ...  ->
  gateway: backend_marked_healthy        MTTR = healthy.ts - server_killed.ts

How the kill works:
  - With fakes: POST {CHAOS_TARGET}/chaos/down, later /chaos/ok.
  - With real servers (Week 3+): Person 1's chaos script kills the container instead;
    set CHAOS_TARGET="" to disable this built-in switch and run their script on the same timer.

Run:  CHAOS_TARGET=http://localhost:8002 \
        locust -f loadtest/scenario_failure.py --headless --host http://localhost:8000
"""

import os

import gevent
import requests
import stages
from common import ResiliGuardUser, write_event
from locust import LoadTestShape, events
from locust.runners import WorkerRunner

CHAOS_TARGET = os.getenv("CHAOS_TARGET", "http://app-2:8000")
CHAOS_SERVER_ID = os.getenv("CHAOS_SERVER_ID", "app-2")


class FailureUser(ResiliGuardUser):
    pass


class FailureShape(LoadTestShape):
    def tick(self):
        return stages.users_at(stages.failure(), self.get_run_time())


def _chaos(mode: str) -> None:
    try:
        requests.post(f"{CHAOS_TARGET}/chaos/{mode}", timeout=2)
    except requests.RequestException as e:
        print(f"[chaos] could not reach {CHAOS_TARGET}: {e}")


def _chaos_timeline() -> None:
    gevent.sleep(stages.scaled(stages.FAILURE_KILL_AT))
    print(f"[chaos] killing {CHAOS_SERVER_ID}")
    write_event("chaos", "server_killed", CHAOS_SERVER_ID, method="fake_chaos_switch")
    _chaos("down")

    gevent.sleep(stages.scaled(stages.FAILURE_RESTORE_AT) - stages.scaled(stages.FAILURE_KILL_AT))
    print(f"[chaos] restarting {CHAOS_SERVER_ID}")
    _chaos("ok")


@events.test_start.add_listener
def start_chaos(environment, **kwargs):
    # Run the kill/restore timeline once — never on each distributed worker.
    if CHAOS_TARGET and not isinstance(environment.runner, WorkerRunner):
        gevent.spawn(_chaos_timeline)
