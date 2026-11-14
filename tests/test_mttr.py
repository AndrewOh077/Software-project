"""MTTR calculator: pairs server_killed -> unhealthy -> healthy for the same server."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments"))

import mttr  # noqa: E402

SAMPLE = ROOT / "experiments/sample_data/events_sample.jsonl"


def write(tmp_path, *lines):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(x) if isinstance(x, dict) else x for x in lines))
    return path


def ev(ts, event, server="app-2", source="gateway"):
    return {
        "ts": f"2026-11-20T15:00:{ts}Z",
        "source": source,
        "event": event,
        "server_id": server,
        "detail": {},
    }


def incidents_from(path):
    events, _ = mttr.load_events(path)
    return mttr.find_incidents(events)


def test_simple_incident(tmp_path):
    path = write(
        tmp_path,
        ev("00.000", "server_killed", source="chaos"),
        ev("01.250", "backend_marked_unhealthy"),
        ev("16.000", "backend_marked_healthy"),
    )
    [inc] = incidents_from(path)
    assert inc.detection_sec == 1.25
    assert inc.mttr_sec == 16.0


def test_events_before_the_kill_are_ignored(tmp_path):
    path = write(
        tmp_path,
        ev("00.000", "backend_marked_unhealthy"),  # startup noise
        ev("01.000", "backend_marked_healthy"),
        ev("10.000", "server_killed", source="chaos"),
        ev("11.000", "backend_marked_unhealthy"),
        ev("20.000", "backend_marked_healthy"),
    )
    [inc] = incidents_from(path)
    assert inc.mttr_sec == 10.0


def test_other_servers_do_not_count(tmp_path):
    path = write(
        tmp_path,
        ev("00.000", "server_killed", source="chaos"),
        ev("02.000", "backend_marked_healthy", server="app-3"),
        ev("30.000", "backend_marked_healthy"),
    )
    [inc] = incidents_from(path)
    assert inc.mttr_sec == 30.0


def test_not_recovered(tmp_path):
    path = write(
        tmp_path,
        ev("00.000", "server_killed", source="chaos"),
        ev("01.000", "backend_marked_unhealthy"),
    )
    [inc] = incidents_from(path)
    assert not inc.recovered and inc.mttr_sec is None
    s = mttr.summarize([inc])
    assert (s.incidents, s.recovered, s.not_recovered, s.mttr_mean_sec) == (1, 0, 1, None)


def test_killed_again_before_recovering(tmp_path):
    path = write(
        tmp_path,
        ev("00.000", "server_killed", source="chaos"),
        ev("10.000", "server_killed", source="chaos"),
        ev("15.000", "backend_marked_healthy"),
    )
    first, second = incidents_from(path)
    assert not first.recovered
    assert second.mttr_sec == 5.0


def test_events_out_of_order_in_file_are_sorted(tmp_path):
    path = write(
        tmp_path,
        ev("20.000", "backend_marked_healthy"),
        ev("00.000", "server_killed", source="chaos"),
    )
    [inc] = incidents_from(path)
    assert inc.mttr_sec == 20.0


def test_sample_file_end_to_end():
    events, skipped = mttr.load_events(SAMPLE)
    incidents = mttr.find_incidents(events)
    assert skipped == 1  # the broken line
    assert [(i.server_id, i.detection_sec, i.mttr_sec) for i in incidents] == [
        ("app-2", 1.5, 20.0),
        ("app-1", 2.0, 40.0),
        ("app-3", 1.0, None),
    ]
    s = mttr.summarize(incidents)
    assert s.recovered == 2 and s.not_recovered == 1
    assert s.mttr_mean_sec == 30.0
    assert s.mttr_median_sec == 30.0
    assert s.mttr_max_sec == 40.0
    assert s.detection_mean_sec == pytest.approx(1.5)


def test_cli_report_and_csv(tmp_path, capsys):
    out = tmp_path / "mttr.csv"
    assert mttr.main([str(SAMPLE), "--csv", str(out)]) == 0
    report = capsys.readouterr().out
    assert "NOT RECOVERED" in report
    assert "mean 30.00s" in report
    rows = out.read_text().splitlines()
    assert rows[0].startswith("server_id,killed_at")
    assert len(rows) == 4  # header + 3 incidents


def test_cli_json(capsys):
    assert mttr.main([str(SAMPLE), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["mttr_mean_sec"] == 30.0


def test_cli_missing_file(tmp_path):
    assert mttr.main([str(tmp_path / "nope.jsonl")]) == 1
