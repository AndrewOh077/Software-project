"""Scenario 1 — Normal traffic.

    users
     20 |  ___________________
        | /                   \
      0 +--------------------------> time
        0  15s              165s 180s

Purpose: the baseline. Every other scenario is compared against how calm this one looks.
Expect: ~20 req/s, ~0% errors, p99 latency well under 100 ms.

Run:  locust -f loadtest/scenario_normal.py --headless --host http://localhost:8000
"""

import stages
from common import ResiliGuardUser
from locust import LoadTestShape


class NormalUser(ResiliGuardUser):
    pass


class NormalShape(LoadTestShape):
    def tick(self):
        return stages.users_at(stages.normal(), self.get_run_time())
