"""Week 8–9 — AI early warning: spot a sick server from its numbers.

Health checks only ask "are you alive?". A server can say "yes" and still fail 30% of
requests. This module watches the numbers instead and shouts when they look abnormal.

Two detectors, both trained only on a NORMAL run (no faults injected):
  z-score           how many standard deviations a number is above normal; z > 3 = weird
  Isolation Forest  many random trees cut the data; weird points get cut off in few cuts

Features, one row per server every 5 s (from Prometheus, the app servers' own metrics):
  err_ratio  share of that server's /work requests that failed with 5xx   (0.0 – 1.0)
  p95_ms     95% of that server's /work requests were faster than this    (milliseconds)

The AI only warns. It never restarts anything: it sends POST /warning to Person 1's
controller, which lowers that server's traffic to 50% for 30 s (docs/controller.md).

Commands (run from the repo root, stack up with `docker compose up -d`):
  python -m ai.anomaly collect  --minutes 4 --out experiments/results/raw/normal_metrics.csv
  python -m ai.anomaly train    experiments/results/raw/normal_metrics.csv
  python -m ai.anomaly evaluate experiments/results/raw/sick_metrics.csv --json out.json
  python -m ai.anomaly watch    # live: warns the controller, logs to data/ai.jsonl
"""

import argparse
import csv
import json
import math
import pickle
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from prometheus_client import Gauge, start_http_server
from sklearn.ensemble import IsolationForest

from experiments.mttr import DATA, find_incidents, read_jsonl

FEATURES = ("err_ratio", "p95_ms")
STEP_SEC = 5  # = Prometheus scrape interval
WINDOW = "15s"  # rate() over 3 scrapes: reacts fast, still smooth enough
MIN_REQ_RATE = 0.5  # req/s; below this a server has too few requests to judge
# A clean run has err_ratio = 0 everywhere, so its std is 0 and ANY error would be
# "infinitely weird". These floors say how much wiggle still counts as normal.
MIN_STD = {"err_ratio": 0.02, "p95_ms": 5.0}
Z_LIMIT = 3.0
GRACE_SEC = 20  # rate()[15s] keeps showing a fault ~15–20 s after it ends
DEFAULT_MODEL = Path("ai/models/detector.pkl")
DEFAULT_PROM = "http://localhost:9090"
DEFAULT_CONTROLLER = "http://localhost:9100"
WARN_EVERY = 30  # s; = the controller's warn_duration_s (docs/controller.md)

WORK = 'path="/work"'
QUERIES = {
    "req_rate": f"sum by (server) (rate(app_requests_total{{{WORK}}}[{WINDOW}]))",
    "err_rate": f'sum by (server) (rate(app_requests_total{{{WORK},status=~"5.."}}[{WINDOW}]))',
    "p95_ms": (
        "1000 * histogram_quantile(0.95, sum by (server, le) "
        f"(rate(app_request_latency_seconds_bucket{{{WORK}}}[{WINDOW}])))"
    ),
}


# ---------------------------------------------------------------- 1. get the numbers


def _query_range(prom: str, query: str, start: float, end: float) -> dict:
    """{(ts, server_id): value} for one PromQL query."""
    params = urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": STEP_SEC})
    with urllib.request.urlopen(f"{prom}/api/v1/query_range?{params}", timeout=10) as r:
        body = json.load(r)
    if body.get("status") != "success":
        raise RuntimeError(f"Prometheus error: {body}")
    out = {}
    for series in body["data"]["result"]:
        sid = series["metric"].get("server")
        for ts, value in series["values"]:
            out[(float(ts), sid)] = float(value)
    return out


def to_rows(req: dict, err: dict, p95: dict) -> list[dict]:
    """Join the three queries into one row per (time, server). Skips rows we can't judge."""
    rows = []
    for (ts, sid), rate in sorted(req.items()):
        p = p95.get((ts, sid), math.nan)
        if rate < MIN_REQ_RATE or math.isnan(p):
            continue  # idle (e.g. removed by the gateway) or no latency data yet
        rows.append(
            {
                "ts": ts,
                "server_id": sid,
                "req_rate": round(rate, 3),
                "err_ratio": round(err.get((ts, sid), 0.0) / rate, 4),
                "p95_ms": round(p, 1),
            }
        )
    return rows


def fetch(prom: str, start: float, end: float) -> list[dict]:
    got = {name: _query_range(prom, q, start, end) for name, q in QUERIES.items()}
    return to_rows(got["req_rate"], got["err_rate"], got["p95_ms"])


def write_rows(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "server_id", "req_rate", "err_ratio", "p95_ms"])
        w.writeheader()
        w.writerows(rows)


def read_rows(path: Path) -> list[dict]:
    with path.open() as f:
        return [
            {**r, **{k: float(r[k]) for k in ("ts", "req_rate", "err_ratio", "p95_ms")}}
            for r in csv.DictReader(f)
        ]


