"""Turn `python -m ai.anomaly evaluate ... --json` results into one HTML page you can open.

    python -m ai.report experiments/results/ai-report.html \
        spike=/tmp/spike-eval.json failure=/tmp/failure-eval.json sick=/tmp/sick-eval.json

Each argument after the output file is NAME=path, one per run. The page shows:
  - the headline: the first run with a fault health checks can't see ("errors"/"latency")
  - how the detector decides, with the numbers it learned
  - one tab per run: error ratio + latency charts, alerts marked, a readout under the pointer
  - every run x detector in one table
Styles: ai/report.css. With JavaScript off, every run is shown one under another.
"""

import html
import json
import sys
from pathlib import Path

CSS = Path(__file__).with_name("report.css")
RUN_NAMES = {"spike": "Traffic spike", "failure": "app-2 killed", "sick": "app-3 sick"}
W, H, PAD_L, PAD_B, PAD_T, PAD_R = 960, 220, 64, 28, 16, 8
DETECTORS = {"z_flag": "z-score", "forest_flag": "Isolation Forest"}
FAULT_WORDS = {
    "killed": "was killed",
    "errors": "failed requests on purpose",
    "latency": "was slowed down",
}


def esc(x) -> str:
    return html.escape(str(x))


def sec(x) -> str:
    return "never" if x is None else f"{x:.0f} s" if x >= 1 else f"{x:.1f} s"


def hidden_fault(run: dict) -> dict | None:
    """The fault health checks can't see (errors / latency), if this run has one."""
    return next((f for f in run["faults"] if f["kind"] != "killed"), None)


def main_fault(run: dict) -> dict | None:
    return hidden_fault(run) or next(iter(run["faults"]), None)


def alerts(run: dict) -> list[dict]:
    """First sample of each alert spell, per server and detector (flags are already debounced)."""
    out, prev = [], {}
    for r in run["timeline"]:
        for key, label in DETECTORS.items():
            k = (r["server_id"], key)
            if r[key] and not prev.get(k):
                out.append({**r, "detector": label, "key": key})
            prev[k] = r[key]
    return out


# ---------------------------------------------------------------- charts


