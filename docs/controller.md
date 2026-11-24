# Controller Guide (Week 4–10)

## What each file does

| File | What it does |
|---|---|
| `core.py` | **The core brain.** Judges server state and decides what to do |
| `adapters.py` | Connects to the outside world: server checks (HTTP), `docker restart`, Gateway calls, alerts |
| `config.py` | Numeric settings (how many failures = failed? how many restarts allowed?) |
| `events.py` | Records what happened to `data/events.jsonl`, and each incident to `data/incidents.jsonl` (Week 10) |
| `api.py` | `localhost:9100`: `/status`, `/metrics`, `/events`, `/incidents`, `POST /warning`, `POST /admin/release/{server}` |
| `__main__.py` | Entry point: wires everything up and repeats `tick()` every second |

## States a server goes through

```
STARTING ──ready──▶ HEALTHY ◀────────────── WARMING (traffic 10→25→50→100%)
                     │  ▲                       ▲
           1 failure ▼  │ recovers              │ ready
                    SUSPECT                     │
                     │ 3 failures in a row      │
                     ▼                          │
                    DOWN ──restart──▶ RESTARTING ┘
                     │ ▲                 │ not alive within 30 s
                     │ └─────────────────┘ (wait grows 2→4→8 s)
                     │ still failing after 3 restarts in 2 minutes
                     ▼
                 QUARANTINED (stop restarting, alert, emergency mode)   ← Week 5
```

**Emergency mode (Week 5)**: ON when zero servers are working or any server is quarantined → tells the Gateway "only handle critical requests".
OFF once 2 or more servers have been healthy for 10 seconds straight.

## Why it's built this way (likely interview questions)

1. **Why separate `/health` and `/ready`?**
   `/health` failing = the process is dead → needs a restart.
   `/ready` failing = alive but busy → restarting would only reduce capacity further. Just pull traffic and wait 15 seconds.
   Same idea as Kubernetes liveness/readiness probes.

2. **Why not mark it failed after a single failure?**
   Brief network blips are common. Requiring 3 in a row reduces false alarms.
   It's a trade-off between detecting fast and avoiding false alarms.

3. **Why ramp traffic up gradually on return?**
   A freshly started server may still be slow, and if the cause wasn't fixed it will die again right away.
   Checking at 10% first keeps the damage small if it blows up again (slow start / canary).

4. **Why limit the number of restarts?** (Week 5)
   Things like code bugs aren't fixed by restarting. Infinite restarts (crash loop) waste resources.
   More than 3 in 2 minutes → stop and call a human. The retry interval also grows (exponential backoff).

5. **What if the Gateway dies?**
   Even if Gateway calls fail, server restarts continue. It remembers the instructions it sent and resends failed ones later.

6. **What if the Controller dies? (who watches the watcher?)**
   The Controller itself is revived by Docker's `restart: unless-stopped`.
   State is rebuilt by checking servers directly every second, so it's back to normal quickly after a restart.

7. **Why can't AI warnings trigger restarts?** (Week 8)
   ML can be wrong. All a warning can do is the reversible action "50% traffic for 30 seconds",
   so a false warning does little harm and reverts on its own. **The AI advises; the rules act.**
   And if the AI is off, rule-based recovery keeps working (scenario 8).

8. **Why not restart slow servers?** (scenario 5)
   Slow isn't broken, it's busy. Restarting a busy server leaves even fewer servers to do the work and makes things worse.
   Sending less traffic to slow servers is the job of the Gateway (Person 2) and anomaly detection (Person 3).

9. **Why no external packages at all?**
   The Controller has to be the most robust part of the system, so dependencies are kept to a minimum.
   And every outside connection (checks, docker, gateway, clock) is swappable,
   so tests run in 0.01 seconds without Docker (`tests/unit/test_core.py`).

## Early warning (Week 8) — to share with Person 3

When Person 3's anomaly detection (z-score, Isolation Forest) decides "this server looks like it's about to have trouble", call it like this.

```powershell
curl.exe -X POST localhost:9100/warning -H "Content-Type: application/json" `
  -d '{\"server\": \"app2\", \"score\": 0.93, \"reason\": \"p99 latency 3x baseline\", \"source\": \"zscore\"}'
```

```python
# From Python (to put in Person 3's code)
import json, urllib.request
req = urllib.request.Request("http://localhost:9100/warning", method="POST",
    data=json.dumps({"server": "app2", "score": 0.93, "reason": "p99 3x", "source": "zscore"}).encode(),
    headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req).read())
```

| Response | Meaning |
|---|---|
| 200 `{"accepted": true, "action": "reduce_weight", "weight": 50, "duration_s": 30}` | Accepted: 50% traffic for 30 seconds |
| 409 `{"accepted": false, ...}` | Already handling a failure, or unknown server name |
| 400 | `server` is missing |

- Warnings are recorded in `data/events.jsonl` as `"type": "warning"`. To later compute "how many seconds before the real failure did the warning arrive"
  and "how many false positives were there", compare against `data/chaos.jsonl` (when failures were actually injected).
- Settings: `WARN_WEIGHT` (default 50), `WARN_DURATION_S` (default 30)

## Operator command: release

```powershell
curl.exe -X POST localhost:9100/admin/release/app2
```
- Quarantined (QUARANTINED) server: clears its restart history and tries to revive it ("it's fixed, try again")
- Healthy server: only clears the restart history ("forget past failures"). Used when tests keep killing the same server

## Contract with the Gateway (to share with Person 2)

The Controller calls these 5 endpoints on the Gateway. If they differ from what was agreed in Week 0, change `HttpGateway` in `adapters.py`.

| Call | Meaning |
|---|---|
| `GET /admin/backends` | Get current state |
| `POST /admin/backends/{id}/disable` | Stop sending requests to this server |
| `POST /admin/backends/{id}/enable` | Send to it again |
| `POST /admin/backends/{id}/weight` body `{"weight": 0-100}` | Traffic share |
| `POST /admin/mode` body `{"mode": "normal" or "emergency"}` | Turn emergency mode on/off |

## Log files (to share with Person 3)

- `data/events.jsonl`: what the Controller did. E.g. `{"time": ..., "type": "recovered", "server": "app2", "recovery_s": 9.1}`
- `data/chaos.jsonl`: when and which failure was injected with the chaos tool (ground truth)
- Recovery time (MTTR) = from the failure time in chaos.jsonl to the `recovered` time in events.jsonl
- `data/incidents.jsonl` (Week 10): one line per incident. `started`, `ended`, `duration_s` (MTTR), `servers`, `restarts`, `emergency`, `timeline`.
  An incident opens when a server goes DOWN or emergency mode turns on, and closes when all servers are healthy and emergency mode is off.
  You can also see them at `localhost:9100/incidents`.