# ---------------------------------------------------------------- 2. the two detectors


def _matrix(rows: list[dict]) -> np.ndarray:
    return np.array([[r[f] for f in FEATURES] for r in rows], dtype=float)


@dataclass
class Detector:
    mean: np.ndarray
    std: np.ndarray
    forest: IsolationForest
    cutoff: float  # forest scores below this = anomaly

    def zscores(self, rows: list[dict]) -> np.ndarray:
        return (_matrix(rows) - self.mean) / self.std

    def zscore_flags(self, rows: list[dict]) -> list[bool]:
        # One-sided: only MORE errors or SLOWER counts. A server that is faster than normal
        # (e.g. a few quick requests as a test winds down) is not sick.
        return list(self.zscores(rows).max(axis=1) > Z_LIMIT)

    def forest_scores(self, rows: list[dict]) -> np.ndarray:
        # The forest sees z-scores, not raw numbers, so both features are on one scale.
        return self.forest.score_samples(self.zscores(rows))

    def forest_flags(self, rows: list[dict]) -> list[bool]:
        worse = self.zscores(rows).max(axis=1) > 0  # same rule: unusual AND worse than normal
        return list((self.forest_scores(rows) < self.cutoff) & worse)


def train(rows: list[dict], seed: int = 0) -> Detector:
    if len(rows) < 20:
        raise ValueError(f"need at least 20 normal rows to learn 'normal', got {len(rows)}")
    x = _matrix(rows)
    floors = np.array([MIN_STD[f] for f in FEATURES])
    mean, std = x.mean(axis=0), np.maximum(x.std(axis=0), floors)
    z = (x - mean) / std

    # Isolation Forest only cuts between the min and max it saw. A feature that never moved
    # in training (err_ratio = 0) gets no cuts, so the forest would be blind to errors.
    # Fix: train on copies jittered by the same "normal wiggle" the z-score allows.
    rng = np.random.default_rng(seed)
    z_train = np.vstack([z, z + rng.normal(0, 1, size=z.shape)])
    forest = IsolationForest(n_estimators=200, random_state=seed).fit(z_train)

    # Cutoff: the lowest score any real normal row got, minus a small margin.
    # = "nothing in the normal run would have fired", which is our false-positive target.
    cutoff = float(forest.score_samples(z).min()) - 0.02
    return Detector(mean, std, forest, cutoff)


def debounce(flags: list[bool], rows: list[dict], k: int) -> list[bool]:
    """Only keep a flag once the same server was flagged k samples in a row."""
    run: dict[str, int] = {}
    out = []
    for f, r in zip(flags, rows, strict=True):
        run[r["server_id"]] = run.get(r["server_id"], 0) + 1 if f else 0
        out.append(run[r["server_id"]] >= k)
    return out


# ---------------------------------------------------------------- 3. score against truth


@dataclass
class Fault:
    server_id: str
    kind: str
    start: float
    end: float
    health_check_sec: float | None = None  # how fast the controller's health check noticed


@dataclass
class Score:
    method: str
    k: int
    normal_rows: int
    false_positive_rows: int
    false_alarms: int
    faults: list[dict] = field(default_factory=list)

    @property
    def fp_rate(self) -> float:
        return self.false_positive_rows / self.normal_rows if self.normal_rows else 0.0


def load_faults(data: Path, first_ts: float, last_ts: float) -> list[Fault]:
    """Ground truth from data/chaos.jsonl: when was each server really broken (in this run)?

    kill/stop/pause/sick/break: until the controller says `recovered` (same rule as MTTR).
    errors/slow: until the next `clear` of that server. The controller doesn't restart
    these (the server still answers /health), which is exactly the AI's job.
    """
    chaos, events = read_jsonl(data / "chaos.jsonl"), read_jsonl(data / "events.jsonl")
    faults = [
        Fault(i.server, i.action, i.broken_at, i.recovered_at or last_ts, i.detection_s)
        for i in find_incidents(chaos, events)
    ]
    for n, c in enumerate(chaos):
        if c.get("action") not in {"errors", "slow"} or not c.get("ok", True):
            continue
        end = next(
            (x["time"] for x in chaos[n + 1 :] if x["target"] == c["target"] and x["action"] == "clear"),
            last_ts,
        )
        down = [
            e["time"] - c["time"]
            for e in events
            if e["type"] == "server_down" and e.get("server") == c["target"] and c["time"] <= e["time"] <= end
        ]
        faults.append(Fault(c["target"], c["action"], c["time"], end, round(min(down), 2) if down else None))
    return [f for f in faults if first_ts <= f.start <= last_ts]


