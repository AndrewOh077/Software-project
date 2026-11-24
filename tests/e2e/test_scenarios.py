"""
Week 6: the 8 test scenarios from the project plan, run against the REAL Docker stack.
Week 7: GitHub Actions runs this file automatically on every push / PR.

Before running:  docker compose up -d --build
Run all:         python -m unittest discover -s tests/e2e -v
Run one:         python -m unittest discover -s tests/e2e -v -k test_s2

Scenarios marked "Person 2" need the real Gateway (rate limiting, 503 responses).
They are skipped until you set REAL_GATEWAY=1. Person 2 fills them in during Week 6.
"""

import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = "http://localhost:9100"
GATEWAY = os.getenv("GATEWAY_ADMIN", "http://localhost:8080")
REAL_GATEWAY = os.getenv("REAL_GATEWAY") == "1"
RECOVERY_SLO_S = 60          # a crashed server must be HEALTHY again within this
SERVERS = ("app1", "app2", "app3")


# ------------------------------------------------------------------ helpers
def http(method, url, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        with e:  # close it, so Python doesn't print ResourceWarning
            return e.code, json.loads(e.read() or b"null")


def get(url):
    return http("GET", url)[1]


def status():
    return get(f"{CONTROLLER}/status")


def state(server):
    return status()["servers"][server]["state"]


def gateway():
    return get(f"{GATEWAY}/admin/backends")


def events(types=None, since=0.0):
    evs = get(f"{CONTROLLER}/events?limit=500")
    return [e for e in evs if e["time"] >= since and (types is None or e["type"] in types)]


def docker(*args):
    subprocess.run(["docker", *args], check=True, capture_output=True)


def chaos(*args):
    subprocess.run([sys.executable, str(ROOT / "chaos" / "chaos.py"), *args],
                   check=True, capture_output=True)


def wait_until(pred, timeout, msg, every=1.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = pred()
            if last:
                return last
        except Exception as e:  # controller/gateway may be briefly unreachable
            last = e
        time.sleep(every)
    try:
        st = status()
    except Exception as e:
        st = f"<unavailable: {e}>"
    raise AssertionError(f"timed out after {timeout}s waiting for: {msg}\nlast={last!r}\nstatus={st}")


def restarts(server):
    return status()["servers"][server]["restarts_total"]


def recovered_again(server, before):
    """True once `server` has been restarted at least once more than `before` and is HEALTHY."""
    return lambda: restarts(server) > before and state(server) == "HEALTHY"


def all_healthy():
    st = status()
    return not st["emergency"] and all(s["state"] == "HEALTHY" for s in st["servers"].values())


# ------------------------------------------------------------------ scenarios
class Scenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wait_until(all_healthy, 120, "all servers HEALTHY at start")

    def setUp(self):
        # Clean slate: undo faults a previous (failed) test may have left behind.
        for s in SERVERS:
            chaos("fix", s)
            try:
                chaos("clear", s)
            except subprocess.CalledProcessError:
                pass  # server may be down right now; the controller will bring it back
            # Tests kill the same servers again and again within minutes. Without this,
            # the Week 5 restart limit would (correctly!) quarantine them mid-test.
            http("POST", f"{CONTROLLER}/admin/release/{s}")
        wait_until(all_healthy, 120, "system calm before test")
        self.t0 = status()["server_time"]   # container clock (may differ from this PC's clock)

    # 1 ─ 사용자 갑자기 증가 → Rate Limiting 적용  (Person 2)
    @unittest.skipUnless(REAL_GATEWAY, "Person 2: needs real Gateway rate limiting")
    def test_s1_traffic_spike_is_rate_limited(self):
        def call(priority):
            return http("GET", f"{GATEWAY}/work?ms=10&priority={priority}")[0]

        # 420 requests at once, every 20th one critical
        priorities = ["critical" if i % 20 == 0 else "normal" for i in range(420)]
        with ThreadPoolExecutor(max_workers=50) as pool:
            codes = list(pool.map(call, priorities))
        normal = [c for c, p in zip(codes, priorities) if p == "normal"]
        critical = [c for c, p in zip(codes, priorities) if p == "critical"]

        self.assertGreater(normal.count(429), 0, f"burst was not rate limited: {sorted(set(normal))}")
        self.assertIn(200, normal, "some normal requests should still be served")
        self.assertEqual(set(critical), {200}, f"critical requests must survive the spike: {critical}")
        wait_until(all_healthy, 30, "app servers stay HEALTHY after the spike")
        
    # 2 ─ Server B 종료 → B 제외 후 A와 C로 트래픽 분배
    def test_s2_one_server_killed(self):
        docker("kill", "app2")
        wait_until(lambda: gateway()["backends"]["app2"]["enabled"] is False, 15,
                   "app2 removed from the gateway")
        gw = gateway()["backends"]
        self.assertTrue(gw["app1"]["enabled"] and gw["app3"]["enabled"], "A and C keep serving")
        self.assertFalse(status()["emergency"], "one server down is not an emergency")

        wait_until(lambda: state("app2") == "HEALTHY", RECOVERY_SLO_S, "app2 HEALTHY again")
        self.assertEqual(gateway()["backends"]["app2"], {"enabled": True, "weight": 100})
        took = status()["servers"]["app2"]["last_recovery_s"]
        print(f"\n    app2 recovered in {took}s")

    # 3 ─ 서버 2개 동시 종료 → 남은 서버로 버팀 (용량에 맞춘 제한은 Person 2의 AIMD)
    def test_s3_two_servers_killed(self):
        docker("kill", "app1", "app3")
        # Both must be detected and taken out of the gateway (not necessarily at the same instant:
        # the controller may already be bringing app1 back while it handles app3).
        wait_until(lambda: {e["server"] for e in events({"server_down"}, self.t0)} >= {"app1", "app3"},
                   20, "app1 and app3 both detected as down")
        disabled = {c["path"] for c in get(f"{GATEWAY}/admin/calls")}
        self.assertIn("/admin/backends/app1/disable", disabled)
        self.assertIn("/admin/backends/app3/disable", disabled)
        self.assertIn(state("app2"), ("HEALTHY", "SUSPECT"), "app2 kept serving")
        wait_until(all_healthy, RECOVERY_SLO_S + 15, "app1+app3 recovered")
        self.assertFalse(events({"emergency_on"}, self.t0),
                         "one server always kept serving -> never an emergency")

    @unittest.skipUnless(REAL_GATEWAY, "Person 2: needs real Gateway AIMD limiter")
    def test_s3b_limit_shrinks_with_capacity(self):
        def limit():
            return get(f"{GATEWAY}/metrics")["concurrency_limit"]
        full = get(f"{GATEWAY}/metrics")["concurrency_max"]
        wait_until(lambda: limit() >= full * 0.9, 30, "limit at full capacity before the test")
        docker("kill", "app1", "app3")
        wait_until(lambda: limit() <= full * 0.4, 20, "limit shrinks to about 1/3")
        wait_until(all_healthy, RECOVERY_SLO_S + 15, "app1+app3 recovered")
        wait_until(lambda: limit() >= full * 0.9, 60, "limit grows back after recovery")

    # 4 ─ 서버 복구 실패 → 재시작 제한 → 격리 + Emergency Mode
    def test_s4_recovery_fails_then_emergency(self):
        chaos("break", "app2")      # restarting will NOT fix this
        docker("kill", "app2")
        wait_until(lambda: state("app2") == "QUARANTINED", 150, "app2 QUARANTINED")
        self.assertTrue(status()["emergency"])
        self.assertEqual(gateway()["mode"], "emergency")
        restarts = len(events({"restart"}, self.t0))
        self.assertLessEqual(restarts, 3, "gave up after the restart limit instead of looping")

        # a human fixes it and releases the server
        chaos("fix", "app2")
        code, body = http("POST", f"{CONTROLLER}/admin/release/app2")
        self.assertEqual(code, 200, body)
        wait_until(all_healthy, RECOVERY_SLO_S + 20, "app2 back and emergency off")
        self.assertEqual(gateway()["mode"], "normal")

    # 5 ─ 서버 응답속도 저하 → 재시작하지 않음 (느린 건 고장이 아님; 감지/제한은 Person 2·3)
    def test_s5_slow_server_is_not_restarted(self):
        before = restarts("app1")
        chaos("slow", "app1", "--ms", "800")
        time.sleep(10)
        self.assertIn(state("app1"), ("HEALTHY", "SUSPECT"))
        self.assertEqual(restarts("app1"), before, "a slow-but-alive server must not be restarted")
        chaos("clear", "app1")

    # 6 ─ 서버 다시 정상 작동 → Readiness 확인 후 점진적 복귀 (10→25→50→100%)
    def test_s6_gradual_return_after_recovery(self):
        before = restarts("app3")
        docker("kill", "app3")
        wait_until(recovered_again("app3", before), RECOVERY_SLO_S, "app3 restarted and recovered")
        calls = get(f"{GATEWAY}/admin/calls")
        weights = [c["body"]["weight"] for c in calls if c["path"] == "/admin/backends/app3/weight"]
        self.assertEqual(weights[-4:], [10, 25, 50, 100], f"weights sent: {weights}")
        ramp = [e for e in events({"state_change"}, self.t0)
                if e["server"] == "app3" and e["new"] == "WARMING"]
        self.assertTrue(ramp, "went through WARMING before HEALTHY")

    # 7 ─ 모든 App 서버 다운 → Emergency + 관리자 경고 (503 응답은 Person 2의 Gateway)
    def test_s7_all_servers_down(self):
        docker("kill", *SERVERS)
        wait_until(lambda: status()["emergency"], 20, "emergency mode ON")
        self.assertEqual(gateway()["mode"], "emergency")
        self.assertTrue(events({"emergency_on"}, self.t0), "emergency_on event (alert) recorded")
        wait_until(all_healthy, RECOVERY_SLO_S + 30, "everything recovered and emergency OFF")
        self.assertEqual(gateway()["mode"], "normal")

    @unittest.skipUnless(REAL_GATEWAY, "Person 2: needs real Gateway emergency behaviour")
    def test_s7b_gateway_returns_503_for_normal_requests(self):
        docker("kill", *SERVERS)
        wait_until(lambda: gateway()["mode"] == "emergency", 20, "gateway in emergency mode")
        code, body = http("GET", f"{GATEWAY}/work?priority=normal")
        self.assertEqual(code, 503, body)
        self.assertIn("emergency", body["detail"])
        code, body = http("GET", f"{GATEWAY}/work?priority=critical")
        self.assertNotIn("emergency", str(body.get("detail", "")), "critical requests must still be tried")
        wait_until(all_healthy, RECOVERY_SLO_S + 30, "everything recovered and emergency OFF")
        wait_until(lambda: http("GET", f"{GATEWAY}/work?ms=10")[0] == 200, 30,
                   "normal requests are served again")

    # 8 ─ AI 작동 중단 → Rule-based Controller가 계속 보호
    def test_s8_recovery_works_without_ai(self):
        # No warnings are sent at all (the AI is "down"). Rules alone must still heal.
        before = restarts("app2")
        docker("kill", "app2")
        wait_until(recovered_again("app2", before), RECOVERY_SLO_S, "app2 recovered with no AI involved")
        self.assertFalse(events({"warning"}, self.t0), "no AI warnings were involved")


    # Week 10 ─ 장애 기록: 장애 하나가 시작부터 끝까지 한 줄로 남는다
    def test_s9_incident_is_recorded_with_duration(self):
        before = restarts("app2")
        docker("kill", "app2")
        wait_until(recovered_again("app2", before), RECOVERY_SLO_S, "app2 recovered")

        def closed_since():
            return [i for i in get(f"{CONTROLLER}/incidents")["closed"] if i["started"] >= self.t0]

        inc = wait_until(closed_since, 15, "incident closed")[-1]
        self.assertIn("app2", inc["servers"])
        self.assertGreaterEqual(inc["restarts"], 1)
        self.assertGreater(inc["duration_s"], 0)
        self.assertIn("recovered", [e["type"] for e in inc["timeline"]])
        print(f"\n    incident {inc['id']} lasted {inc['duration_s']}s")


class EarlyWarning(unittest.TestCase):
    """Week 8: Controller에 조기 경고 연결"""

    def setUp(self):
        wait_until(all_healthy, 120, "system calm before test")

    def test_warning_reduces_traffic_temporarily_and_never_restarts(self):
        before = restarts("app1")
        code, body = http("POST", f"{CONTROLLER}/warning",
                          {"server": "app1", "score": 0.91, "reason": "e2e test", "source": "test"})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["accepted"])
        wait_until(lambda: gateway()["backends"]["app1"]["weight"] == body["weight"], 5,
                   "gateway weight reduced")
        self.assertTrue(status()["servers"]["app1"]["warned"])

        wait_until(lambda: gateway()["backends"]["app1"]["weight"] == 100,
                   body["duration_s"] + 10, "weight restored after the warning expires")
        self.assertEqual(restarts("app1"), before, "a warning must never restart a server")

    def test_bad_warnings_are_rejected(self):
        code, _ = http("POST", f"{CONTROLLER}/warning", {"server": "nope"})
        self.assertEqual(code, 409)
        code, _ = http("POST", f"{CONTROLLER}/warning", {"score": 0.5})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
