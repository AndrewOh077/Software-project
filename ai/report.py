"""Turn `python -m ai.anomaly evaluate ... --json` results into one HTML page you can open.

    python -m ai.report experiments/results/ai-report.html \
        spike=/tmp/spike-eval.json failure=/tmp/failure-eval.json sick=/tmp/sick-eval.json

Each argument after the output file is NAME=path. The chart shows the first run that has an
"errors" or "latency" fault (the kind health checks can't see). Styles: ai/report.css.
"""

import html
import json
import sys
from pathlib import Path

CSS = Path(__file__).with_name("report.css")
RUN_NAMES = {"spike": "Traffic spike, no fault", "failure": "app-2 killed", "sick": "app-3 sick"}
W, H, PAD_L, PAD_B, PAD_T = 960, 300, 48, 28, 16


def esc(x) -> str:
    return html.escape(str(x))


def sec(x) -> str:
    return "never" if x is None else f"{x:.0f} s" if x >= 1 else f"{x:.1f} s"


def chart(run: dict) -> tuple[str, dict]:
    """Inline SVG: every server's error ratio over time; the sick one in the accent colour."""
    fault = next(f for f in run["faults"] if f["kind"] != "killed")
    rows = run["timeline"]
    t0, t1 = rows[0]["ts"], rows[-1]["ts"]
    y_max = max(0.4, max(r["err_ratio"] for r in rows))

    def x(ts: float) -> float:
        return PAD_L + (ts - t0) / (t1 - t0) * (W - PAD_L - 8)

    def y(v: float) -> float:
        return H - PAD_B - v / y_max * (H - PAD_B - PAD_T)

    parts = [
        f'<rect class="window" x="{x(fault["start"]):.1f}" y="{PAD_T}" '
        f'width="{x(fault["end"]) - x(fault["start"]):.1f}" height="{H - PAD_B - PAD_T}"/>'
    ]
    for v in (0, 0.1, 0.2, 0.3, 0.4):
        yv = f"{y(v):.1f}"
        parts.append(f'<line class="grid" x1="{PAD_L}" x2="{W - 8}" y1="{yv}" y2="{yv}"/>')
        parts.append(f'<text x="{PAD_L - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.0%}</text>')
    for t in range(0, int(t1 - t0) + 1, 30):
        parts.append(f'<text x="{x(t0 + t):.1f}" y="{H - 8}" text-anchor="middle">{t} s</text>')

    servers = sorted({r["server_id"] for r in rows}, key=lambda s: s == fault["server_id"])
    for sid in servers:  # sick server drawn last, on top
        pts = " ".join(
            f"{x(r['ts']):.1f},{y(r['err_ratio']):.1f}" for r in rows if r["server_id"] == sid
        )
        cls = "sick" if sid == fault["server_id"] else "other"
        parts.append(f'<polyline class="{cls}" points="{pts}"/>')

    alert = next(
        (
            r
            for r in rows
            if r["server_id"] == fault["server_id"] and r["z_flag"] and r["ts"] >= fault["start"]
        ),
        None,
    )
    if alert:
        ax = x(alert["ts"])
        parts += [
            f'<line class="alert" x1="{ax:.1f}" x2="{ax:.1f}" y1="{PAD_T}" y2="{H - PAD_B}"/>',
            f'<circle class="alert-dot" cx="{ax:.1f}" cy="{y(alert["err_ratio"]):.1f}" r="4"/>',
            f'<text class="label" x="{ax + 8:.1f}" y="{PAD_T + 14}">AI alert, '
            f"{alert['ts'] - fault['start']:.0f} s in</text>",
        ]
    parts.append(
        f'<text class="label" x="{x(fault["end"]) - 8:.1f}" y="{H - PAD_B - 10}" '
        'text-anchor="end">'
        "health check: still ready</text>"
    )
    svg = (
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" '
        f'aria-labelledby="chart-title"><title id="chart-title">Error ratio per server. '
        f"{esc(fault['server_id'])} rises to about {max_err(rows, fault):.0%} during the fault; "
        f"the others stay at 0.</title>{''.join(parts)}</svg>"
    )
    return svg, fault


def max_err(rows: list[dict], fault: dict) -> float:
    return max(r["err_ratio"] for r in rows if r["server_id"] == fault["server_id"])


def board(runs: dict[str, dict]) -> str:
    head = (
        "<tr><th>Run</th><th>Method</th><th class='num'>Need in a row</th>"
        "<th class='num'>False alarms</th><th class='num'>FP rate</th>"
        "<th class='num'>Faults caught</th><th class='num'>AI noticed</th>"
        "<th class='num'>Health check noticed</th></tr>"
    )
    body = []
    for name, run in runs.items():
        for s in run["scores"]:
            f = s["faults"][0] if s["faults"] else None
            caught = sum(x["ai_detect_sec"] is not None for x in s["faults"])
            pick = ' class="pick"' if (s["method"], s["k"]) == ("z-score", 2) else ""
            no_fault = '<td class="num none">no fault</td>'
            body.append(
                f"<tr{pick}><td>{esc(RUN_NAMES.get(name, name))}</td><td>{esc(s['method'])}</td>"
                f"<td class='num'>{s['k']}</td>"
                f"<td class='num fig'>{s['false_alarms']}</td>"
                f"<td class='num'>{s['fp_rate']:.1%}</td>"
                + (
                    f"<td class='num'>{caught}/{len(s['faults'])}</td>"
                    f"<td class='num fig'>{sec(f['ai_detect_sec'])}</td>"
                    f"<td class='num'>{sec(f['health_check_sec'])}</td>"
                    if f
                    else no_fault * 3
                )
                + "</tr>"
            )
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


