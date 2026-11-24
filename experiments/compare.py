"""Week 5 + 11–12 — compare protection modes on the same scenarios (Person 3).

    A  no protection   controller stopped, gateway limits off
    B  rules           controller on, gateway rate/concurrency limits on   (Week 5 baseline)
    C  rules + AI      B, plus the AI warning the controller                (Week 8–9)

Reads what experiments/run_all.py leaves in experiments/results/raw/:
  <mode>-<scenario>_stats.csv / _stats_history.csv / _failures.csv   (Locust --csv)
  runs.json                                                         (start/end of every run)
and data/chaos.jsonl + data/events.jsonl for MTTR.

    python experiments/compare.py        # prints the table, writes experiments/results/three-mode.md
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mttr import DATA, find_incidents, read_jsonl  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "experiments" / "results" / "raw"
MODES = {"A": "no protection", "B": "rules", "C": "rules + AI"}
SCENARIOS = {
    "normal": "Normal traffic",
    "spike": "Traffic spike (scenario 1)",
    "kill": "app2 killed (scenario 2)",
    "errors": "app3 fails 30%, still 'healthy'",
    "slow": "app3 +800 ms (scenario 5)",
}


def read_run(prefix: Path) -> dict | None:
    stats = Path(f"{prefix}_stats.csv")
    if not stats.exists():
        return None
    rows = {r["Name"]: r for r in csv.DictReader(stats.open())}
    total, crit = rows.get("Aggregated"), rows.get("/work [critical]")
    if total is None or int(total["Request Count"]) == 0:
        return None
    rejected = errors = 0
    failures = Path(f"{prefix}_failures.csv")
    if failures.exists():
        for r in csv.DictReader(failures.open()):
            n = int(r["Occurrences"])
            if "rejected:" in r["Error"]:
                rejected += n
            else:
                errors += n
    n = int(total["Request Count"])
    crit_n = int(crit["Request Count"]) if crit else 0
    return {
        "requests": n,
        "errors": errors / n,  # real failures users saw (5xx, no backend, timeouts)
        "rejected": rejected / n,  # refused on purpose by the gateway (429 / emergency 503)
        "p99_ms": float(total["99%"]),
        "critical_ok": 1 - int(crit["Failure Count"]) / crit_n if crit_n else None,
    }


def collect(raw: Path = RAW, data: Path = DATA) -> dict:
    runs = json.loads((raw / "runs.json").read_text()) if (raw / "runs.json").exists() else {}
    incidents = find_incidents(read_jsonl(data / "chaos.jsonl"), read_jsonl(data / "events.jsonl"))
    out: dict = {}
    for sc in SCENARIOS:
        for mode in MODES:
            r = read_run(raw / f"{mode}-{sc}")
            if r is None:
                continue
            t = runs.get(f"{mode}-{sc}", {})
            mine = [i for i in incidents if t and t["start"] <= i.broken_at <= t["end"]]
            done = [i.mttr_s for i in mine if i.mttr_s is not None]
            r["mttr_s"] = round(sum(done) / len(done), 1) if done else None
            r["had_incident"] = bool(mine)
            out.setdefault(sc, {})[mode] = r
    return out


def pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.1%}"


def markdown(results: dict, note: str = "") -> str:
    lines = [
        "# Final experiment: 3 protection modes, same scenarios",
        "",
        "A = no protection (controller off, gateway limits off) · B = rules (controller + gateway "
        "limits) · C = rules + AI early warning.",
        *([note] if note else []),
        "",
        "| Scenario | Mode | Requests | Errors | Rejected on purpose | p99 | Critical OK | MTTR |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for sc, modes in results.items():
        for mode, r in modes.items():
            if not r["had_incident"]:
                mttr = "–"
            else:
                never = "never (no controller)" if mode == "A" else "not recovered"
                mttr = never if r["mttr_s"] is None else f"{r['mttr_s']:.1f} s"
            lines.append(
                f"| {SCENARIOS[sc]} | {mode}, {MODES[mode]} | {r['requests']} | {pct(r['errors'])} | "
                f"{pct(r['rejected'])} | {r['p99_ms']:.0f} ms | {pct(r['critical_ok'])} | {mttr} |"
            )
    missing = [f"{m}-{sc}" for sc in SCENARIOS for m in MODES if m not in results.get(sc, {})]
    if missing:
        lines += ["", f"Missing runs: {', '.join(missing)}."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--raw", type=Path, default=RAW)
    p.add_argument("--data", type=Path, default=DATA)
    p.add_argument("--out", type=Path, default=ROOT / "experiments" / "results" / "three-mode.md")
    p.add_argument("--note", default="", help="one line added under the title (e.g. the machine)")
    a = p.parse_args(argv)
    results = collect(a.raw, a.data)
    if not results:
        print(f"no runs in {a.raw}. Run: python experiments/run_all.py")
        return 1
    text = markdown(results, a.note)
    print(text)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text)
    (a.raw / "three-mode.json").write_text(json.dumps(results, indent=1))
    print(f"saved {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
