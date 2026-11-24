"""Scenario: normal traffic, 20 users for 3 minutes. The calm baseline.

python -m locust -f loadtest/scenario_normal.py --headless --host http://localhost:8080
"""

import stages
from common import ResiliGuardUser
from locust import LoadTestShape


class NormalUser(ResiliGuardUser):
    pass


class NormalShape(LoadTestShape):
    def tick(self):
        return stages.users_at(stages.normal(), self.get_run_time())