def score(rows: list[dict], flags: list[bool], faults: list[Fault], method: str, k: int) -> Score:
    def fault_at(r: dict) -> Fault | None:
        for f in faults:
            if f.server_id == r["server_id"] and f.start <= r["ts"] <= f.end + GRACE_SEC:
                return f
        return None

    normal = fp_rows = alarms = 0
    prev: dict[str, bool] = {}
    first_hit: dict[int, float] = {}
    for r, flag in zip(rows, flags, strict=True):
        f = fault_at(r)
        if f is None:
            normal += 1
            fp_rows += flag
            alarms += flag and not prev.get(r["server_id"], False)  # count each new alarm once
        elif flag and id(f) not in first_hit:
            first_hit[id(f)] = r["ts"] - f.start
        prev[r["server_id"]] = flag

    result = Score(method, k, normal, int(fp_rows), int(alarms))
    for f in faults:
        hit = first_hit.get(id(f))
        result.faults.append(
            {
                "server_id": f.server_id,
                "kind": f.kind,
                "duration_sec": round(f.end - f.start, 1),
                "ai_detect_sec": None if hit is None else round(hit, 1),
                "health_check_sec": f.health_check_sec,
            }
        )
    return result


def evaluate(rows: list[dict], faults: list[Fault], det: Detector) -> list[Score]:
    raw = {"z-score": det.zscore_flags(rows), "isolation-forest": det.forest_flags(rows)}
    return [
        score(rows, debounce(flags, rows, k), faults, method, k)
        for method, flags in raw.items()
        for k in (1, 2)
    ]


def print_scores(scores: list[Score]) -> None:
    print(f"{'method':<17} {'k':>2} {'FP rate':>8} {'false alarms':>13}   faults caught")
    for s in scores:
        caught = sum(f["ai_detect_sec"] is not None for f in s.faults)
        print(f"{s.method:<17} {s.k:>2} {s.fp_rate:>8.2%} {s.false_alarms:>13}   {caught}/{len(s.faults)}")
    print("\nper fault (k=2):")
    for s in scores:
        if s.k != 2:
            continue
        for f in s.faults:
            ai = "missed" if f["ai_detect_sec"] is None else f"{f['ai_detect_sec']:.0f}s"
            hc = "never" if f["health_check_sec"] is None else f"{f['health_check_sec']:.1f}s"
            print(f"  {s.method:<17} {f['server_id']} {f['kind']:<8} AI {ai:>7}  health check {hc}")


# ---------------------------------------------------------------- 4. live early warning