def chart(run: dict, feature: str, label: str) -> str:
    """One small chart: `feature` for every server over time; fault window and alerts marked."""
    rows, fault = run["timeline"], main_fault(run)
    t0, t1 = rows[0]["ts"], rows[-1]["ts"]
    top = max(r[feature] for r in rows)
    if feature == "err_ratio":
        y_max = max(0.4, top)
        ticks = [0, 0.1, 0.2, 0.3, 0.4]
        fmt = "{:.0%}"
    else:
        y_max = max(100, 50 * (int(top * 1.15) // 50 + 1))
        ticks = [y_max * i / 4 for i in range(5)]
        fmt = "{:.0f}"

    def x(ts: float) -> float:
        return PAD_L + (ts - t0) / (t1 - t0) * (W - PAD_L - PAD_R)

    def y(v: float) -> float:
        return H - PAD_B - v / y_max * (H - PAD_B - PAD_T)

    parts = []
    if fault:
        x0, x1 = x(max(fault["start"], t0)), x(min(fault["end"], t1))
        parts.append(
            f'<rect class="window" x="{x0:.1f}" y="{PAD_T}" width="{max(x1 - x0, 2):.1f}" '
            f'height="{H - PAD_B - PAD_T}"/>'
        )
    for v in ticks:
        yv = f"{y(v):.1f}"
        parts.append(f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{yv}" y2="{yv}"/>')
        parts.append(
            f'<text x="{PAD_L - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{fmt.format(v)}</text>'
        )
    for t in range(0, int(t1 - t0) + 1, 30):
        parts.append(f'<text x="{x(t0 + t):.1f}" y="{H - 8}" text-anchor="middle">{t} s</text>')

    focus = fault["server_id"] if fault else None
    step = run.get("model", {}).get("step_sec", 5)
    for sid in sorted({r["server_id"] for r in rows}, key=lambda s: s == focus):
        cls = "focus" if sid == focus else "other"  # the fault's server is drawn last, on top
        seg, prev_ts = [], None
        for r in (r for r in rows if r["server_id"] == sid):
            if prev_ts is not None and r["ts"] - prev_ts > 1.5 * step and seg:
                # a gap = no traffic (e.g. removed by the gateway): never draw a line across it
                parts.append(f'<polyline class="{cls}" points="{" ".join(seg)}"/>')
                seg = []
            seg.append(f"{x(r['ts']):.1f},{y(r[feature]):.1f}")
            prev_ts = r["ts"]
        if seg:
            parts.append(f'<polyline class="{cls}" points="{" ".join(seg)}"/>')

    for a in alerts(run):
        ax, ay = x(a["ts"]), y(a[feature])
        if a["key"] == "z_flag":
            parts.append(f'<circle class="mark" cx="{ax:.1f}" cy="{ay:.1f}" r="5"/>')
        else:
            parts.append(
                f'<rect class="mark hollow" x="{ax - 6:.1f}" y="{ay - 6:.1f}" '
                'width="12" height="12"/>'
            )

    parts.append(f'<line class="cursor" x1="0" x2="0" y1="{PAD_T}" y2="{H - PAD_B}"/>')
    return (
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(label)}" '
        f'data-t0="{t0}" data-t1="{t1}">{"".join(parts)}</svg>'
    )


def summary(run: dict) -> str:
    fault = main_fault(run)
    pick = next(s for s in run["scores"] if (s["method"], s["k"]) == ("z-score", 2))
    if fault is None:
        return (
            "No fault was injected, so any alert here is a false alarm. "
            f"The z-score raised {pick['false_alarms']}."
        )
    hit = next(f for f in pick["faults"] if f["server_id"] == fault["server_id"])
    ai, hc = hit["ai_detect_sec"], hit["health_check_sec"]
    return (
        f"{esc(fault['server_id'])} {FAULT_WORDS[fault['kind']]} for "
        f"{fault['end'] - fault['start']:.0f} s. "
        f"The AI {'noticed after ' + sec(ai) if ai is not None else 'never noticed'}; "
        f"the health check {'noticed after ' + sec(hc) if hc is not None else 'never noticed'}."
    )


def panel(run_id: str, run: dict) -> str:
    name = esc(RUN_NAMES.get(run_id, run_id))
    fault = main_fault(run)
    t0 = run["timeline"][0]["ts"]
    points = [
        [round(r["ts"] - t0, 1), r["server_id"], r["err_ratio"], r["p95_ms"]]
        for r in run["timeline"]
    ]
    items = "".join(
        f"<li><span class='mk {'circle' if a['key'] == 'z_flag' else 'square'}' "
        f"aria-hidden='true'></span><b>{a['ts'] - t0:.0f} s</b> {esc(a['server_id'])}, "
        f"{esc(a['detector'])}: errors {a['err_ratio']:.0%}, p95 {a['p95_ms']:.0f} ms</li>"
        for a in alerts(run)
    )
    items = items or "<li>No alerts in this run.</li>"
    focus = esc(fault["server_id"]) if fault else "one server"
    return f"""<section class="panel" id="panel-{run_id}" role="tabpanel"
 aria-labelledby="tab-{run_id}">
<h3 class="panel-title">{name}</h3>
<p class="says">{summary(run)}</p>
<figure tabindex="0" aria-describedby="readout-{run_id}">
<div class="pair">
<div><p class="chart-title">Error ratio, %</p>
{chart(run, "err_ratio", f"Error ratio per server, {name} run")}</div>
<div><p class="chart-title">p95 latency, ms</p>
{chart(run, "p95_ms", f"p95 latency per server, {name} run")}</div>
</div>
<div class="key"><span><span class="swatch focus"></span>{focus}</span>
<span><span class="swatch other"></span>other servers</span>
<span><span class="swatch window"></span>fault window</span>
<span><span class="mk circle"></span>z-score alert</span>
<span><span class="mk square"></span>forest alert</span></div>
<p class="readout" id="readout-{run_id}" aria-live="polite">Point at a chart, or focus it
and press the arrow keys, to read every server at one moment.</p>
<script type="application/json" class="points">{json.dumps(points)}</script>
</figure>
<h4>Alerts</h4>
<ol class="alerts">{items}</ol>
</section>"""


# ---------------------------------------------------------------- tables and page


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


def steps(model: dict) -> str:
    mean = dict(zip(model["features"], model["mean"], strict=True))
    std = dict(zip(model["features"], model["std"], strict=True))
    return f"""<section class="how" aria-labelledby="how-h">
<h2 id="how-h">How it decides</h2>
<ol class="steps">
<li><span class="n">1</span><h3>Learn normal</h3><p>One clean 3-minute run. Normal was
{mean["err_ratio"]:.0%} errors (wiggle {std["err_ratio"]:.0%}) and p95
{mean["p95_ms"]:.0f} ms (wiggle {std["p95_ms"]:.0f} ms).</p></li>
<li><span class="n">2</span><h3>Score every {model["step_sec"]} s</h3><p>For each server: how
many wiggles from normal is it now? More than {model["z_limit"]:.0f} counts as odd.</p></li>
<li><span class="n">3</span><h3>Two in a row</h3><p>Only two odd samples in a row raise an
alert, so one random blip never does.</p></li>
</ol>
</section>"""


# Tabs (state kept in the URL hash) and the chart readout. Plain JS, no library.
SCRIPT = """
(() => {
  document.documentElement.classList.add('js');
  const tabs = [...document.querySelectorAll('[role=tab]')];
  const select = (t, save) => {
    tabs.forEach((x) => {
      const on = x === t;
      x.setAttribute('aria-selected', on);
      x.tabIndex = on ? 0 : -1;
      document.getElementById(x.getAttribute('aria-controls')).hidden = !on;
    });
    if (save) history.replaceState(null, '', '#' + t.id.slice(4));
  };
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => select(t, true));
    t.addEventListener('keydown', (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (!d) return;
      const n = tabs[(i + d + tabs.length) % tabs.length];
      select(n, true);
      n.focus();
      e.preventDefault();
    });
  });
  const fromUrl = tabs.find((t) => t.id === 'tab-' + location.hash.slice(1));
  if (tabs.length) select(fromUrl || tabs.find((t) => 'default' in t.dataset) || tabs[0]);

  const plot = (span) => (t) => __PAD_L__ + (t / span) * (__W__ - __PAD_L__ - __PAD_R__);
  document.querySelectorAll('.panel figure').forEach((fig) => {
    const pts = JSON.parse(fig.querySelector('.points').textContent);
    const times = [...new Set(pts.map((p) => p[0]))];
    const svgs = [...fig.querySelectorAll('svg')];
    const out = fig.querySelector('.readout');
    let idx = -1;
    const show = (i) => {
      idx = Math.max(0, Math.min(times.length - 1, i));
      const t = times[idx];
      svgs.forEach((s) => {
        const x = plot(s.dataset.t1 - s.dataset.t0)(t);
        const c = s.querySelector('.cursor');
        c.setAttribute('x1', x);
        c.setAttribute('x2', x);
        c.classList.add('on');
      });
      out.textContent = t.toFixed(0) + ' s: ' + pts.filter((p) => p[0] === t)
        .map((p) => p[1] + ' errors ' + Math.round(p[2] * 100) + '%, p95 '
          + Math.round(p[3]) + ' ms').join('; ');
    };
    fig.addEventListener('pointermove', (e) => {
      const s = e.target.closest('svg');
      if (!s) return;
      const r = s.getBoundingClientRect();
      const vx = ((e.clientX - r.left) / r.width) * __W__;
      const span = s.dataset.t1 - s.dataset.t0;
      const t = ((vx - __PAD_L__) / (__W__ - __PAD_L__ - __PAD_R__)) * span;
      let best = 0;
      times.forEach((v, i) => { if (Math.abs(v - t) < Math.abs(times[best] - t)) best = i; });
      show(best);
    });
    fig.addEventListener('keydown', (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (d) { show(idx < 0 ? 0 : idx + d); e.preventDefault(); }
    });
  });
})();
"""
for _k, _v in {"__W__": W, "__PAD_L__": PAD_L, "__PAD_R__": PAD_R}.items():
    SCRIPT = SCRIPT.replace(_k, str(_v))


def render(runs: dict[str, dict]) -> str:
    sick_id = next((k for k, r in runs.items() if hidden_fault(r)), None)
    if sick_id:
        sick = runs[sick_id]
        fault = hidden_fault(sick)
        pick = next(s for s in sick["scores"] if (s["method"], s["k"]) == ("z-score", 2))
        hit = next(f for f in pick["faults"] if f["server_id"] == fault["server_id"])
        peak = max(r["err_ratio"] for r in sick["timeline"] if r["server_id"] == fault["server_id"])
        h1 = f"{esc(fault['server_id'])} broke quietly. "
        h1 += f"The AI saw it in {sec(hit['ai_detect_sec'])}."
        lead = (
            f"For {fault['end'] - fault['start']:.0f} s it failed up to {peak:.0%} of requests "
            "while still answering “ready”. Health checks can’t see that. Numbers can."
        )
        plates = (
            '<dl class="plates">'
            f'<div class="plate ink"><dt>AI noticed</dt><dd>{sec(hit["ai_detect_sec"])}</dd></div>'
            f'<div class="plate"><dt>Health check noticed</dt>'
            f"<dd>{sec(hit['health_check_sec'])}</dd></div></dl>"
        )
    else:
        h1, lead, plates = "No hidden fault in these runs.", "Run make ai-eval to add one.", ""

    order = sorted(runs, key=lambda k: k != sick_id)  # the headline run first
    first = sick_id or order[0]
    tablist = "".join(
        f'<button type="button" role="tab" class="tab" id="tab-{k}" aria-controls="panel-{k}" '
        f'aria-selected="{str(k == first).lower()}"{" data-default" if k == first else ""}>'
        f"{esc(RUN_NAMES.get(k, k))}</button>"
        for k in order
    )
    panels = "".join(panel(k, runs[k]) for k in order)
    model = next((r["model"] for r in runs.values() if "model" in r), None)

    created = min(r["created"] for r in runs.values())[:10]
    kills = [
        f["health_check_sec"]
        for r in runs.values()
        for f in r["faults"]
        if f["kind"] == "killed" and f["health_check_sec"] is not None
    ]
    kill_hc = sec(min(kills)) if kills else "a few seconds"
    z_alarms = {
        k: sum(
            x["false_alarms"]
            for r in runs.values()
            for x in r["scores"]
            if x["method"] == "z-score" and x["k"] == k
        )
        for k in (1, 2)
    }
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ResiliGuard AI early warning, results</title>
<meta name="description" content="Can the AI spot a sick server that health checks miss?">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet"
 href="https://fonts.googleapis.com/css2?family=Chivo+Mono:wght@400;500;800&display=swap">
<style>
{CSS.read_text()}
</style>
</head>
<body>
<a class="skip" href="#main">Skip to results</a>
<header class="bar"><div class="container">
<span class="wordmark">ResiliGuard</span><p>AI early warning, runs from {esc(created)}</p>
</div></header>
<main id="main"><div class="container">
<section class="head" aria-labelledby="h1">
<div><h1 id="h1">{h1}</h1><p class="lead">{lead}</p></div>
{plates}
</section>
{steps(model) if model else ""}
<section class="runs" aria-labelledby="runs-h">
<div class="runs-head"><h2 id="runs-h">Each run, sample by sample</h2>
<div class="tabs" role="tablist" aria-label="Runs">{tablist}</div></div>
{panels}
</section>
<section class="board" aria-labelledby="board-h">
<h2 id="board-h">Every run, every detector</h2>
<p>Marked row: the default for live use. False alarm = an alert while nothing was broken.</p>
<div class="scroll">{board(runs)}</div>
</section>
<section class="findings" aria-label="What we learned">
<div><h3>A crash is the health check’s job</h3><p>A killed server is caught in {kill_hc}
and gets no traffic after that, so the AI has nothing to judge. That is fine.</p></div>
<div><h3>Two in a row stops false alarms</h3><p>Z-score false alarms across all runs:
{z_alarms[1]} when one odd sample is enough, {z_alarms[2]} when it needs two in a row.
The cost is one more sample: 5 s slower.</p></div>
<div><h3>The forest adds nothing yet</h3><p>With only two numbers per server, Isolation
Forest finds the same faults as a z-score. It should help once we add more numbers.</p></div>
</section>
</div></main>
<footer><div class="container">Made by <code>python -m ai.report</code> from
<code>python -m ai.anomaly evaluate</code> results.
Fake servers, real Prometheus data.</div></footer>
<script>{SCRIPT}</script>
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
