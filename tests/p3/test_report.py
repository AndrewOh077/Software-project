"""CLI end to end: train -> evaluate --json -> HTML report (no Prometheus, no Docker)."""

import json

from ai import anomaly, report
from tests.p3.test_anomaly import normal_rows, row

MODES = {}  # the eval file made by run_cli, reused by the 3-mode test


def run_cli(tmp_path, with_fault: bool):
    anomaly.write_rows(normal_rows(), tmp_path / "normal.csv")
    model = tmp_path / "m.pkl"
    assert anomaly.main(["train", str(tmp_path / "normal.csv"), "--model", str(model)]) == 0

    start = 2000.0
    sick = [row(start + 5 * i, err=0.3 if with_fault and 6 <= i < 12 else 0.0) for i in range(24)]
    anomaly.write_rows(sick, tmp_path / "sick.csv")
    chaos = []
    if with_fault:
        chaos = [
            {"time": start + 30, "action": "errors", "target": "app3", "ok": True, "rate": 0.3},
            {"time": start + 60, "action": "clear", "target": "app3", "ok": True},
        ]
    (tmp_path / "chaos.jsonl").write_text("".join(json.dumps(e) + "\n" for e in chaos))
    out = tmp_path / "eval.json"
    args = ["evaluate", str(tmp_path / "sick.csv"), "--data", str(tmp_path)]
    assert anomaly.main([*args, "--model", str(model), "--json", str(out)]) == 0
    MODES["file"] = out

    page = tmp_path / "report.html"
    assert report.main([str(page), f"sick={out}"]) == 0
    return page.read_text()


def test_report_shows_the_hidden_fault(tmp_path):
    page = run_cli(tmp_path, with_fault=True)
    assert "app3 broke quietly. The AI saw it in 5 s." in page
    assert "<svg" in page and "never" in page  # health check never noticed


def test_report_without_a_fault_says_so(tmp_path):
    page = run_cli(tmp_path, with_fault=False)
    assert "No hidden fault in these runs." in page
    assert "No fault was injected" in page and "No alerts in this run." in page


def test_report_has_tabs_steps_alerts_and_works_without_js(tmp_path):
    page = run_cli(tmp_path, with_fault=True)
    assert 'role="tab"' in page and 'role="tabpanel"' in page
    assert "How it decides" in page and "Learn normal" in page  # model numbers from evaluate
    assert "app3, z-score: errors 30%" in page  # alert list = the chart in words
    assert " hidden" not in page.split("<script>")[0].split("<body>")[1]  # JS hides, not HTML


def test_chart_does_not_draw_across_a_gap():
    rows = [
        {
            "ts": t,
            "server_id": "app-2",
            "err_ratio": 0.0,
            "p95_ms": 40.0,
            "z_flag": False,
            "forest_flag": False,
        }
        for t in (0, 5, 10, 60, 65)  # no samples between 10 s and 60 s (server was removed)
    ]
    svg = report.chart({"timeline": rows, "faults": []}, "err_ratio", "test")
    assert svg.count("<polyline") == 2


def test_report_needs_name_equals_path():
    assert report.main(["out.html", "just-a-path.json"]) == 1


def test_three_modes_section_leads_the_page(tmp_path):
    run_cli(tmp_path, with_fault=True)
    r = {"requests": 1000, "rejected": 0.0, "p99_ms": 300.0, "critical_ok": 1.0, "had_incident": True}
    modes = {
        "kill": {"A": {**r, "errors": 0.10, "mttr_s": None}, "C": {**r, "errors": 0.01, "mttr_s": 14.2}},
        "errors": {"A": {**r, "errors": 0.10, "mttr_s": None}, "C": {**r, "errors": 0.03, "mttr_s": None}},
    }
    (tmp_path / "modes.json").write_text(json.dumps(modes))
    page_path = tmp_path / "final.html"
    assert report.main([str(page_path), f"sick={MODES['file']}", f"modes={tmp_path / 'modes.json'}"]) == 0
    page = page_path.read_text()
    assert "Same broken servers. 2.0% errors instead of 10.0%." in page  # weighted over fault runs
    assert '<h2 id="ai-h">' in page and page.count("<h1") == 1  # AI part steps down to h2
    assert "14.2 s" in page and "Rules + AI" in page
