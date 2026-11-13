"""Scenario 2 — Sudden traffic spike.

    users
    150 |          ______
        |         |      |
     10 |_________|      |_________
      0 +-------------------------------> time
        0        60s   120s       180s

Purpose: what happens when 15x more people show up at once (e.g. a sale starts)?
  - Mode A (no protection): latency explodes, servers fall over.
  - Mode B (rate limit / queue): extra requests get 429 quickly, the rest stay fast.
Expect on the dashboard: "Requests / sec" jumps; with RATE_LIMIT_RPS set, the
"Rejected by reason" graph shows rate_limited during the spike.

Run:  locust -f loadtest/scenario_spike.py --headless --host http://localhost:8000
"""

import stages
from common import ResiliGuardUser
from locust import LoadTestShape


class SpikeUser(ResiliGuardUser):
    pass


class SpikeShape(LoadTestShape):
    def tick(self):
        return stages.users_at(stages.spike(), self.get_run_time())
