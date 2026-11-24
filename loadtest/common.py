"""Shared pieces for every Locust scenario (Person 3).

A Locust "user" is a fake person in a loop: send a request, wait, send another.
Each user sends about 1 request per second, so "20 users" = about 20 requests per second.
Requests go through the real gateway: GET /work?ms=50&priority=normal|critical.
"""

from locust import HttpUser, constant_pacing, task


class ResiliGuardUser(HttpUser):
    abstract = True  # scenario files make concrete subclasses
    wait_time = constant_pacing(1)

    def _call(self, priority: str) -> None:
        with self.client.get(
            f"/work?ms=50&priority={priority}",
            name=f"/work [{priority}]",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                r.success()
            elif r.status_code == 429 or (r.status_code == 503 and "emergency" in r.text):
                # refused ON PURPOSE by the gateway (rate limit, concurrency limit, emergency)
                r.failure(f"rejected:{r.status_code}")
            else:
                r.failure(f"error:{r.status_code}")  # a real failure: 500 from an app, no backend...

    @task(9)
    def normal_request(self):
        self._call("normal")

    @task(1)
    def critical_request(self):
        """10% of traffic is 'critical' (e.g. checkout). The gateway never rate-limits these."""
        self._call("critical")
