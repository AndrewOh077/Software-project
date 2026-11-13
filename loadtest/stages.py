"""Scenario timelines as plain tables — no Locust import, so they're easy to read and test.

Each stage is (end_time_sec, users, spawn_rate_per_sec): "until END seconds, run USERS users,
adding/removing SPAWN_RATE of them per second to get there".
Because each user sends ~1 request/sec (see common.py), users ≈ requests per second.
"""

import os

# Time multiplier: SCENARIO_SCALE=0.1 turns a 3-minute scenario into ~18 seconds.
SCALE = float(os.getenv("SCENARIO_SCALE", "1"))

Stage = tuple[int, int, int]


def scaled(seconds: float) -> int:
    return max(1, round(seconds * SCALE))


def normal() -> list[Stage]:
    return [
        (scaled(15), 20, 2),  # ramp up gently
        (scaled(165), 20, 2),  # hold 20 users
        (scaled(180), 0, 5),  # ramp down
    ]


def spike() -> list[Stage]:
    return [
        (scaled(60), 10, 5),  # calm
        (scaled(120), 150, 100),  # spike: 15x, almost instantly
        (scaled(180), 10, 100),  # drop back
    ]


def failure() -> list[Stage]:
    return [(scaled(180), 20, 5)]  # steady; the kill happens on a timer (scenario_failure.py)


FAILURE_KILL_AT = 60
FAILURE_RESTORE_AT = 120


def users_at(stages: list[Stage], t: float) -> tuple[int, int] | None:
    """Which (users, spawn_rate) applies at time t? None = test is over."""
    for end, users, rate in stages:
        if t < end:
            return users, rate
    return None
