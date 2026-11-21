"""Week 8–9 AI early warning — no Prometheus needed, all data is made up here."""

import json
import random

import pytest

from ai.anomaly import (
    Fault,
    debounce,
    evaluate,
    load_faults,
    read_rows,
    score,
    to_rows,
    train,
    write_rows,
)
from experiments.mttr import parse_ts


def normal_rows(n=120, servers=("app-1", "app-2", "app-3"), seed=1):
    rnd = random.Random(seed)
    return [
        {
            "ts": 1000.0 + 5 * (i // len(servers)),
            "server_id": servers[i % len(servers)],
            "req_rate": 6.7,
            "err_ratio": 0.0,
            "p95_ms": rnd.gauss(28, 2),
        }
        for i in range(n)
    ]


def row(ts, sid="app-3", err=0.0, p95=28.0):
    return {"ts": ts, "server_id": sid, "req_rate": 6.7, "err_ratio": err, "p95_ms": p95}


@pytest.fixture(scope="module")
def det():
    return train(normal_rows())


def test_to_rows_joins_queries_and_skips_idle_or_empty():
    req = {(5.0, "app-1"): 10.0, (5.0, "app-2"): 0.1, (5.0, "app-3"): 10.0}
    err = {(5.0, "app-1"): 3.0}  # app-3 has no 5xx series at all -> 0
    p95 = {(5.0, "app-1"): 40.0, (5.0, "app-2"): 30.0, (5.0, "app-3"): float("nan")}
    rows = to_rows(req, err, p95)
    assert [(r["server_id"], r["err_ratio"]) for r in rows] == [("app-1", 0.3)]


def test_csv_round_trip(tmp_path):
    rows = normal_rows(6)
    write_rows(rows, tmp_path / "m.csv")
    back = read_rows(tmp_path / "m.csv")
    assert back[0]["server_id"] == "app-1" and back[0]["p95_ms"] == pytest.approx(rows[0]["p95_ms"])


def test_training_needs_enough_data():
    with pytest.raises(ValueError):
        train(normal_rows(5))


def test_normal_rows_are_not_flagged(det):
    rows = normal_rows(seed=1)
    assert not any(det.zscore_flags(rows))
    assert not any(det.forest_flags(rows))


@pytest.mark.parametrize("bad", [row(0, err=0.3), row(0, p95=900)])
def test_both_detectors_catch_errors_and_slowness(det, bad):
    assert det.zscore_flags([bad]) == [True]
    assert det.forest_flags([bad]) == [True]


def test_faster_or_cleaner_than_normal_is_not_sick(det):
    # found in a real spike run: a few very fast requests at the end looked "odd" (z = -4)
    fast = row(0, p95=10)
    assert det.zscore_flags([fast]) == [False]
    assert det.forest_flags([fast]) == [False]


def test_forest_is_not_blind_to_a_feature_that_never_moved_in_training(det):
    # err_ratio was 0 in every training row; without jitter the forest never cuts on it
    assert det.forest_scores([row(0, err=0.3)])[0] < det.forest_scores([row(0)])[0]


def test_debounce_needs_k_in_a_row_per_server():
    rows = [row(0, "a"), row(0, "b"), row(5, "a"), row(5, "b"), row(10, "a")]
    flags = [True, True, False, True, True]
    assert debounce(flags, rows, 2) == [False, False, False, True, False]


def test_score_counts_false_alarms_and_detection_delay():
    fault = Fault("app-3", "errors", start=100, end=160)
    rows = [row(t) for t in range(0, 300, 5)]
    flags = [r["ts"] in {20, 25, 110, 115, 120} for r in rows]  # 1 false alarm, then the fault
    s = score(rows, flags, [fault], "z-score", 1)
    assert s.false_alarms == 1 and s.false_positive_rows == 2
    assert s.faults[0]["ai_detect_sec"] == 10
    assert s.normal_rows == len(rows) - len(range(100, 181, 5))  # fault + 20 s grace excluded


def test_load_faults_reads_kills_and_injected_errors(tmp_path):
    lines = [
        {"ts": "2026-11-20T10:00:00Z", "event": "server_killed", "server_id": "app-2"},
        {"ts": "2026-11-20T10:00:01Z", "event": "backend_marked_unhealthy", "server_id": "app-2"},
        {"ts": "2026-11-20T10:00:30Z", "event": "backend_marked_healthy", "server_id": "app-2"},
        {
            "ts": "2026-11-20T10:01:00Z",
            "event": "errors_injected",
            "server_id": "app-3",
            "detail": {"duration_sec": 60},
        },
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps({"source": "x", **e}) for e in lines) + "\nbroken\n")
    t = lambda s: parse_ts(f"2026-11-20T{s}Z").timestamp()  # noqa: E731
    killed, sick = load_faults(path, t("09:59:00"), t("10:05:00"))
    assert load_faults(path, t("10:00:30"), t("10:05:00")) == [sick]  # kill was before this run
    assert (killed.kind, killed.end - killed.start, killed.health_check_sec) == ("killed", 30, 1.0)
    assert (sick.kind, sick.end - sick.start, sick.health_check_sec) == ("errors", 60, None)


def test_evaluate_end_to_end(det):
    rows = normal_rows(seed=2) + [row(1300 + 5 * i, err=0.3) for i in range(6)]
    scores = evaluate(rows, [Fault("app-3", "errors", 1300, 1330)], det)
    assert {(s.method, s.k) for s in scores} == {
        ("z-score", 1),
        ("z-score", 2),
        ("isolation-forest", 1),
        ("isolation-forest", 2),
    }
    for s in scores:
        assert s.faults[0]["ai_detect_sec"] == 5 * (s.k - 1)
