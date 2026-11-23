#!/usr/bin/env python3
"""
Chaos tool — breaks app servers on purpose. Standard library only; runs on your machine
(needs `docker` on PATH and the app ports 8001-8003 published).

Every action is appended to data/chaos.jsonl with a timestamp. Person 3 uses that file as
the "ground truth" of when failures really happened (for MTTR and anomaly-detection labels).

  python chaos/chaos.py status
  python chaos/chaos.py kill app2              # hard crash (container killed)
  python chaos/chaos.py stop app2              # graceful stop
  python chaos/chaos.py pause app2             # freeze: process exists but never answers
  python chaos/chaos.py unpause app2
  python chaos/chaos.py start app2
  python chaos/chaos.py slow app2 --ms 800     # add latency to /work
  python chaos/chaos.py errors app2 --rate 0.3 # 30% of /work return 500
  python chaos/chaos.py sick app2              # /health and /ready return 503
  python chaos/chaos.py notready app2          # alive, but /ready returns 503
  python chaos/chaos.py clear app2             # remove injected faults
  python chaos/chaos.py break app2             # broken for good: restarting won't fix it
  python chaos/chaos.py fix app2               # undo `break` (like a human fixing the config)
  python chaos/chaos.py random --duration 300 --every 30 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
PORTS = {"app1": 8001, "app2": 8002, "app3": 8003}
LOG = Path(__file__).resolve().parent.parent / "data" / "chaos.jsonl"
FAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "faults"   # mounted into apps as /faults


def record(action: str, target: str | None, ok: bool, **extra) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    ev = {"time": round(time.time(), 3), "action": action, "target": target, "ok": ok, **extra}
    with LOG.open("a") as f:
        f.write(json.dumps(ev) + "\n")
    mark = "✓" if ok else "✗"
    print(f"{mark} {action} {target or ''} {json.dumps(extra) if extra else ''}".rstrip())


def docker(*args: str) -> bool:
    r = subprocess.run(["docker", *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  docker {' '.join(args)}: {r.stderr.strip()}", file=sys.stderr)
    return r.returncode == 0


def http(method: str, url: str, body: dict | None = None, timeout=2.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, None


def fault(target: str, cfg: dict | None) -> bool:
    url = f"http://localhost:{PORTS[target]}/admin/fault"
    code, _ = http("DELETE", url) if cfg is None else http("POST", url, cfg)
    return code == 200


# ------------------------------------------------------------------ actions
def act(action: str, target: str, ms: int = 800, rate: float = 0.3) -> bool:
    if action in ("kill", "stop", "pause", "unpause", "start"):
        ok = docker(action, target)
    elif action == "slow":
        ok = fault(target, {"latency_ms": ms})
    elif action == "errors":
        ok = fault(target, {"error_rate": rate})
    elif action == "sick":
        ok = fault(target, {"unhealthy": True})
    elif action == "notready":
        ok = fault(target, {"not_ready": True})
    elif action == "clear":
        ok = fault(target, None)
    elif action == "break":
        FAULT_DIR.mkdir(parents=True, exist_ok=True)
        (FAULT_DIR / f"{target}.broken").touch()
        ok = True
    elif action == "fix":
        (FAULT_DIR / f"{target}.broken").unlink(missing_ok=True)
        ok = True
    else:
        raise ValueError(action)
    extra = {"ms": ms} if action == "slow" else {"rate": rate} if action == "errors" else {}
    record(action, target, ok, **extra)
    return ok


def status() -> None:
    for name, port in PORTS.items():
        h, _ = http("GET", f"http://localhost:{port}/health", timeout=1)
        r, _ = http("GET", f"http://localhost:{port}/ready", timeout=1)
        _, f = http("GET", f"http://localhost:{port}/admin/fault", timeout=1)
        faults = {k: v for k, v in (f or {}).items() if k != "server" and v}
        print(f"{name}: health={h or 'no answer'} ready={r or 'no answer'} faults={faults or '-'}")
    code, st = http("GET", "http://localhost:9100/status", timeout=1)
    if code == 200:
        print(f"controller: emergency={st['emergency']} "
              + " ".join(f"{n}={v['state']}(w{v['weight']})" for n, v in st["servers"].items()))


def random_chaos(duration: float, every: float, targets: list[str], seed: int | None,
                 heal_after: float) -> None:
    """
    Every `every` seconds pick a random server and a random failure.
    Fault-injection failures (slow/errors/notready) are cleared after `heal_after` seconds;
    crashes (kill/pause) are left for the controller to fix — that's what we are testing.
    """
    rng = random.Random(seed)
    actions = ["kill", "pause", "slow", "errors", "notready"]
    record("random_start", None, True, duration=duration, every=every, seed=seed)
    end = time.time() + duration
    pending: list[tuple[float, str, str]] = []
    try:
        while time.time() < end:
            target, action = rng.choice(targets), rng.choice(actions)
            act(action, target, ms=rng.choice([300, 800, 2000]), rate=rng.choice([0.2, 0.5, 1.0]))
            if action in ("slow", "errors", "notready"):
                pending.append((time.time() + heal_after, "clear", target))
            next_at = time.time() + every
            while time.time() < min(next_at, end):
                for item in [p for p in pending if p[0] <= time.time()]:
                    pending.remove(item)
                    act(item[1], item[2])
                time.sleep(0.5)
    finally:
        for _, a, t in pending:
            act(a, t)
        record("random_end", None, True)


def main() -> None:
    p = argparse.ArgumentParser(description="Break app servers on purpose.")
    p.add_argument("action", choices=["status", "kill", "stop", "pause", "unpause", "start", "slow",
                                      "errors", "sick", "notready", "clear", "break", "fix", "random"])
    p.add_argument("target", nargs="?", choices=list(PORTS))
    p.add_argument("--ms", type=int, default=800, help="latency for 'slow'")
    p.add_argument("--rate", type=float, default=0.3, help="error rate for 'errors'")
    p.add_argument("--duration", type=float, default=300, help="random: total seconds")
    p.add_argument("--every", type=float, default=30, help="random: seconds between failures")
    p.add_argument("--heal-after", type=float, default=15, help="random: clear injected faults after")
    p.add_argument("--targets", default="app1,app2,app3", help="random: comma-separated servers")
    p.add_argument("--seed", type=int, default=None, help="random: same seed = same sequence")
    a = p.parse_args()

    if a.action == "status":
        return status()
    if a.action == "random":
        return random_chaos(a.duration, a.every, a.targets.split(","), a.seed, a.heal_after)
    if not a.target:
        p.error(f"'{a.action}' needs a target: {', '.join(PORTS)}")
    sys.exit(0 if act(a.action, a.target, a.ms, a.rate) else 1)


if __name__ == "__main__":
    main()
