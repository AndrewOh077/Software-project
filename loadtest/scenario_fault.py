"""Scenarios 2 and 5 + the AI test: steady 20 users, one server breaks at 60 s, undone at 120 s.

    FAULT=kill   TARGET=app2  server crashes          (scenario 2; undo = docker start)
    FAULT=errors TARGET=app3  30% of its requests 500 (alive and "ready": only the AI can see it)
    FAULT=slow   TARGET=app3  +800 ms per request     (scenario 5)

The fault is made with Person 1's chaos/chaos.py, which logs it to data/chaos.jsonl:
that log is the ground truth for MTTR (experiments/mttr.py) and for scoring the AI.

    FAULT=errors TARGET=app3 python -m locust -f loadtest/scenario_fault.py --headless \
        --host http://localhost:8080
"""

import os
import subprocess
import sys
from pathlib import Path

import gevent
import stages
from common import ResiliGuardUser
from locust import LoadTestShape, events
from locust.runners import WorkerRunner

FAULT = os.getenv("FAULT", "kill")
TARGET = os.getenv("TARGET", "app2")
UNDO = {"kill": "start", "errors": "clear", "slow": "clear"}
CHAOS = Path(__file__).resolve().parent.parent / "chaos" / "chaos.py"


class FaultUser(ResiliGuardUser):
    pass


class FaultShape(LoadTestShape):
    def tick(self):
        return stages.users_at(stages.fault(), self.get_run_time())


def chaos(action: str) -> None:
    # stays in a subprocess so chaos.py logs exactly as when a person runs it
    subprocess.run([sys.executable, str(CHAOS), action, TARGET], check=False)


def timeline() -> None:
    gevent.sleep(stages.scaled(stages.FAULT_AT))
    chaos(FAULT)
    gevent.sleep(stages.scaled(stages.FAULT_END) - stages.scaled(stages.FAULT_AT))
    chaos(UNDO[FAULT])  # for kill: a no-op if the controller already brought it back


@events.test_start.add_listener
def start_fault(environment, **kwargs):
    if FAULT not in UNDO:
        raise SystemExit(f"FAULT must be one of {', '.join(UNDO)}")
    if not isinstance(environment.runner, WorkerRunner):
        gevent.spawn(timeline)
