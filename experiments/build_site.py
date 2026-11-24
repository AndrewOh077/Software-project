"""Build the project website's data from real experiment runs (Person 3).

    python experiments/build_site.py      # after experiments/run_all.py; writes site/data.js

site/ is a static website (GitHub Pages publishes it, see .github/workflows/pages.yml):
  site/template.html  the page, hand-written; index.html is made from it here with the real numbers
  site/replay.js    plays a recorded run back in the browser
  site/data.js      written here: every replayed run + the 3-mode results
  site/report.html  copied here from experiments/results/report.html

Replay = what really happened in one run, sample by sample: each server's traffic, errors and
p95 every 5 s (Prometheus), plus every chaos action, controller decision and AI warning. Nothing
on the website talks to a live system, so it can't break during a demo or be misused.
"""

import csv
import json
import shutil
import sys
from datetime import date
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
RAW, DATA, SITE = ROOT / "experiments/results/raw", ROOT / "data", ROOT / "site"
REPO = "https://github.com/AndrewOh077/Software-project"
FAULTS = ("kill", "errors", "slow")
SCENARIOS = {"kill": "app2 crashes", "errors": "app3 fails 30% quietly", "slow": "app3 slows down"}
MODES = {"A": "No protection", "C": "Rules + AI"}
TAIL = 20  # seconds after Locust stops: recovery often lands here


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def words(src: str, e: dict) -> str | None:
    """One event in plain words, or None to leave it out of the replay feed."""
    s = e.get("server") or e.get("target", "")
    if src == "chaos":
        return {
            "kill": f"{s} crashes (chaos tool)",
            "errors": f"{s} starts failing 30% of requests but still answers /health",
            "slow": f"{s} gets 800 ms slower",
            "start": f"a person starts {s} again",
            "clear": f"{s}'s fault is removed",
        }.get(e.get("action"))
    if src == "ai":
        return f"{s} looks sick (errors {e['err_ratio']:.0%}, p95 {e['p95_ms']:.0f} ms)"
    return {
        "server_down": f"takes {s} out of traffic ({e.get('reason', '')})",
        "restart": f"restarts {s}",
        "ramp": f"sends {s} {e.get('weight')}% of its traffic",
        "recovered": f"{s} fully back after {e.get('recovery_s', 0):.1f} s",
        "warning": f"lowers {s} to 50% for 30 s after the AI warning",
        "emergency_on": "emergency mode on",
        "emergency_off": "emergency mode off",
    }.get(e.get("type"))


def replay(name: str, start: float, end: float) -> dict:
    """One run: samples every 5 s + plain-word events, times in seconds from the start."""
    rows = list(csv.DictReader((RAW / f"{name}_metrics.csv").open(encoding="utf-8")))
    samples: dict[float, dict] = {}
    for r in rows:
        t = round(float(r["ts"]) - start, 1)
        if -5 <= t <= end - start + TAIL:
            samples.setdefault(t, {})[r["server_id"]] = [
                round(float(r["req_rate"]), 2),
                round(float(r["err_ratio"]), 3),
                round(float(r["p95_ms"])),
            ]
    feed = []
    for src, path in (("chaos", "chaos.jsonl"), ("controller", "events.jsonl"), ("ai", "ai.jsonl")):
        for e in read_jsonl(DATA / path):
            if not start <= e.get("time", 0) <= end + TAIL:
                continue
            if src == "ai" and e.get("type") != "anomaly_detected":
                continue
            text = words(src, e)
            if text:
                feed.append(
                    {
                        "t": round(e["time"] - start, 1),
                        "src": src,
                        "text": text,
                        "server": e.get("server") or e.get("target"),
                        "state": e.get("new") if e.get("type") == "state_change" else None,
                    }
                )
            if e.get("type") == "state_change":  # not in the feed, but drives the server tiles
                feed.append(
                    {
                        "t": round(e["time"] - start, 1),
                        "src": "state",
                        "server": e["server"],
                        "state": e["new"],
                    }
                )
    # what users saw: Locust's own failure rate (a dead server can't count the requests it never got)
    users = []
    hist = RAW / f"{name}_stats_history.csv"
    if hist.exists():
        for r in csv.DictReader(hist.open(encoding="utf-8")):
            t, rps = round(float(r["Timestamp"]) - start), float(r["Requests/s"] or 0)
            if r["Name"] == "Aggregated" and 0 <= t <= end - start + TAIL and t % 2 == 0 and rps:
                users.append([t, round(float(r["Failures/s"] or 0) / rps, 3)])
    order = {"chaos": 0, "ai": 1, "controller": 2, "state": 3}  # cause before effect at the same second
    feed.sort(key=lambda x: (x["t"], order[x["src"]]))
    return {
        "length": round(end - start + TAIL),
        "samples": sorted(([t, v] for t, v in samples.items()), key=lambda x: x[0]),
        "events": feed,
        "users": users,
    }


