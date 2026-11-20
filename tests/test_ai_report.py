"""CLI end to end: train -> evaluate --json -> HTML report (no Prometheus needed)."""

import json
from datetime import UTC, datetime

from ai import anomaly, report
from tests.test_anomaly import normal_rows, row


def run_cli(tmp_path, with_fault: bool):
    anomaly.write_rows(normal_rows(), tmp_path / "normal.csv")
    model = tmp_path / "m.pkl"
    assert anomaly.main(["train", str(tmp_path / "normal.csv"), "--model", str(model)]) == 0

    start = 2000.0
    sick = [row(start + 5 * i, err=0.3 if with_fault and 6 <= i < 12 else 0.0) for i in range(24)]
    anomaly.write_rows(sick, tmp_path / "sick.csv")
    events = []
    if with_fault:
        ts = datetime.fromtimestamp(start + 30, UTC).isoformat().replace("+00:00", "Z")
        events.append(
            {
                "ts": ts,
                "source": "chaos",
                "event": "errors_injected",
                "server_id": "app-3",
                "detail": {"error_rate": 0.3, "duration_sec": 30},
            }
        )
    (tmp_path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    out = tmp_path / "eval.json"
    args = ["evaluate", str(tmp_path / "sick.csv"), str(tmp_path / "events.jsonl")]
    assert anomaly.main([*args, "--model", str(model), "--json", str(out)]) == 0

    page = tmp_path / "report.html"
    assert report.main([str(page), f"sick={out}"]) == 0
    return page.read_text()


def test_report_shows_the_hidden_fault(tmp_path):
    page = run_cli(tmp_path, with_fault=True)
    assert "app-3 broke quietly. The AI saw it in 5 s." in page
    assert "<svg" in page and "never" in page  # health check never noticed


def test_report_without_a_fault_says_so(tmp_path):
    page = run_cli(tmp_path, with_fault=False)
    assert "No hidden fault in these runs." in page and "<svg" not in page


def test_report_needs_name_equals_path():
    assert report.main(["out.html", "just-a-path.json"]) == 1
