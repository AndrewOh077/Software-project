"""Week 5 Mode A vs Mode B table, from made-up Locust CSVs."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))

import compare_modes  # noqa: E402

HEAD = "Type,Name,Request Count,Failure Count,99%\n"


def locust_csv(folder: Path, name: str, total: tuple, critical: tuple, start: float) -> None:
    (folder / f"{name}_stats.csv").write_text(
        HEAD
        + f"GET,/work [normal],{total[0] - critical[0]},{total[1] - critical[1]},50\n"
        + f"GET,/work [critical],{critical[0]},{critical[1]},50\n"
        + f",Aggregated,{total[0]},{total[1]},{total[2]}\n"
    )
    (folder / f"{name}_stats_history.csv").write_text(
        "Timestamp,User Count\n" + "".join(f"{start + t},20\n" for t in range(0, 181, 10))
    )


def event(ts: float, name: str, sid: str = "app-2") -> str:
    from datetime import UTC, datetime

    iso = datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")
    return json.dumps({"ts": iso, "source": "x", "event": name, "server_id": sid}) + "\n"


def test_table_compares_modes_and_only_counts_recovery_inside_the_run(tmp_path):
    a0, b0 = 1_000_000.0, 1_000_500.0
    locust_csv(tmp_path, "modeA-failure", (3000, 300, 120), (300, 30), a0)
    locust_csv(tmp_path, "modeB-failure", (3000, 3, 60), (300, 0), b0)
    locust_csv(tmp_path, "modeB-normal", (3000, 0, 55), (300, 0), b0 - 400)
    (tmp_path / "events.jsonl").write_text(
        event(a0 + 60, "server_killed")  # Mode A: nobody ever marks it, so no MTTR
        + event(b0 + 60, "server_killed")
        + event(b0 + 61, "backend_marked_unhealthy")
        + event(b0 + 75, "backend_marked_healthy")
    )
    results = compare_modes.collect(tmp_path, tmp_path / "events.jsonl")
    a, b = results["failure"]["A"], results["failure"]["B"]
    assert (a["failed"], a["critical_ok"], a["mttr_sec"]) == (0.1, 0.9, None)
    assert (b["failed"], b["critical_ok"], b["mttr_sec"]) == (0.001, 1.0, 15.0)

    md = compare_modes.markdown(results)
    assert "| failure | Mode A, no protection | 3000 | 10.0% | 120 ms | 90.0% | n/a" in md
    assert "| failure | Mode B, rules | 3000 | 0.1% | 60 ms | 100.0% | 15.0 s |" in md
    assert "**failure**: failed 10.0% → 0.1%" in md
    assert "Missing runs: modeA-normal, modeA-spike, modeB-spike." in md


def test_no_runs_is_a_clear_message(tmp_path, capsys):
    assert compare_modes.main([str(tmp_path)]) == 1
    assert "make results" in capsys.readouterr().out
