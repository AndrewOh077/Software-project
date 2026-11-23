"""
Unit tests for the controller state machine. No Docker, no network, no waiting:
a fake clock, fake servers, fake docker and fake gateway.

Run: python -m unittest discover -s tests/unit -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from controller.adapters import Probe  # noqa: E402
from controller.config import Config  # noqa: E402
from controller.core import Controller, State  # noqa: E402
from controller.events import EventLog  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeWorld:
    """Pretends to be 3 app servers + docker. `restart` brings a server back after `boot_s`."""

    def __init__(self, clock, names=("app1", "app2", "app3"), boot_s=2.0):
        self.clock = clock
        self.boot_s = boot_s
        self.live = {n: True for n in names}
        self.ready = {n: True for n in names}
        self.ready_at = {n: None for n in names}
        self.broken_forever = set()   # restarting these never helps
        self.restarts = []

    # prober interface
    def check(self, name, url):
        at = self.ready_at[name]
        if at is not None and self.clock() >= at:
            self.live[name] = self.ready[name] = True
            self.ready_at[name] = None
        return Probe(self.live[name], self.ready[name])

    # docker interface
    def restart(self, name):
        self.restarts.append((self.clock(), name))
        if name not in self.broken_forever:
            self.live[name] = True
            self.ready[name] = False
            self.ready_at[name] = self.clock() + self.boot_s
        return True

    def kill(self, name):
        self.live[name] = self.ready[name] = False
        self.ready_at[name] = None


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.mode = "normal"

    def disable(self, s): self.calls.append(("disable", s)); return True
    def enable(self, s): self.calls.append(("enable", s)); return True
    def set_weight(self, s, w): self.calls.append(("weight", s, w)); return True

    def set_mode(self, m):
        self.calls.append(("mode", m))
        self.mode = m
        return True

    def weights(self, server):
        return [c[2] for c in self.calls if c[0] == "weight" and c[1] == server]


class FakeAlerter:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


def make(cfg=None, **world_kw):
    clock = Clock()
    world = FakeWorld(clock, **world_kw)
    gw = FakeGateway()
    alerts = FakeAlerter()
    events = EventLog(None, wall=clock)
    c = Controller(
        cfg or Config(),
        {n: f"http://{n}:8000" for n in world.live},
        prober=world, docker=world, gateway=gw, events=events, alerter=alerts,
        clock=clock, async_restart=False,
    )
    return c, world, gw, alerts, clock


def run(c, clock, seconds, step=1.0):
    for _ in range(int(seconds / step)):
        c.tick()
        clock.t += step


def types(c):
    return [e["type"] for e in c.events.recent]


class TestStartup(unittest.TestCase):
    def test_all_servers_become_healthy_and_enabled(self):
        c, world, gw, _, clock = make()
        run(c, clock, 1)
        for s in c.servers.values():
            self.assertEqual(s.state, State.HEALTHY)
        self.assertIn(("enable", "app1"), gw.calls)
        self.assertIn(("weight", "app1", 100), gw.calls)
        self.assertFalse(c.emergency)

    def test_slow_starting_server_is_not_restarted_during_startup_grace(self):
        c, world, gw, _, clock = make()
        world.ready["app2"] = False
        world.ready_at["app2"] = clock.t + 10
        run(c, clock, 12)
        self.assertEqual(world.restarts, [])
        self.assertEqual(c.servers["app2"].state, State.HEALTHY)


class TestDetectionAndRecovery(unittest.TestCase):
    def test_one_failed_probe_is_only_suspect(self):
        c, world, gw, _, clock = make()
        run(c, clock, 2)
        world.ready["app2"] = False
        run(c, clock, 1)
        self.assertEqual(c.servers["app2"].state, State.SUSPECT)
        world.ready["app2"] = True
        run(c, clock, 1)
        self.assertEqual(c.servers["app2"].state, State.HEALTHY)
        self.assertNotIn(("disable", "app2"), gw.calls)
        self.assertEqual(world.restarts, [])

    def test_killed_server_is_removed_restarted_and_ramped_back(self):
        cfg = Config()
        c, world, gw, _, clock = make(cfg)
        run(c, clock, 2)
        world.kill("app2")
        run(c, clock, cfg.fail_threshold)            # detection
        self.assertIn(("disable", "app2"), gw.calls)
        self.assertEqual(len(world.restarts), 1)     # restarted immediately once DOWN
        self.assertEqual(c.servers["app2"].state, State.RESTARTING)

        run(c, clock, 30)                            # boot + ramp
        self.assertEqual(c.servers["app2"].state, State.HEALTHY)
        self.assertEqual(gw.weights("app2")[-4:], [10, 25, 50, 100])
        self.assertIsNotNone(c.servers["app2"].last_recovery_s)
        # the other servers were never touched
        self.assertNotIn(("disable", "app1"), gw.calls)
        self.assertNotIn(("disable", "app3"), gw.calls)

    def test_server_that_comes_back_by_itself_is_not_restarted(self):
        cfg = Config(not_ready_grace_s=20)
        c, world, gw, _, clock = make(cfg)
        run(c, clock, 2)
        world.ready["app1"] = False                  # alive but not ready (e.g. overloaded)
        run(c, clock, 5)
        self.assertEqual(c.servers["app1"].state, State.DOWN)
        self.assertEqual(world.restarts, [])         # within grace period: no restart
        world.ready["app1"] = True
        run(c, clock, 15)
        self.assertEqual(c.servers["app1"].state, State.HEALTHY)
        self.assertEqual(world.restarts, [])

    def test_alive_but_stuck_server_is_restarted_after_grace(self):
        cfg = Config(not_ready_grace_s=10)
        c, world, gw, _, clock = make(cfg)
        run(c, clock, 2)
        world.ready["app1"] = False
        run(c, clock, 8)
        self.assertEqual(world.restarts, [])
        run(c, clock, 6)
        self.assertEqual(len(world.restarts), 1)


class TestRestartLimitAndEmergency(unittest.TestCase):
    def test_server_that_never_recovers_is_quarantined_with_alert(self):
        cfg = Config(max_restarts=3, ready_timeout_s=5, backoff_base_s=1)
        c, world, gw, alerts, clock = make(cfg)
        run(c, clock, 2)
        world.broken_forever.add("app2")
        world.kill("app2")
        run(c, clock, 60)
        self.assertEqual(len(world.restarts), 3)     # gave up after the limit
        self.assertEqual(c.servers["app2"].state, State.QUARANTINED)
        self.assertTrue(any("app2" in a for a in alerts.sent))

    def test_quarantine_turns_on_emergency_mode(self):
        cfg = Config(max_restarts=2, ready_timeout_s=3, backoff_base_s=1)
        c, world, gw, alerts, clock = make(cfg)
        run(c, clock, 2)
        world.broken_forever.add("app1")
        world.kill("app1")
        run(c, clock, 30)
        self.assertTrue(c.emergency)
        self.assertEqual(gw.mode, "emergency")

    def test_operator_release_leads_to_recovery_and_emergency_off(self):
        cfg = Config(max_restarts=2, ready_timeout_s=3, backoff_base_s=1, exit_stable_s=5)
        c, world, gw, alerts, clock = make(cfg)
        run(c, clock, 2)
        world.broken_forever.add("app1")
        world.kill("app1")
        run(c, clock, 30)
        self.assertEqual(c.servers["app1"].state, State.QUARANTINED)

        world.broken_forever.discard("app1")         # "a human fixed it"
        self.assertTrue(c.release("app1")["ok"])
        run(c, clock, 40)
        self.assertEqual(c.servers["app1"].state, State.HEALTHY)
        self.assertFalse(c.emergency)
        self.assertEqual(gw.mode, "normal")

    def test_release_clears_restart_history_of_healthy_server(self):
        cfg = Config(max_restarts=2)
        c, world, gw, alerts, clock = make(cfg)
        run(c, clock, 2)
        world.kill("app1")
        run(c, clock, 30)
        self.assertEqual(len(c.servers["app1"].restart_times), 1)
        self.assertEqual(c.release("app1"), {"ok": True, "action": "restart history cleared"})
        self.assertEqual(len(c.servers["app1"].restart_times), 0)
        self.assertEqual(c.servers["app1"].state, State.HEALTHY)
        self.assertFalse(c.release("nope")["ok"])

    def test_all_servers_down_is_emergency_then_recovers(self):
        cfg = Config(exit_stable_s=5)
        c, world, gw, alerts, clock = make(cfg)
        run(c, clock, 2)
        for n in ("app1", "app2", "app3"):
            world.kill(n)
        run(c, clock, cfg.fail_threshold + 1)
        self.assertTrue(c.emergency)
        self.assertIn("emergency_on", types(c))
        run(c, clock, 40)
        self.assertFalse(c.emergency)
        self.assertIn("emergency_off", types(c))
        self.assertTrue(all(s.state == State.HEALTHY for s in c.servers.values()))

    def test_one_server_down_is_not_emergency(self):
        c, world, gw, alerts, clock = make()
        run(c, clock, 2)
        world.kill("app2")
        run(c, clock, 30)
        self.assertNotIn("emergency_on", types(c))

    def test_restart_backoff_grows(self):
        cfg = Config(max_restarts=10, restart_window_s=1000, ready_timeout_s=2,
                     backoff_base_s=2, backoff_max_s=100)
        c, world, gw, alerts, clock = make(cfg)
        run(c, clock, 2)
        world.broken_forever.add("app3")
        world.kill("app3")
        run(c, clock, 60, step=0.5)
        times = [t for t, n in world.restarts if n == "app3"]
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertGreaterEqual(len(gaps), 3)
        self.assertTrue(all(b > a for a, b in zip(gaps, gaps[1:])), gaps)


class TestEarlyWarning(unittest.TestCase):
    def test_warning_reduces_weight_temporarily(self):
        cfg = Config(warn_weight=50, warn_duration_s=10)
        c, world, gw, alerts, clock = make(cfg)
        run(c, clock, 2)
        r = c.warn("app2", score=0.9, reason="p99 rising")
        self.assertTrue(r["accepted"])
        self.assertEqual(gw.weights("app2")[-1], 50)
        run(c, clock, 12)
        self.assertEqual(gw.weights("app2")[-1], 100)
        self.assertEqual(world.restarts, [])         # a warning never restarts anything

    def test_warning_for_down_server_is_ignored(self):
        c, world, gw, alerts, clock = make()
        run(c, clock, 2)
        world.broken_forever.add("app2")
        world.kill("app2")
        run(c, clock, 4)
        self.assertFalse(c.warn("app2")["accepted"])
        self.assertFalse(c.warn("nope")["accepted"])


class TestViews(unittest.TestCase):
    def test_status_and_metrics_render(self):
        c, world, gw, alerts, clock = make()
        run(c, clock, 2)
        st = c.status()
        self.assertEqual(st["servers"]["app1"]["state"], "HEALTHY")
        m = c.metrics()
        self.assertIn('controller_server_state{server="app1",state="HEALTHY"} 1', m)
        self.assertIn("controller_emergency_mode 0", m)


class TestAsyncRestart(unittest.TestCase):
    def test_api_stays_responsive_while_docker_restart_is_slow(self):
        import threading
        import time as _time
        c, world, gw, alerts, clock = make()
        c.async_restart = True
        started, release = threading.Event(), threading.Event()
        real_restart = world.restart

        def slow_restart(name):
            started.set()
            release.wait(5)          # pretend `docker restart` takes a long time
            return real_restart(name)

        world.restart = slow_restart
        run(c, clock, 2)
        world.kill("app2")
        run(c, clock, 3)
        self.assertTrue(started.wait(2), "restart was started")
        self.assertEqual(c.servers["app2"].state, State.RESTARTING)
        t0 = _time.monotonic()
        c.status()                   # must not wait for the slow restart
        c.release("app1")
        self.assertLess(_time.monotonic() - t0, 0.5)
        release.set()


class TestHangingProbe(unittest.TestCase):
    def test_hanging_probe_counts_as_failure_and_does_not_slow_others(self):
        import threading
        import time as _time
        cfg = Config(probe_timeout_s=0.2)
        c, world, gw, alerts, clock = make(cfg)
        c.async_restart = True
        world.restart = lambda name: True
        run(c, clock, 2)
        hang = threading.Event()
        real_check = world.check

        def check(name, url):
            if name == "app1":
                hang.wait(5)         # like a slow DNS lookup for a stopped container
            return real_check(name, url)

        world.check = check
        t0 = _time.monotonic()
        for _ in range(3):
            c.tick()
            clock.t += 1
        self.assertLess(_time.monotonic() - t0, 3.0, "3 ticks must not wait for the hanging probe")
        self.assertEqual(c.servers["app1"].state, State.RESTARTING, "hang counted as failure -> DOWN -> restart")
        self.assertEqual(c.servers["app2"].state, State.HEALTHY)
        hang.set()


class TestGatewayDown(unittest.TestCase):
    def test_recovery_still_works_when_gateway_calls_fail(self):
        c, world, gw, alerts, clock = make()
        gw.disable = gw.enable = lambda s: False
        gw.set_weight = lambda s, w: False
        run(c, clock, 2)
        world.kill("app1")
        run(c, clock, 30)
        self.assertEqual(len(world.restarts), 1)
        self.assertEqual(c.servers["app1"].state, State.HEALTHY)


if __name__ == "__main__":
    unittest.main()
