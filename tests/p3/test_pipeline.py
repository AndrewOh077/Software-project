"""Person 3's pipeline pieces, with made-up logs: no Docker, no network."""

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "experiments"), str(ROOT / "loadtest"), str(ROOT / "monitoring")]

import compare  # noqa: E402
import gateway_exporter  # noqa: E402
import mttr  # noqa: E402
import stages  # noqa: E402

from ai import incident  # noqa: E402
from ai.anomaly import Fault  # noqa: E402


def jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"half a line\n', encoding="utf-8")


# ---------------------------------------------------------------- Week 4: MTTR


def test_mttr_pairs_each_break_with_detection_and_recovery():
    chaos = [
        {"time": 100, "action": "kill", "target": "app2", "ok": True},
        {"time": 150, "action": "slow", "target": "app1", "ok": True},  # not a break
        {"time": 300, "action": "kill", "target": "app2", "ok": True},  # second incident, never recovered
    ]
    events = [
        {"time": 99, "type": "recovered", "server": "app2"},  # before the break: ignored
        {"time": 101.2, "type": "server_down", "server": "app1"},  # other server
        {"time": 101.4, "type": "server_down", "server": "app2"},
        {"time": 114, "type": "recovered", "server": "app2"},
        {"time": 301, "type": "server_down", "server": "app2"},
    ]
    first, second = mttr.find_incidents(chaos, events)
    assert (first.detection_s, first.mttr_s) == (1.4, 14)
    assert (second.detection_s, second.mttr_s) == (1, None)
    assert mttr.summarize([first, second]) == {
        "incidents": 2,
        "recovered": 1,
        "mttr_mean_s": 14,
        "mttr_max_s": 14,
        "detection_mean_s": 1.2,
    }


def test_mttr_ignores_controller_events_after_a_person_fixed_it():
    # Mode A: no controller; chaos `start` brings app2 back; the controller starts much later
    chaos = [
        {"time": 100, "action": "kill", "target": "app2", "ok": True},
        {"time": 160, "action": "start", "target": "app2", "ok": True},
    ]
    events = [
        {"time": 900, "type": "server_down", "server": "app2"},
        {"time": 910, "type": "recovered", "server": "app2"},
    ]
    (inc,) = mttr.find_incidents(chaos, events)
    assert (inc.detection_s, inc.mttr_s) == (None, None)


def test_mttr_cli_skips_broken_lines(tmp_path, capsys):
    jsonl(tmp_path / "chaos.jsonl", [{"time": 1, "action": "kill", "target": "app2", "ok": True}])
    jsonl(
        tmp_path / "events.jsonl",
        [
            {"time": 2, "type": "server_down", "server": "app2"},
            {"time": 9, "type": "recovered", "server": "app2"},
        ],
    )
    assert mttr.main(["--data", str(tmp_path), "--csv", str(tmp_path / "m.csv")]) == 0
    assert "mean MTTR 8" in capsys.readouterr().out
    assert next(csv.DictReader((tmp_path / "m.csv").open(encoding="utf-8")))["mttr_s"] == "8"


# ---------------------------------------------------------------- Week 10: incident assistant


def test_rule_analyst_reads_symptoms():
    crash = {"timeline": [{"t": 1, "source": "controller", "what": "server_down"}]}
    errs = {"timeline": [], "peak_error_ratio": 0.25, "peak_p95_ms": 90}
    slow = {"timeline": [], "peak_error_ratio": 0, "peak_p95_ms": 900}
    assert incident.rule_analyst(crash)["action"] == "none"  # the controller already fixed it
    assert incident.rule_analyst(errs)["action"] == "lower_traffic"
    assert "slow" in incident.rule_analyst(slow)["root_cause"]


