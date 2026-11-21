"""Week 5 — Mode A (no protection) vs Mode B (rules): one table, same scenarios.

    python experiments/compare_modes.py experiments/results/raw \
        --out experiments/results/mode-baseline.md --json experiments/results/raw/modes.json

Reads what `make results` (experiments/run_all.sh) leaves in the folder:
  modeA-normal_stats.csv, modeA-normal_stats_history.csv, ...   (Locust --csv output)
  events.jsonl                                                  (gateway + chaos events)

For each mode x scenario:
  requests      how many requests users sent
  failed        share that failed (5xx, 429, timeouts; anything Locust counts as a failure)
  p99           99% of requests were faster than this
  critical ok   share of 'critical' requests (10% of traffic) that succeeded
  MTTR          server killed -> back in the gateway's rotation (Week 4). Mode A has no
                health checks, so nothing ever takes the server out or puts it back: "n/a".
"""

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # so `mttr` imports when run as a file
from mttr import find_incidents, load_events  # noqa: E402

MODES = {"A": "Mode A, no protection", "B": "Mode B, rules"}
SCENARIOS = ("normal", "spike", "failure")
TAIL_SEC = 30  # recovery can land a little after Locust stops


def read_stats(prefix: Path) -> dict | None:
    stats = Path(f"{prefix}_stats.csv")
    history = Path(f"{prefix}_stats_history.csv")
    if not stats.exists():
        return None
    rows = {r["Name"]: r for r in csv.DictReader(stats.open())}
    total, crit = rows.get("Aggregated"), rows.get("/work [critical]")
    if total is None or int(total["Request Count"]) == 0:
        return None
    times = (
        [float(r["Timestamp"]) for r in csv.DictReader(history.open())] if history.exists() else []
    )

    def ok_share(r: dict | None) -> float | None:
        if r is None or int(r["Request Count"]) == 0:
            return None
        return 1 - int(r["Failure Count"]) / int(r["Request Count"])

    return {
        "requests": int(total["Request Count"]),
        "failed": int(total["Failure Count"]) / int(total["Request Count"]),
        "p99_ms": float(total["99%"]),
        "critical_ok": ok_share(crit),
        "start": min(times) if times else None,
        "end": max(times) if times else None,
    }


def mttr_in_window(events_path: Path, start: float | None, end: float | None) -> float | None:
    """Mean MTTR of incidents that started while this run was going (None = none recovered)."""
    if start is None or not events_path.exists():
        return None
    events, _ = load_events(events_path)
    times = [
        i.mttr_sec
        for i in find_incidents(events)
        if start <= i.killed_at.timestamp() <= end + TAIL_SEC and i.mttr_sec is not None
    ]
    return round(sum(times) / len(times), 2) if times else None


def collect(folder: Path, events: Path) -> dict:
    out: dict = {}
    for mode in MODES:
        for sc in SCENARIOS:
            s = read_stats(folder / f"mode{mode}-{sc}")
            if s is None:
                continue
            s["mttr_sec"] = (
                mttr_in_window(events, s["start"], s["end"]) if sc == "failure" else None
            )
            out.setdefault(sc, {})[mode] = s
    return out


def pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.1%}"


def markdown(results: dict) -> str:
    starts = [s["start"] for m in results.values() for s in m.values() if s["start"]]
    day = datetime.fromtimestamp(min(starts), UTC).date() if starts else "unknown date"
    lines = [
        "# Week 5 baseline: Mode A (no protection) vs Mode B (rules)",
        "",
        f"Fake stack, {day} (UTC). Same Locust scenarios; only the gateway's health checks change.",
        "",
        "| Scenario | Mode | Requests | Failed | p99 | Critical ok | MTTR |",
        "|---|---|---|---|---|---|---|",
    ]
    for sc, modes in results.items():
        for mode, s in modes.items():
            mttr = "–"
            if sc == "failure":
                mttr = (
                    "n/a (never removed)"
                    if mode == "A"
                    else ("not recovered" if s["mttr_sec"] is None else f"{s['mttr_sec']:.1f} s")
                )
            lines.append(
                f"| {sc} | {MODES[mode]} | {s['requests']} | {pct(s['failed'])} | "
                f"{s['p99_ms']:.0f} ms | {pct(s['critical_ok'])} | {mttr} |"
            )
    lines += ["", "## What changed (B compared with A)", ""]
    for sc, modes in results.items():
        if {"A", "B"} <= modes.keys():
            a, b = modes["A"], modes["B"]
            lines.append(
                f"- **{sc}**: failed {pct(a['failed'])} → {pct(b['failed'])}, "
                f"critical ok {pct(a['critical_ok'])} → {pct(b['critical_ok'])}."
            )
    missing = [f"mode{m}-{sc}" for sc in SCENARIOS for m in MODES if m not in results.get(sc, {})]
    if missing:
        lines += ["", f"Missing runs: {', '.join(missing)}."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("folder", type=Path)
    p.add_argument("--events", type=Path, help="default: <folder>/events.jsonl")
    p.add_argument("--out", type=Path, help="save the markdown table here")
    p.add_argument("--json", type=Path, help="save the numbers here")
    a = p.parse_args(argv)

    results = collect(a.folder, a.events or a.folder / "events.jsonl")
    if not results:
        print(f"no modeA-*/modeB-* Locust CSVs in {a.folder}. Run: make results")
        return 1
    text = markdown(results)
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text)
        print(f"saved {a.out}")
    if a.json:
        a.json.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