def weighted(results: dict, mode: str, key: str) -> float | None:
    """A metric over the fault runs, weighted by how many requests each run had."""
    runs = [
        results[sc][mode]
        for sc in FAULTS
        if mode in results.get(sc, {}) and results[sc][mode].get(key) is not None
    ]
    n = sum(r["requests"] for r in runs)
    return sum(r[key] * r["requests"] for r in runs) / n if n else None


def ai_noticed() -> float | None:
    """Seconds from a quiet 30% error fault to the AI's alert (z-score, 2 in a row), scored on Mode B's
    run where the AI only watched: the same number docs/experiments.md reports."""
    path = RAW / "B-errors_eval.json"
    if not path.exists():
        return None
    for s in json.loads(path.read_text(encoding="utf-8"))["scores"]:
        if (s["method"], s["k"]) == ("z-score", 2) and s["faults"]:
            return s["faults"][0]["ai_detect_sec"]
    return None


def rows(results: dict) -> str:
    pct = lambda x: "n/a" if x is None else f"{x:.1%}"  # noqa: E731
    secs = lambda x: "never" if x is None else f"{x:.0f} s"  # noqa: E731
    kill = results.get("kill", {})
    table = [
        ("Requests that failed", {m: weighted(results, m, "errors") for m in "ABC"}, pct, min),
        ("Critical requests that worked", {m: weighted(results, m, "critical_ok") for m in "ABC"}, pct, max),
        ("Back to full service after a crash", {m: kill.get(m, {}).get("mttr_s") for m in "ABC"}, secs, min),
        (
            "A server failing quietly is noticed after",
            {"A": None, "B": None, "C": ai_noticed()},
            secs,
            min,
        ),
    ]
    out = []
    for label, vals, fmt, best in table:
        known = [v for v in vals.values() if v is not None]
        top = best(known) if known else None
        cells = "".join(
            ('<td class="best">' if v is not None and v == top else "<td>") + f"{fmt(v)}</td>"
            for v in vals.values()
        )
        out.append(f'<tr><th scope="row">{label}</th>{cells}</tr>')
    return "\n".join(out)


def page(out: dict) -> str:
    res = out["results"]
    radios = lambda name, items, first: "".join(  # noqa: E731
        f'<label><input type="radio" name="{name}" value="{k}"{" checked" if k == first else ""}>'
        f"<span>{v}</span></label>"
        for k, v in items.items()
    )
    tiles = "".join(
        f'<article class="server" id="s-{n}" aria-label="{n}"><h3>{n}</h3>'
        f'<span class="state" data-s="HEALTHY">healthy</span><dl>'
        f'<div><dt>Share of traffic</dt><dd class="rps">-</dd></div>'
        f'<div class="share" aria-hidden="true"><i></i></div>'
        f'<div><dt>Errors</dt><dd class="err">-</dd></div>'
        f'<div><dt>p95 response</dt><dd class="p95">-</dd></div></dl></article>'
        for n in ("app1", "app2", "app3")
    )
    a, c = weighted(res, "A", "errors"), weighted(res, "C", "errors")
    return Template((SITE / "template.html").read_text(encoding="utf-8")).substitute(
        repo=REPO,
        err_a="n/a" if a is None else f"{a:.1%}",
        err_c="n/a" if c is None else f"{c:.1%}",
        scenario_inputs=radios("scenario", SCENARIOS, "kill"),
        mode_inputs=radios("mode", MODES, "A"),
        server_tiles=tiles,
        result_rows=rows(res),
        built=date.today().isoformat(),
    )


def main() -> int:
    runs = json.loads((RAW / "runs.json").read_text(encoding="utf-8"))
    out = {"scenarios": SCENARIOS, "modes": MODES, "runs": {}}
    for sc in SCENARIOS:
        for mode in MODES:
            name = f"{mode}-{sc}"
            if name in runs and (RAW / f"{name}_metrics.csv").exists():
                out["runs"][name] = replay(name, runs[name]["start"], runs[name]["end"])
    modes_file = RAW / "three-mode.json"
    out["results"] = json.loads(modes_file.read_text(encoding="utf-8")) if modes_file.exists() else {}
    if not out["runs"]:
        print("no runs to replay. Run: python experiments/run_all.py")
        return 1
    SITE.mkdir(exist_ok=True)
    js = "// written by experiments/build_site.py from real runs; don't edit by hand\n"
    (SITE / "data.js").write_text(js + "window.RESILIGUARD = " + json.dumps(out) + ";\n", encoding="utf-8")
    (SITE / "index.html").write_text(page(out), encoding="utf-8")
    report = ROOT / "experiments/results/report.html"
    if report.exists():
        shutil.copy(report, SITE / "report.html")
    print(f"site/data.js: {len(out['runs'])} runs, {(SITE / 'data.js').stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
