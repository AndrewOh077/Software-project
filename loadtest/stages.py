"""Scenario timelines as plain tables: no Locust import, so they're easy to read and test.

Each stage is (end_time_sec, users, spawn_rate_per_sec). SCENARIO_SCALE=0.3 shortens everything.
"""

import os

SCALE = float(os.getenv("SCENARIO_SCALE", "1"))
FAULT_AT, FAULT_END = 60, 120  # scenario.py: break at 60 s, undo at 120 s

Stage = tuple[int, int, int]


def scaled(seconds: float) -> int:
    return max(1, round(seconds * SCALE))


def normal() -> list[Stage]:
    return [(scaled(15), 20, 2), (scaled(165), 20, 2), (scaled(180), 0, 5)]


def spike() -> list[Stage]:
    # 10 -> 150 users (15x) for a minute, like a sale starting
    return [(scaled(60), 10, 5), (scaled(120), 150, 100), (scaled(180), 10, 100)]


def fault() -> list[Stage]:
    return [(scaled(180), 20, 5)]  # steady traffic; the fault happens on a timer


def users_at(stages: list[Stage], t: float) -> tuple[int, int] | None:
    """Which (users, spawn_rate) applies at time t? None = test is over."""
    for end, users, rate in stages:
        if t < end:
            return users, rate
    return None
