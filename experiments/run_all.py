"""Person 3's whole experiment, in order, with one command (Windows, Mac and Linux):

    docker compose up -d --build
    pip install -r requirements-p3.txt
    python experiments/run_all.py                       # ~60 min, full length
    $env:SCENARIO_SCALE="0.3"; python experiments/run_all.py   # ~20 min rehearsal (PowerShell)

What it does
  1. Mode B (rules): normal, spike, kill, errors, slow      -> the Week 5 baseline
  2. Train the AI on Mode B's normal run                    -> ai/models/detector.pkl
  3. Mode C (rules + AI): the same 5, with the AI warning the controller
  4. Mode A (no protection): the same 5, controller stopped, gateway limits off
  5. Put everything back to normal (Mode B), then write the results:
       experiments/results/three-mode.md      the comparison table
       experiments/results/report.html        charts + AI results (open in a browser)
       experiments/results/postmortems/       one report per incident in Mode C (Week 10)
       experiments/results/raw/               raw numbers (git-ignored)

Run part of it: python experiments/run_all.py --modes B --scenarios errors
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
RAW = ROOT / "experiments" / "results" / "raw"
OUT = ROOT / "experiments" / "results"
PY = sys.executable
SCENARIOS = {  # name -> (locust file, extra environment)
    "normal": ("scenario_normal.py", {}),
    "spike": ("scenario_spike.py", {}),
    "kill": ("scenario_fault.py", {"FAULT": "kill", "TARGET": "app2"}),
    "errors": ("scenario_fault.py", {"FAULT": "errors", "TARGET": "app3"}),
    "slow": ("scenario_fault.py", {"FAULT": "slow", "TARGET": "app3"}),
}
# Mode A: gateway limits off. AIMD_DECREASE_FACTOR=1 stops the concurrency limit from halving
# after every failed request (without it, one broken server still makes the gateway refuse traffic).
NO_LIMITS = {"RATE_LIMIT_RPS": "1000000", "CONCURRENCY_PER_BACKEND": "1000000", "AIMD_DECREASE_FACTOR": "1"}


def say(text: str) -> None:
    print(f"\n== {text}", flush=True)


def run(*cmd: str, env: dict | None = None, check: bool = True) -> int:
    return subprocess.run(cmd, cwd=ROOT, env={**os.environ, **(env or {})}, check=check).returncode


def compose(*args: str, env: dict | None = None) -> None:
    run("docker", "compose", *args, env=env)


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
    compose("up", "-d", "app1", "app2", "app3")
    for name in ("app1", "app2", "app3"):
        run(PY, "chaos/chaos.py", "clear", name, check=False)
    for port in (8001, 8002, 8003):
        wait_for(f"http://localhost:{port}/ready")


def set_mode(mode: str) -> None:
    if mode == "A":
        compose("stop", "controller")
        compose("up", "-d", "--force-recreate", "gateway", env=NO_LIMITS)
    else:
        env = {k: v for k, v in os.environ.items() if k not in NO_LIMITS}  # gateway defaults
        subprocess.run(
            ["docker", "compose", "up", "-d", "--force-recreate", "gateway"], cwd=ROOT, env=env, check=True
        )
        compose("up", "-d", "controller")
        wait_for("http://localhost:9100/healthz")
    wait_for("http://localhost:8080/health")
    time.sleep(5)  # controller sees every server before traffic starts


def one_run(mode: str, scenario: str, runs: dict) -> None:
    name = f"{mode}-{scenario}"
    file, env = SCENARIOS[scenario]
    say(f"{name}  ({file} {env or ''})")
    reset_servers()
    start = time.time()
    # Locust exits 1 when any request failed; in these scenarios that is the point, not a crash
    run(
        PY,
        "-m",
        "locust",
        "-f",
        f"loadtest/{file}",
        "--headless",
        "--only-summary",
        "--host",
        "http://localhost:8080",
        "--csv",
        str(RAW / name),
        env=env,
        check=False,
    )
    end = time.time()
    time.sleep(10)  # last scrape + any recovery lands
    run(
        PY,
        "-m",
        "ai.anomaly",
        "collect",
        "--minutes",
        f"{(time.time() - start) / 60:.2f}",
        "--out",
        str(RAW / f"{name}_metrics.csv"),
    )
    runs[name] = {"start": start, "end": end}
    (RAW / "runs.json").write_text(json.dumps(runs, indent=1))
    time.sleep(20)  # rate() windows forget this run before the next one


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--modes", default="B,C,A", help="order matters: C needs B's normal run")
    p.add_argument("--scenarios", default=",".join(SCENARIOS))
    a = p.parse_args(argv)
    modes, scenarios = a.modes.split(","), a.scenarios.split(",")

    RAW.mkdir(parents=True, exist_ok=True)
    runs = json.loads((RAW / "runs.json").read_text()) if (RAW / "runs.json").exists() else {}
    wait_for("http://localhost:9090/-/ready")
    watcher = None
    try:
        for mode in modes:
            set_mode(mode)
            if mode == "C":
                say("train the AI on Mode B's normal run, then let it watch")
                run(PY, "-m", "ai.anomaly", "train", str(RAW / "B-normal_metrics.csv"))
                watcher = subprocess.Popen([PY, "-m", "ai.anomaly", "watch"], cwd=ROOT)
                c_start = time.time()
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

    say("score the AI on Mode B's runs (it wasn't acting there, so this is pure detection)")
    evals = []
    for sc in ("spike", "kill", "errors", "slow"):
        if (RAW / f"B-{sc}_metrics.csv").exists() and (ROOT / "ai/models/detector.pkl").exists():
            run(
                PY,
                "-m",
                "ai.anomaly",
                "evaluate",
                str(RAW / f"B-{sc}_metrics.csv"),
                "--json",
                str(RAW / f"B-{sc}_eval.json"),
            )
            evals.append(f"{sc}={RAW / f'B-{sc}_eval.json'}")

    say("results")
    run(PY, "experiments/mttr.py", "--csv", str(RAW / "mttr.csv"), check=False)
    run(PY, "experiments/compare.py")
    if evals:
        run(PY, "-m", "ai.report", str(OUT / "report.html"), *evals, f"modes={RAW / 'three-mode.json'}")
    print(f"\nDone. Open {OUT / 'report.html'} in a browser; table: {OUT / 'three-mode.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