def test_llm_answers_outside_the_allowed_list_are_refused(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(
        incident,
        "llm_analyst",
        lambda s: {"root_cause": "x", "confidence": 7, "action": "delete_database", "why": "trust me"},
    )
    out = incident.analyse({"timeline": []}, use_llm=True)
    assert (out["action"], out["refused_action"], out["why"]) == ("none", "delete_database", "")
    assert out["confidence"] == 1.0 and out["executed"] is False


def test_broken_llm_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(incident, "llm_analyst", lambda s: (_ for _ in ()).throw(OSError("down")))
    out = incident.analyse({"timeline": [], "peak_error_ratio": 0.3}, use_llm=True)
    assert out["analyst"] == "rules" and "LLM failed" in out["note"]


def test_postmortem_hides_the_answer_until_the_end(tmp_path):
    jsonl(
        tmp_path / "events.jsonl",
        [
            {
                "time": 105,
                "type": "warning",
                "server": "app3",
                "reason": "errors 20%",
                "action": "weight 50 for 30s",
            }
        ],
    )
    jsonl(
        tmp_path / "ai.jsonl",
        [{"time": 105, "type": "anomaly_detected", "server": "app3", "err_ratio": 0.2, "p95_ms": 90}],
    )
    f = Fault("app3", "errors", 100, 160)
    s = incident.symptoms(f, tmp_path, prom=None)
    assert "errors" not in json.dumps({k: v for k, v in s.items() if k != "timeline"})
    md = incident.postmortem(f, s, incident.analyse({**s, "peak_error_ratio": 0.2}, use_llm=False))
    assert "lowered its traffic after the AI warning" in md and "AI noticed after 5.0 s" in md
    assert md.index("`errors` on app3") > md.index("Suggested follow-up")


# ---------------------------------------------------------------- Week 11–12: comparison table


def locust_csvs(raw: Path, name: str, total: int, fails: dict, crit: tuple, p99: int) -> None:
    head = "Type,Name,Request Count,Failure Count,99%\n"
    nf = sum(fails.values())
    (raw / f"{name}_stats.csv").write_text(
        head + f"GET,/work [critical],{crit[0]},{crit[1]},50\n,Aggregated,{total},{nf},{p99}\n",
        encoding="utf-8",
    )
    (raw / f"{name}_failures.csv").write_text(
        "Method,Name,Error,Occurrences\n"
        + "".join(f"GET,/work [normal],CatchResponseError('{k}'),{v}\n" for k, v in fails.items()),
        encoding="utf-8",
    )


def test_compare_splits_errors_from_refusals_and_finds_mttr(tmp_path):
    raw, data = tmp_path / "raw", tmp_path / "data"
    raw.mkdir(), data.mkdir()
    locust_csvs(raw, "A-kill", 1000, {"error:503": 100}, (100, 10), 900)
    locust_csvs(raw, "B-kill", 1000, {"error:503": 5, "rejected:429": 20}, (100, 0), 400)
    (raw / "runs.json").write_text(
        json.dumps({"A-kill": {"start": 0, "end": 180}, "B-kill": {"start": 500, "end": 680}}),
        encoding="utf-8",
    )
    jsonl(
        data / "chaos.jsonl",
        [
            {"time": 60, "action": "kill", "target": "app2", "ok": True},
            {"time": 560, "action": "kill", "target": "app2", "ok": True},
        ],
    )
    jsonl(
        data / "events.jsonl",
        [
            {"time": 561, "type": "server_down", "server": "app2"},
            {"time": 574, "type": "recovered", "server": "app2"},
        ],
    )
    res = compare.collect(raw, data)["kill"]
    assert (res["A"]["errors"], res["A"]["critical_ok"], res["A"]["mttr_s"]) == (0.1, 0.9, None)
    assert (res["B"]["errors"], res["B"]["rejected"], res["B"]["mttr_s"]) == (0.005, 0.02, 14)
    md = compare.markdown({"kill": res})
    assert "| app2 killed (scenario 2) | A, no protection | 1000 | 10.0% | 0.0% | 900 ms | 90.0% |" in md
    assert "never (no controller)" in md and "14.0 s" in md


# ---------------------------------------------------------------- monitoring + load test shapes


def test_exporter_turns_gateway_json_into_prometheus_text(monkeypatch):
    answers = {
        "/metrics": {
            "mode": "emergency",
            "enabled_backends": 2,
            "concurrency_limit": 60.0,
            "concurrency_max": 90,
            "inflight": 3,
        },
        "/admin/backends": {
            "mode": "emergency",
            "backends": {"app1": {"enabled": True, "weight": 100}, "app2": {"enabled": False, "weight": 100}},
        },
    }
    monkeypatch.setattr(gateway_exporter, "get", answers.__getitem__)
    text = gateway_exporter.render()
    assert "gateway_emergency_mode 1" in text and "gateway_concurrency_limit 60.0" in text
    assert 'gateway_backend_weight{server="app2"} 0' in text  # disabled = no traffic


def test_exporter_reports_a_down_gateway(monkeypatch):
    monkeypatch.setattr(gateway_exporter, "get", lambda p: (_ for _ in ()).throw(OSError()))
    assert gateway_exporter.render() == "gateway_up 0\n"


@pytest.mark.parametrize("shape", [stages.normal, stages.spike, stages.fault])
def test_every_scenario_lasts_three_minutes_then_stops(shape):
    assert stages.users_at(shape(), 179) is not None and stages.users_at(shape(), 181) is None


def test_spike_is_fifteen_times_calm():
    assert stages.users_at(stages.spike(), 90)[0] == 15 * stages.users_at(stages.spike(), 30)[0]
