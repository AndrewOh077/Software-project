"""Person 3's whole experiment, in order, with one command (Windows, Mac and Linux):

    docker compose up -d --build
    pip install -r requirements-p3.txt
    python experiments/run_all.py                              # ~25 min (each scenario 90 s)
    $env:SCENARIO_SCALE="1"; python experiments/run_all.py     # ~55 min, full 3-minute scenarios

What it does
  1. Mode B (rules): normal, spike, kill, errors, slow      -> the Week 5 baseline
  2. Train the AI on Mode B's normal run                    -> ai/models/detector.pkl
  3. Mode C (rules + AI): the same 5, with the AI warning the controller
  4. Mode A (no protection): the same 5, controller stopped, gateway limits off
  5. Back to normal (Mode B), then the results:
       experiments/results/three-mode.md      the comparison table
       experiments/results/report.html        charts + AI results (open in a browser)
       experiments/results/postmortems/       one report per incident in Mode C (Week 10)
       experiments/results/raw/               raw numbers (git-ignored)

Part of it: python experiments/run_all.py --modes B --scenarios errors
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "experiments/results/raw", ROOT / "experiments/results"
PY = sys.executable
AI = [PY, "-m", "ai.anomaly"]
os.environ.setdefault("SCENARIO_SCALE", "0.5")  # 90 s scenarios: enough for every fault and the AI
SCENARIOS = ("normal", "spike", "kill", "errors", "slow")  # loadtest/scenario.py
# Mode A: gateway limits off. AIMD_DECREASE_FACTOR=1 stops the concurrency limit halving after every
# failed request (without it, one broken server still makes the gateway refuse traffic).
NO_LIMITS = {"RATE_LIMIT_RPS": "1000000", "CONCURRENCY_PER_BACKEND": "1000000", "AIMD_DECREASE_FACTOR": "1"}


def say(text: str) -> None:
    print(f"\n== {text}", flush=True)


def run(*cmd: str, env: dict | None = None, check: bool = True) -> int:
    return subprocess.run(cmd, cwd=ROOT, env={**os.environ, **(env or {})}, check=check).returncode


def wait_for(url: str, seconds: int = 90) -> None:
    for _ in range(seconds):
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise SystemExit(f"gave up waiting for {url}")


def reset_servers() -> None:
    """Every run starts from 3 healthy servers with no injected fault."""
    run("docker", "compose", "up", "-d", "app1", "app2", "app3")
    for name in ("app1", "app2", "app3"):
        run(PY, "chaos/chaos.py", "clear", name, check=False)
    for port in (8001, 8002, 8003):
        wait_for(f"http://localhost:{port}/ready")


def set_mode(mode: str) -> None:
    gateway = ["docker", "compose", "up", "-d", "--force-recreate", "gateway"]
    if mode == "A":
        run("docker", "compose", "stop", "controller")
        run(*gateway, env=NO_LIMITS)
    else:  # gateway back to its own defaults
        defaults = {k: v for k, v in os.environ.items() if k not in NO_LIMITS}
        subprocess.run(gateway, cwd=ROOT, env=defaults, check=True)
        run("docker", "compose", "up", "-d", "controller")
        wait_for("http://localhost:9100/healthz")
    wait_for("http://localhost:8080/health")
    time.sleep(3)  # controller sees every server before traffic starts


def one_run(mode: str, scenario: str, runs: dict) -> None:
    name = f"{mode}-{scenario}"
    say(name)
    reset_servers()
    start = time.time()
    locust = ["-m", "locust", "-f", "loadtest/scenario.py", "--headless", "--only-summary"]
    # Locust exits 1 when any request failed; in these scenarios that is the point, not a crash
    run(PY, *locust, "--host", "http://localhost:8080", "--csv", str(RAW / name),
        env={"SCENARIO": scenario}, check=False)  # fmt: skip
    end = time.time()
    time.sleep(5)  # last scrape + any recovery lands
    minutes = f"{(time.time() - start) / 60:.2f}"
    run(*AI, "collect", "--minutes", minutes, "--out", str(RAW / f"{name}_metrics.csv"))
    runs[name] = {"start": start, "end": end}
    (RAW / "runs.json").write_text(json.dumps(runs, indent=1), encoding="utf-8")
    time.sleep(10)  # with the 5 s above: rate()[15s] windows forget this run


def run_modes(modes: list[str], scenarios: list[str]) -> None:
    runs = json.loads((RAW / "runs.json").read_text(encoding="utf-8")) if (RAW / "runs.json").exists() else {}
    watcher = None
    try:
        for mode in modes:
            set_mode(mode)
            if mode == "C":
                say("train the AI on Mode B's normal run, then let it watch")
                run(*AI, "train", str(RAW / "B-normal_metrics.csv"))
                watcher, c_start = subprocess.Popen([*AI, "watch"], cwd=ROOT), time.time()
            for sc in scenarios:
                one_run(mode, sc, runs)
            if watcher:
                watcher.terminate()
                watcher.wait()
                watcher = None
                say("Week 10: a postmortem for every incident in Mode C")
                run(PY, "-m", "ai.incident", "--since", str(c_start), check=False)
    finally:
        if watcher:
            watcher.terminate()
        say("back to normal (Mode B)")
        set_mode("B")
        reset_servers()


def write_results() -> None:
    say("score the AI on Mode B's runs (it wasn't acting there, so this is pure detection)")
    evals = []
    for sc in ("spike", "kill", "errors", "slow"):
        metrics, out = RAW / f"B-{sc}_metrics.csv", RAW / f"B-{sc}_eval.json"
        if metrics.exists() and (ROOT / "ai/models/detector.pkl").exists():
            run(*AI, "evaluate", str(metrics), "--json", str(out))
            evals.append(f"{sc}={out}")
    say("results")
    run(PY, "experiments/mttr.py", "--csv", str(RAW / "mttr.csv"), check=False)
    run(PY, "experiments/compare.py")
    if evals:
        run(PY, "-m", "ai.report", str(OUT / "report.html"), *evals, f"modes={RAW / 'three-mode.json'}")
    print(f"\nDone. Open {OUT / 'report.html'} in a browser; table: {OUT / 'three-mode.md'}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--modes", default="B,C,A", help="order matters: C needs B's normal run")
    p.add_argument("--scenarios", default=",".join(SCENARIOS))
    p.add_argument("--results-only", action="store_true", help="skip the runs, rebuild the results")
    a = p.parse_args(argv)
    RAW.mkdir(parents=True, exist_ok=True)
    wait_for("http://localhost:9090/-/ready")
    if not a.results_only:
        run_modes(a.modes.split(","), a.scenarios.split(","))
    write_results()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles: same fix as chaos/chaos.py
    sys.exit(main())
