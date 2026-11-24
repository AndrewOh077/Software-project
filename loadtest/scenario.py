"""All load-test scenarios in one file; SCENARIO picks one (default: normal). About 3 minutes each.

    normal  20 users, calm                                        the baseline
    spike   10 -> 150 users for a minute                          scenario 1: rate limiting
    kill    20 users; app2 crashes at 60 s, started at 120 s      scenario 2
    errors  20 users; app3 fails 30% from 60-120 s, but stays "healthy" (only the AI can see it)
    slow    20 users; app3 +800 ms from 60-120 s                  scenario 5

    $env:SCENARIO="kill"; python -m locust -f loadtest/scenario.py --headless --host http://localhost:8080

Faults are made with Person 1's chaos/chaos.py, which logs them to data/chaos.jsonl: the ground
truth for MTTR (experiments/mttr.py) and for scoring the AI. TARGET=app1 picks another server.
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

SCENARIO = os.getenv("SCENARIO", "normal")
FAULTS = {"kill": ("app2", "start"), "errors": ("app3", "clear"), "slow": ("app3", "clear")}  # target, undo
if SCENARIO not in {"normal", "spike", *FAULTS}:
    raise SystemExit(f"SCENARIO must be normal, spike, {', '.join(FAULTS)}")
TARGET = os.getenv("TARGET", FAULTS.get(SCENARIO, ("",))[0])
CHAOS = Path(__file__).resolve().parent.parent / "chaos" / "chaos.py"


class User(ResiliGuardUser):
    pass


class Shape(LoadTestShape):
    timeline = {"normal": stages.normal, "spike": stages.spike}.get(SCENARIO, stages.fault)()

    def tick(self):
        return stages.users_at(self.timeline, self.get_run_time())


def chaos(action: str) -> None:
    # a subprocess, so chaos.py logs exactly as when a person runs it
    subprocess.run([sys.executable, str(CHAOS), action, TARGET], check=False)


def fault_timeline() -> None:
    gevent.sleep(stages.scaled(stages.FAULT_AT))
    chaos(SCENARIO)
    gevent.sleep(stages.scaled(stages.FAULT_END) - stages.scaled(stages.FAULT_AT))
    chaos(FAULTS[SCENARIO][1])  # for kill: a no-op if the controller already brought it back


@events.test_start.add_listener
def start_fault(environment, **_):
    if SCENARIO in FAULTS and not isinstance(environment.runner, WorkerRunner):
        gevent.spawn(fault_timeline)
