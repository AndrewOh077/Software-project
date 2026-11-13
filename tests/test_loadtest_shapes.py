"""The three scenarios ask for the right number of users at the right time.

Tests read loadtest/stages.py (plain tables) instead of the Locust files on purpose:
importing Locust patches Python's networking (gevent) and freezes the web-server tests.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loadtest"))

import stages  # noqa: E402


def users(stage_list, t):
    result = stages.users_at(stage_list, t)
    return None if result is None else result[0]


@pytest.mark.parametrize(("t", "expected"), [(5, 20), (100, 20), (170, 0), (181, None)])
def test_normal(t, expected):
    assert users(stages.normal(), t) == expected


@pytest.mark.parametrize(
    ("t", "expected"), [(30, 10), (61, 150), (119, 150), (121, 10), (181, None)]
)
def test_spike(t, expected):
    assert users(stages.spike(), t) == expected


@pytest.mark.parametrize(("t", "expected"), [(0, 20), (179, 20), (181, None)])
def test_failure(t, expected):
    assert users(stages.failure(), t) == expected


def test_kill_happens_while_traffic_flows():
    assert 0 < stages.FAILURE_KILL_AT < stages.FAILURE_RESTORE_AT < 180


def test_locust_files_use_the_tables():
    root = Path(__file__).resolve().parent.parent / "loadtest"
    for name, fn in [("normal", "normal"), ("spike", "spike"), ("failure", "failure")]:
        assert f"stages.{fn}()" in (root / f"scenario_{name}.py").read_text()