def should_warn(bad_in_a_row: int, k: int) -> bool:
    """Warn when a server turns sick, then again every WARN_EVERY while it stays sick:
    the controller's lower weight only lasts 30 s (found in the 3-mode experiment)."""
    return bad_in_a_row >= k and (bad_in_a_row - k) % (WARN_EVERY // STEP_SEC) == 0


def warn_controller(controller: str, server: str, score: float, reason: str) -> int | None:
    """POST /warning (docs/controller.md). 200 = accepted, 409 = ignored (server not healthy)."""
    body = json.dumps({"server": server, "score": score, "reason": reason, "source": "ai"})
    req = urllib.request.Request(
        f"{controller}/warning", body.encode(), {"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return None  # controller down: the AI keeps watching; rules don't depend on it (scenario 8)


def log_ai(record: dict, data: Path = DATA) -> None:
    data.mkdir(parents=True, exist_ok=True)
    with (data / "ai.jsonl").open("a") as f:
        f.write(json.dumps({"time": round(time.time(), 3), **record}) + "\n")


def watch(
    prom: str, det: Detector, method: str, k: int, metrics_port: int = 8010, controller: str = ""
) -> None:
    """Every 5 s: score the newest row per server. When one turns sick, warn the controller
    and log it to data/ai.jsonl. Serves /metrics on `metrics_port` for Prometheus + Grafana.
    """
    flag = Gauge("ai_anomaly_flag", "1 = the AI says this server looks sick", ["server"])
    zscore = Gauge("ai_anomaly_zscore", "How far above normal (z > 3 = odd)", ["server", "feature"])
    if metrics_port:
        start_http_server(metrics_port)
    run: dict[str, int] = {}
    print(f"[ai] watching {prom} with {method}, alert after {k} bad samples. Ctrl+C to stop.")
    if metrics_port:
        print(f"[ai] my numbers for Prometheus: http://localhost:{metrics_port}/metrics")
    while True:
        now = time.time()
        try:
            latest = {r["server_id"]: r for r in fetch(prom, now - STEP_SEC, now)}
        except (OSError, RuntimeError) as e:  # Prometheus not up yet / restarting: keep waiting
            print(f"[ai] can't read {prom} yet ({e}); trying again in {STEP_SEC} s")
            time.sleep(STEP_SEC)
            continue
        for sid, r in latest.items():
            bad = (det.zscore_flags if method == "z-score" else det.forest_flags)([r])[0]
            run[sid] = run.get(sid, 0) + 1 if bad else 0
            flag.labels(sid).set(int(run[sid] >= k))
            for feat, z in zip(FEATURES, det.zscores([r])[0], strict=True):
                zscore.labels(sid, feat).set(round(float(z), 2))
            if should_warn(run[sid], k):
                z = {f: round(float(v), 1) for f, v in zip(FEATURES, det.zscores([r])[0], strict=True)}
                reason = f"errors {r['err_ratio']:.0%}, p95 {r['p95_ms']:.0f} ms (z {max(z.values())})"
                status = warn_controller(controller, sid, max(z.values()), reason) if controller else None
                log_ai(
                    {
                        "type": "anomaly_detected",
                        "server": sid,
                        "method": method,
                        "err_ratio": r["err_ratio"],
                        "p95_ms": r["p95_ms"],
                        "z": z,
                        "warning_status": status,
                    }
                )
                sent = {200: "controller lowered its traffic", 409: "controller ignored it"}
                print(f"[ai] {sid} looks sick: {reason}. {sent.get(status, 'no controller')}")
        time.sleep(max(0.0, STEP_SEC - (time.time() - now)))


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ai.anomaly", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="save the last N minutes of metrics to a CSV")
    c.add_argument("--minutes", type=float, required=True)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--prom", default=DEFAULT_PROM)

    t = sub.add_parser("train", help="learn 'normal' from a CSV of a clean run")
    t.add_argument("csv", type=Path)
    t.add_argument("--model", type=Path, default=DEFAULT_MODEL)

    e = sub.add_parser("evaluate", help="score both detectors against data/chaos.jsonl")
    e.add_argument("csv", type=Path)
    e.add_argument("--data", type=Path, default=DATA, help="folder with chaos.jsonl, events.jsonl")
    e.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    e.add_argument("--json", type=Path, help="also save results here (for ai/report.py)")

    w = sub.add_parser("watch", help="live early warning -> events.jsonl")
    w.add_argument("--prom", default=DEFAULT_PROM)
    w.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    w.add_argument("--method", choices=["z-score", "isolation-forest"], default="z-score")
    w.add_argument("--k", type=int, default=2)
    w.add_argument("--metrics-port", type=int, default=8010, help="0 = don't serve /metrics")
    w.add_argument("--controller", default=DEFAULT_CONTROLLER, help='"" = only watch, never warn')

    a = p.parse_args(argv)

    if a.cmd == "collect":
        end = time.time()
        rows = fetch(a.prom, end - a.minutes * 60, end)
        write_rows(rows, a.out)
        print(f"saved {len(rows)} rows ({len({r['server_id'] for r in rows})} servers) to {a.out}")
        return 0

    if a.cmd == "train":
        det = train(read_rows(a.csv))
        a.model.parent.mkdir(parents=True, exist_ok=True)
        a.model.write_bytes(pickle.dumps(det))
        print(f"normal mean {dict(zip(FEATURES, det.mean.round(3), strict=True))}")
        print(f"normal std  {dict(zip(FEATURES, det.std.round(3), strict=True))}")
        print(f"saved model to {a.model}")
        return 0

    if not a.model.exists():
        print(f"no model at {a.model}. Run: python -m ai.anomaly train <normal.csv>")
        return 1
    det = pickle.loads(a.model.read_bytes())  # our own file, written by `train`

    if a.cmd == "evaluate":
        rows = read_rows(a.csv)
        if not rows:
            print(f"{a.csv} has no rows")
            return 1
        faults = load_faults(a.data, rows[0]["ts"], rows[-1]["ts"])
        scores = evaluate(rows, faults, det)
        print_scores(scores)
        if a.json:
            out = {
                "created": datetime.now(UTC).isoformat(timespec="seconds"),
                "rows": len(rows),
                "scores": [{**s.__dict__, "fp_rate": s.fp_rate} for s in scores],
                "timeline": [
                    {**r, "z_flag": z, "forest_flag": f}
                    for r, z, f in zip(
                        rows,
                        debounce(det.zscore_flags(rows), rows, 2),
                        debounce(det.forest_flags(rows), rows, 2),
                        strict=True,
                    )
                ],
                "faults": [f.__dict__ for f in faults],
                "model": {
                    "features": list(FEATURES),
                    "mean": [round(float(v), 4) for v in det.mean],
                    "std": [round(float(v), 4) for v in det.std],
                    "z_limit": Z_LIMIT,
                    "step_sec": STEP_SEC,
                },
            }
            a.json.write_text(json.dumps(out, indent=1, default=float))
            print(f"\nsaved {a.json}")
        return 0

    try:
        watch(a.prom, det, a.method, a.k, a.metrics_port, a.controller)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
