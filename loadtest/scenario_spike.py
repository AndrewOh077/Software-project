"""Scenario 1 (docs/scenarios.md): sudden traffic spike, 10 -> 150 users for a minute.

The gateway should refuse the extra normal requests (429) and keep every critical one.

    python -m locust -f loadtest/scenario_spike.py --headless --host http://localhost:8080
"""

import stages
from common import ResiliGuardUser
from locust import LoadTestShape


class SpikeUser(ResiliGuardUser):
    pass


class SpikeShape(LoadTestShape):
    def tick(self):
        return stages.users_at(stages.spike(), self.get_run_time())
