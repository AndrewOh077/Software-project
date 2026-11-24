"""Week 4 — MTTR: how long from "a server broke" to "it is serving again"? (Person 3)

Two logs, both written automatically:
  data/chaos.jsonl   chaos/chaos.py   {"time", "action": "kill", "target": "app2", ...}
  data/events.jsonl  the controller   {"time", "type": "server_down" | "recovered", "server", ...}

One incident = a breaking chaos action (kill, stop, pause, sick, break) on a server, then, until the
next chaos action on that server (e.g. a person runs `start`):
  detected   the controller's next `server_down` for that server
  recovered  the controller's next `recovered` for that server (back at 100% traffic)
  MTTR       recovered - broken      detection time = detected - broken

    python experiments/mttr.py                      # uses data/
    python experiments/mttr.py --csv experiments/results/raw/mttr.csv
"""

import argparse
import csv
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
BREAKS = {"kill", "stop", "pause", "sick", "break"}


@dataclass
class Incident:
    server: str
    action: str
    broken_at: float
    detected_at: float | None = None
    recovered_at: float | None = None

    @property
    def detection_s(self) -> float | None:
        return None if self.detected_at is None else round(self.detected_at - self.broken_at, 2)

    @property
    def mttr_s(self) -> float | None:
        return None if self.recovered_at is None else round(self.recovered_at - self.broken_at, 2)


def read_jsonl(path: Path) -> list[dict]:
    """Every valid line; a half-written or broken line is skipped, not fatal."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return sorted(out, key=lambda e: e.get("time", 0))


def find_incidents(chaos: list[dict], events: list[dict]) -> list[Incident]:
    done = [c for c in chaos if c.get("ok", True)]
    incidents = []
    for i, c in enumerate(done):
        if c.get("action") not in BREAKS:
            continue
        inc = Incident(c["target"], c["action"], c["time"])
        # the next chaos action on this server (a person starting/fixing it, or breaking it again)
        # ends the window: later controller events are not this incident's self-healing
        until = next((x["time"] for x in done[i + 1 :] if x["target"] == inc.server), float("inf"))
        for e in events:
            if e.get("server") != inc.server or not inc.broken_at <= e["time"] < until:
                continue
            if e["type"] == "server_down" and inc.detected_at is None:
                inc.detected_at = e["time"]
            elif e["type"] == "recovered" and inc.detected_at is not None:
                inc.recovered_at = e["time"]
                break
        incidents.append(inc)
    return incidents


def summarize(incidents: list[Incident]) -> dict:
    done = [i.mttr_s for i in incidents if i.mttr_s is not None]
    det = [i.detection_s for i in incidents if i.detection_s is not None]
    return {
        "incidents": len(incidents),
        "recovered": len(done),
        "mttr_mean_s": round(statistics.mean(done), 2) if done else None,
        "mttr_max_s": max(done) if done else None,
        "detection_mean_s": round(statistics.mean(det), 2) if det else None,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--data", type=Path, default=DATA, help="folder with chaos.jsonl, events.jsonl")
    p.add_argument("--since", type=float, default=0, help="only incidents after this unix time")
    p.add_argument("--csv", type=Path)
    a = p.parse_args(argv)

    incidents = [
        i
        for i in find_incidents(read_jsonl(a.data / "chaos.jsonl"), read_jsonl(a.data / "events.jsonl"))
        if i.broken_at >= a.since
    ]
    if not incidents:
        print(f"no incidents in {a.data} (break a server: python chaos/chaos.py kill app2)")
        return 1
    print(f"{'server':<7} {'fault':<6} {'detected':>9} {'MTTR':>8}")
    for i in incidents:
        det = "-" if i.detection_s is None else f"{i.detection_s:.1f}s"
        mttr = "not yet" if i.mttr_s is None else f"{i.mttr_s:.1f}s"
        print(f"{i.server:<7} {i.action:<6} {det:>9} {mttr:>8}")
    s = summarize(incidents)
    print(
        f"\n{s['recovered']}/{s['incidents']} recovered, mean MTTR {s['mttr_mean_s']} s, "
        f"mean detection {s['detection_mean_s']} s"
    )
    if a.csv:
        a.csv.parent.mkdir(parents=True, exist_ok=True)
        with a.csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, [*asdict(incidents[0]), "detection_s", "mttr_s"])
            w.writeheader()
            w.writerows({**asdict(i), "detection_s": i.detection_s, "mttr_s": i.mttr_s} for i in incidents)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles: same fix as chaos/chaos.py
    sys.exit(main())
