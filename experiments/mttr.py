"""MTTR calculator — how long does it take the system to recover from a server failure?

Reads events.jsonl (docs/contracts.md, "Incident log line") and, for every server_killed,
finds what happened next to the SAME server:

    server_killed ──► backend_marked_unhealthy ──► backend_marked_healthy
         t0                    t1                          t2

    detection time (TTD) = t1 - t0   how fast the gateway noticed
    MTTR                 = t2 - t0   how long until it served traffic again
                                     (definition from docs/contracts.md)

Usage:
    python experiments/mttr.py                                    # default events file
    python experiments/mttr.py path/to/events.jsonl
    python experiments/mttr.py events.jsonl --csv experiments/results/mttr.csv
    python experiments/mttr.py events.jsonl --json                # machine-readable summary

Standard library only, so it runs anywhere Python runs.
"""

import argparse
import csv
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_EVENTS = Path("experiments/results/raw/events.jsonl")

KILL_EVENTS = {"server_killed"}
DETECT_EVENT = "backend_marked_unhealthy"
RECOVER_EVENT = "backend_marked_healthy"


@dataclass
class Event:
    ts: datetime
    source: str
    event: str
    server_id: str | None


@dataclass
class Incident:
    server_id: str
    killed_at: datetime
    detected_at: datetime | None = None
    recovered_at: datetime | None = None

    @property
    def detection_sec(self) -> float | None:
        return _seconds(self.killed_at, self.detected_at)

    @property
    def mttr_sec(self) -> float | None:
        return _seconds(self.killed_at, self.recovered_at)

    @property
    def recovered(self) -> bool:
        return self.recovered_at is not None


@dataclass
class Summary:
    incidents: int
    recovered: int
    not_recovered: int
    mttr_mean_sec: float | None
    mttr_median_sec: float | None
    mttr_max_sec: float | None
    detection_mean_sec: float | None
    detection_max_sec: float | None


def _seconds(start: datetime, end: datetime | None) -> float | None:
    return None if end is None else round((end - start).total_seconds(), 3)


def parse_ts(value: str) -> datetime:
    # "2026-11-13T14:59:46.300Z" -> aware datetime. fromisoformat understands "+00:00", not "Z".
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_events(path: Path) -> tuple[list[Event], int]:
    """Returns (events sorted by time, number of lines skipped because they were broken)."""
    events, skipped = [], 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            events.append(
                Event(
                    parse_ts(raw["ts"]), raw.get("source", ""), raw["event"], raw.get("server_id")
                )
            )
        except (ValueError, KeyError, TypeError):
            skipped += 1  # one bad line shouldn't ruin the whole report
    events.sort(key=lambda e: e.ts)
    return events, skipped


def find_incidents(events: list[Event]) -> list[Incident]:
    """Walk events in time order and build one Incident per server_killed.

    Rules:
      - Only events for the same server_id count.
      - Events before the kill are ignored (e.g. startup noise).
      - Recovery must come after detection when there was a detection.
      - If the same server is killed again before recovering, the earlier incident
        stays "not recovered" and a new one starts.
    """
    incidents: list[Incident] = []
    open_by_server: dict[str, Incident] = {}

    for e in events:
        if e.server_id is None:
            continue
        if e.event in KILL_EVENTS:
            inc = Incident(e.server_id, e.ts)
            incidents.append(inc)
            open_by_server[e.server_id] = inc  # replaces any unrecovered earlier one
            continue

        inc = open_by_server.get(e.server_id)
        if inc is None:
            continue
        if e.event == DETECT_EVENT and inc.detected_at is None:
            inc.detected_at = e.ts
        elif e.event == RECOVER_EVENT:
            inc.recovered_at = e.ts
            del open_by_server[e.server_id]

    return incidents


def summarize(incidents: list[Incident]) -> Summary:
    mttrs = [i.mttr_sec for i in incidents if i.mttr_sec is not None]
    detections = [i.detection_sec for i in incidents if i.detection_sec is not None]

    def r(x):
        return None if x is None else round(x, 3)

    return Summary(
        incidents=len(incidents),
        recovered=len(mttrs),
        not_recovered=len(incidents) - len(mttrs),
        mttr_mean_sec=r(statistics.mean(mttrs)) if mttrs else None,
        mttr_median_sec=r(statistics.median(mttrs)) if mttrs else None,
        mttr_max_sec=r(max(mttrs)) if mttrs else None,
        detection_mean_sec=r(statistics.mean(detections)) if detections else None,
        detection_max_sec=r(max(detections)) if detections else None,
    )


def _fmt(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}s"


def print_report(incidents: list[Incident], summary: Summary, skipped: int) -> None:
    if not incidents:
        print("No server_killed events found — nothing to measure.")
        return
    print(f"{'#':>2}  {'server':8} {'killed at (UTC)':23} {'detected':>9} {'MTTR':>9}")
    for n, i in enumerate(incidents, 1):
        mttr = _fmt(i.mttr_sec) if i.recovered else "NOT RECOVERED"
        print(
            f"{n:>2}  {i.server_id:8} {i.killed_at.strftime('%Y-%m-%d %H:%M:%S.%f')[:23]} "
            f"{_fmt(i.detection_sec):>9} {mttr:>9}"
        )
    print()
    print(
        f"incidents: {summary.incidents}  recovered: {summary.recovered}  "
        f"not recovered: {summary.not_recovered}"
    )
    print(
        f"MTTR      mean {_fmt(summary.mttr_mean_sec)}  median {_fmt(summary.mttr_median_sec)}"
        f"  max {_fmt(summary.mttr_max_sec)}"
    )
    print(
        f"detection mean {_fmt(summary.detection_mean_sec)}  max {_fmt(summary.detection_max_sec)}"
    )
    if skipped:
        print(f"(skipped {skipped} unreadable line(s))")


def write_csv(incidents: list[Incident], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "server_id",
                "killed_at",
                "detected_at",
                "recovered_at",
                "detection_sec",
                "mttr_sec",
                "recovered",
            ]
        )
        for i in incidents:
            w.writerow(
                [
                    i.server_id,
                    i.killed_at.isoformat(),
                    i.detected_at.isoformat() if i.detected_at else "",
                    i.recovered_at.isoformat() if i.recovered_at else "",
                    i.detection_sec if i.detection_sec is not None else "",
                    i.mttr_sec if i.mttr_sec is not None else "",
                    i.recovered,
                ]
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute MTTR from events.jsonl")
    parser.add_argument("events", nargs="?", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--csv", type=Path, help="also write one row per incident to this file")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args(argv)

    if not args.events.exists():
        print(
            f"events file not found: {args.events}\n"
            f"Run a failure scenario first (make failure) or pass a path.",
            file=sys.stderr,
        )
        return 1

    events, skipped = load_events(args.events)
    incidents = find_incidents(events)
    summary = summarize(incidents)

    if args.json:
        print(json.dumps(asdict(summary), indent=2))
    else:
        print_report(incidents, summary, skipped)
    if args.csv:
        write_csv(incidents, args.csv)
        print(f"wrote {args.csv}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