def render(runs: dict[str, dict]) -> str:
    sick = next((r for r in runs.values() if any(f["kind"] != "killed" for f in r["faults"])), None)
    if sick:
        svg, fault = chart(sick)
        pick = next(s for s in sick["scores"] if (s["method"], s["k"]) == ("z-score", 2))
        hit = next(f for f in pick["faults"] if f["server_id"] == fault["server_id"])
        h1 = f"{esc(fault['server_id'])} broke quietly. "
        h1 += f"The AI saw it in {sec(hit['ai_detect_sec'])}."
        lead = (
            f"For {fault['end'] - fault['start']:.0f} s it failed up to "
            f"{max_err(sick['timeline'], fault):.0%} of requests while still answering "
            "“ready”. Health checks can’t see that. Numbers can."
        )
        plates = (
            '<dl class="plates">'
            f'<div class="plate ink"><dt>AI noticed</dt><dd>{sec(hit["ai_detect_sec"])}</dd></div>'
            f'<div class="plate"><dt>Health check noticed</dt>'
            f"<dd>{sec(hit['health_check_sec'])}</dd></div></dl>"
        )
        figure = (
            f"<figure>{svg}<div class='key'><span><span class='swatch sick'></span>"
            f"{esc(fault['server_id'])} error ratio</span><span><span class='swatch other'></span>"
            "other servers</span><span><span class='swatch window'></span>fault window</span></div>"
            "<figcaption>Error ratio per server, from Prometheus every 5 s. "
            "Detector: z-score, alert after 2 bad samples in a row.</figcaption></figure>"
        )
    else:
        h1, lead, plates = "No hidden fault in these runs.", "Add a sick-server run.", ""
        figure = (
            '<p class="empty">No run here has an errors or latency fault, so there is nothing '
            "to chart. Run <code>make sick</code>, then evaluate it.</p>"
        )

    created = min(r["created"] for r in runs.values())[:10]
    kills = [
        f["health_check_sec"]
        for r in runs.values()
        for f in r["faults"]
        if f["kind"] == "killed" and f["health_check_sec"] is not None
    ]
    kill_hc = sec(min(kills)) if kills else "a few seconds"
    z_alarms = {
        s["k"]: sum(
            x["false_alarms"]
            for r in runs.values()
            for x in r["scores"]
            if x["method"] == "z-score" and x["k"] == s["k"]
        )
        for s in next(iter(runs.values()))["scores"]
    }
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ResiliGuard AI early warning, results</title>
<meta name="description" content="Can the AI spot a sick server that health checks miss?">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Chivo+Mono:wght@400;500;800&display=swap">
<style>
{CSS.read_text()}
</style>
</head>
<body>
<header class="bar"><div class="container">
<span class="wordmark">ResiliGuard</span><p>AI early warning, runs from {esc(created)}</p>
</div></header>
<main><div class="container">
<section class="head" aria-labelledby="h1">
<div><h1 id="h1">{h1}</h1><p class="lead">{lead}</p></div>
{plates}
</section>
{figure}
<section class="board" aria-labelledby="board-h">
<h2 id="board-h">Every run, every detector</h2>
<p>Marked row: the default for live use. False alarm = an alert while nothing was broken.</p>
<div class="scroll">{board(runs)}</div>
</section>
<section class="findings" aria-label="What we learned">
<div><h3>A crash is the health check’s job</h3><p>A killed server is caught in {kill_hc}
and gets no traffic after that, so the AI has nothing to judge. That is fine.</p></div>
<div><h3>Two in a row stops false alarms</h3><p>Z-score false alarms across all runs:
{z_alarms.get(1, 0)} when one odd sample is enough, {z_alarms.get(2, 0)} when it needs two in a row.
The cost is one more sample: 5 s slower.</p></div>
<div><h3>The forest adds nothing yet</h3><p>With only two numbers per server, Isolation
Forest finds the same faults as a z-score. It should help once we add more numbers.</p></div>
</section>
</div></main>
<footer><div class="container">Made by <code>python -m ai.report</code> from
<code>python -m ai.anomaly evaluate</code> results.
Fake servers, real Prometheus data.</div></footer>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or any("=" not in a for a in argv[1:]):
        print(__doc__)
        return 1
    out = Path(argv[0])
    runs = {}
    for arg in argv[1:]:
        name, path = arg.split("=", 1)
        runs[name] = json.loads(Path(path).read_text())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(runs))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
