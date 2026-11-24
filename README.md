# Self-Healing Platform — Person 1 (Week 1–12)

A system that, when a server dies, **notices automatically, takes it out of traffic, brings it back up, and returns it gradually**.

## Follow along from scratch (5 minutes)

You need: Docker Desktop, Python 3.10 or newer, Git

```powershell
git clone https://github.com/AndrewOh077/Software-project.git
cd Software-project
docker compose up -d --build                  # takes a few minutes the first time
python -m unittest discover -s tests/unit     # 24 ok
docker kill app2                              # kill one server
```

After 20 seconds, if http://localhost:9100/status shows app2 back at `HEALTHY` with `restarts_total` at 1, it worked.
http://localhost:9100/incidents records how many seconds that outage took.

**Measured results** (Windows laptop, Docker Desktop):

| Item | Value |
|---|---|
| Failure detection | about 3 s (checked every second, 3 consecutive failures) |
| Single server recovery (DOWN → HEALTHY) | about 13.5 s (improved from 17 s initially) |
| Unit tests | 24 |
| Scenario tests | 13 (including 3 Gateway tests and 1 incident-record test) |

## What's in each folder

| Folder/file | What it does | Week |
|---|---|---|
| `app/` | Web server code. The same code runs three servers (app1–3) | 1–2 |
| `docker-compose.yml` | Config that starts everything at once | 1–2 |
| `chaos/chaos.py` | Tool that breaks servers on purpose | 3 |
| `scripts/traffic.py` | Tool that keeps sending requests to the servers | 3 |
| `controller/` | **Auto-recovery manager**. Checks servers every second and revives broken ones | 4–5 |
| `gateway/` | **The real Gateway**. Distributes requests and handles Controller admin commands | 1–9 |
| `tests/unit/` | 24 tests for the Controller decision logic (no Docker needed) | 4–10 |
| `tests/e2e/` | **13 automated scenario tests** (really kill servers and check) | 6–10 |
| `scripts/fresh_clone_check.*` | Checks in one go that a fresh clone builds and runs | 11–12 |
| `mock_gateway/` | Fake Gateway for development (no longer used now that the real Gateway exists) | 4 |
| `.github/workflows/ci.yml` | Runs the tests above automatically on GitHub | 7 |
| `docs/` | Docs: how the Controller works, scenario table, API contract between teammates | – |
| `prometheus/`, `grafana/`, `monitoring/` | Monitoring: metrics collection + dashboards (Person 3) | 1–3, 7 |
| `loadtest/` | Locust load tests: normal / spike / failure (Person 3) | 1–3 |
| `experiments/` | MTTR calculation, three-mode comparison experiment, results (Person 3) | 4–5, 11–12 |
| `ai/` | AI early warning + incident postmortem reports (Person 3) | 8–10 |
| `site/` | Project website: failure replay + results (Person 3, GitHub Pages) | 12 |
| `data/` | Where log files accumulate (`events.jsonl`, `incidents.jsonl`, `chaos.jsonl`, `faults/`) | – |

## Running (Windows)

Start Docker Desktop, open a terminal in this folder, and run:

```powershell
docker compose up -d --build
```

| URL | Contents |
|---|---|
| http://localhost:8001 | app1 (success if you see `{"server":"app1"...}`) |
| http://localhost:9100/status | **Server status as seen by the Controller** |
| http://localhost:8080/admin/backends | Gateway's server disabled/enabled/weight state |
| http://localhost:8080/work?ms=50 | User work request through the Gateway |
| http://localhost:3000 | Grafana dashboard |

Shut down: `docker compose down`

## Week 4 check: does a killed server come back on its own?

Open two terminals.

```powershell
# Terminal 1: watch what the controller is doing in real time
docker compose logs -f controller

# Terminal 2: kill app2
python chaos/chaos.py kill app2
python chaos/chaos.py status
```

If terminal 1 shows this sequence, it worked (about 10 s):

```
HEALTHY → SUSPECT → DOWN        no answer 3 times in a row → judged failed, removed from Gateway
DOWN → RESTARTING               docker restart app2
RESTARTING → WARMING            back alive, traffic 10% → 25% → 50% → 100%
WARMING → HEALTHY               recovery complete (recovery_s = time taken)
```

Also try `pause` (freeze) and `notready` (alive but can't do work).
For `notready`, it is expected that it waits 15 seconds instead of restarting right away (reason in `docs/controller.md`).

## Week 5 check: restart limit + emergency mode

**Emergency mode**: kill all three servers

```powershell
docker kill app1 app2 app3
```

→ The log shows `EMERGENCY MODE ON` → once everything is back, `Emergency mode off` 10 seconds later.
Also check at http://localhost:8080/admin/backends that `"mode"` changes from `emergency` → `normal`.

**Restart limit**: keep killing the same server within 2 minutes

```powershell
python chaos/chaos.py kill app3     # once it's back, again
python chaos/chaos.py kill app3     # once it's back, again
python chaos/chaos.py kill app3     # once it's back, again
python chaos/chaos.py kill app3
```

→ On the 4th time it stops restarting: `QUARANTINED` + alert + emergency mode.
A human releases it after "fixing" it (on a healthy server this only clears the restart history):

```powershell
curl.exe -X POST localhost:9100/admin/release/app3
```

## Week 6: automated tests for the 8 scenarios

Automatically checks the 8 "situations to actually test" from the project plan. The stack must be running.

```powershell
python -m unittest discover -s tests/e2e -v
```

Takes 5–8 minutes. If it ends with `OK`, it passed.
To also run the 3 Gateway tests (s1, s3b, s7b), set `$env:REAL_GATEWAY="1"` first. Full table in `docs/scenarios.md`.

New failure type (for scenario 4):
```powershell
python chaos/chaos.py break app2   # a failure that restarting does not fix
python chaos/chaos.py fix app2     # as if a human fixed it
```

## Week 7: GitHub Actions

`.github/workflows/ci.yml` is included, so on GitHub **every push or PR automatically**
① runs the unit tests → ② starts everything with Docker and runs the scenario tests.
See the results in the repository's **Actions** tab. Green ✓ = passed, red ✗ = something broke.
If it fails, download `chaos-run-logs` at the bottom of that run's page to see the logs.

## Week 8: early-warning hookup

When Person 3's anomaly detection sends "this server looks like it's about to have trouble", the Controller
**cuts that server's traffic to 50% for 30 seconds.** It never restarts it (the AI could be wrong).

Try sending one yourself:
```powershell
curl.exe -X POST localhost:9100/warning -H "Content-Type: application/json" -d '{\"server\": \"app1\", \"score\": 0.9, \"reason\": \"test\"}'
```
→ At http://localhost:8080/admin/backends, app1's `weight` becomes 50, then returns to 100 after 30 seconds.
→ At http://localhost:9100/status, app1 shows `"warned": true`

Usage for Person 3 is in the "Early warning" section of `docs/controller.md`.

## Week 10: incident records

For each incident it writes one line with **start time, end time, duration, which servers, number of restarts, whether emergency mode was on, and what happened in between**.
Even if all three servers die at once, it is recorded as a single incident, and it only closes once every server is healthy again.

```powershell
docker kill app2
# after 20 seconds
curl.exe localhost:9100/incidents
```

```
{"id": "INC-1795236720-1", "started": ..., "ended": ..., "duration_s": 13.5,
 "reason": "app2: not alive", "servers": ["app2"], "restarts": 1, "emergency": false,
 "timeline": [ {"type": "server_down", ...}, {"type": "restart", ...}, {"type": "recovered", ...} ]}
```

- File: `data/incidents.jsonl` (one incident per line). Person 3 uses it for MTTR calculation and AI evaluation.
- Metrics: `controller_incident_open` (1 if an incident is in progress), `controller_incidents_total`
- The in-progress incident appears under `"incident"` in `localhost:9100/status`.

## Week 11–12: does it work from scratch? (reproducibility check)

As if freshly downloaded from GitHub: clone into a temp folder → build → unit tests → scenario tests (including the real Gateway),
all in one go, then clean up. **It stops the running stack** (the container names are the same).

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\fresh_clone_check.ps1
powershell -ExecutionPolicy Bypass -File scripts\fresh_clone_check.ps1 -Branch person1-week10-12
```

```bash
# Mac / Linux
./scripts/fresh_clone_check.sh
```

If the last line is `OK: fresh clone of main builds and passes all tests`, it passed.
Besides push and PR, GitHub Actions also **rebuilds from scratch once every Monday** and runs the tests.
This catches breakage from Docker image or package updates even when the code hasn't changed.

## Unit tests (no Docker needed)

```powershell
python -m unittest discover -s tests/unit -v
```

Passes if all 24 are `ok`. Always run them after changing Controller code.

## Changing settings

Add them under the controller's `environment:` in `docker-compose.yml`. For example:

| Setting | Default | Meaning |
|---|---|---|
| `FAIL_THRESHOLD` | 3 | How many consecutive failures count as a failure |
| `MAX_RESTARTS` | 3 | How many restarts are allowed ... |
| `RESTART_WINDOW_S` | 120 | ... within how many seconds |
| `RAMP_WEIGHTS` | 10,25,50,100 | Traffic steps when returning a server |
| `NOT_READY_GRACE_S` | 15 | How many seconds to wait for a server that is alive but can't do work |
| `EXIT_STABLE_S` | 10 | How many seconds things must be stable before emergency mode turns off |
| `READY_TIMEOUT_S` | 15 | How many seconds a server has to come back after a restart |
| `WARN_WEIGHT` | 50 | Traffic share for a warned server |
| `WARN_DURATION_S` | 30 | How many seconds a warning lasts |

# Person 2 — Gateway (Week 1–10)

The Gateway is the **single entry point** that every user request passes through (`gateway/gateway.py`, FastAPI).
It distributes requests to app1–3, protects the system from overload, and removes or restores servers as the Controller instructs.

> Key design point: the Gateway **never removes servers on its own.** Deciding whether a server has failed is the Controller's job;
> the Gateway only follows the `disable / enable / weight / mode` commands the Controller sends.

## What the Gateway does

| Feature | What it does |
|---|---|
| Weighted round robin | Forwards `/work` requests in turn to enabled servers in proportion to weight, and returns the server's actual response as-is |
| Failover | If a server can't be reached or times out, tries each other server once more. If all fail, `503` |
| Admin API | The interface the Controller uses to disable/enable servers, adjust weights, and change mode |
| Rate limiting | Token bucket + priority (`critical`/`normal`/`low`). Over the limit: `429` + `Retry-After: 1` |
| AIMD concurrency limit | Automatically adjusts the cap on concurrent requests to match server capacity. Over the limit: `429` |
| Emergency mode | When `mode` is `emergency`, only `critical` requests are accepted; the rest get `503` |
| Error handling | See the table below |

### Request priority

`/work?priority=critical`, `/work?priority=normal` (default), `/work?priority=low`

- **critical**: Skips the rate limit and concurrency limit, and passes even in emergency mode.
- **normal**: Passes if a token is available.
- **low**: Rejected first once tokens drop below 50% of bucket capacity (`LOW_PRIORITY_RESERVE`).

### How AIMD works

- Cap = `CONCURRENCY_PER_BACKEND` × (sum of enabled servers' weights ÷ 100). When a server is removed, the cap shrinks **immediately**.
- When a server comes back, the limit grows **slowly** by `AIMD_INCREASE_PER_S` per second (Additive Increase).
- If a response is 5xx or a server can't be reached, the current limit is multiplied by `AIMD_DECREASE_FACTOR` to **cut it sharply** (Multiplicative Decrease).

### Error handling

| Situation | Response |
|---|---|
| Over the limit (token bucket) | `429` `rate limit exceeded` |
| Over the concurrency limit (AIMD) | `429` `concurrency limit reached` |
| Non-`critical` request in emergency mode | `503` `emergency mode accepts critical requests only` |
| No enabled servers, or all connections failed | `503` `no reachable enabled backend` |
| Admin command with an unknown server name | `404` |
| weight is not an integer 0–100 | `400` |
| Server responds with 5xx | That response is passed through as-is (not retried on another server) |

## Endpoints

| Path | Description |
|---|---|
| `GET /work` | User request. `?priority=` and other query params are passed to the server as-is |
| `GET /route` | Shows the name and address of the next server to be picked (for debugging) |
| `GET /health` | Whether the Gateway itself is alive |
| `GET /admin/backends` | Each server's `enabled`/`weight` and the current `mode` |
| `POST /admin/backends/{name}/disable` | Remove a server from distribution |
| `POST /admin/backends/{name}/enable` | Include the server again |
| `POST /admin/backends/{name}/weight` | Set traffic share (`{"weight": 0~100}`) |
| `POST /admin/mode` | `{"mode": "normal"}` or `{"mode": "emergency"}` |
| `GET /admin/calls` | Log of the last 200 commands sent by the Controller |
| `GET /metrics` | Current mode, number of enabled servers, concurrency limit/cap/in-flight requests |

## Try it

```powershell
docker compose up -d --build
curl.exe "http://localhost:8080/work?ms=50"
curl.exe "http://localhost:8080/work?ms=50&priority=critical"
curl.exe http://localhost:8080/admin/backends
curl.exe http://localhost:8080/metrics
```

Send commands directly, without the Controller:

```powershell
curl.exe -X POST http://localhost:8080/admin/backends/app2/disable
curl.exe -X POST http://localhost:8080/admin/backends/app2/weight -H "Content-Type: application/json" -d '{\"weight\": 50}'
curl.exe -X POST http://localhost:8080/admin/backends/app2/enable
```

## Gateway settings

Add them under `environment:` for the `gateway` service in `docker-compose.yml`.

| Setting | Default | Meaning |
|---|---|---|
| `REQUEST_TIMEOUT` | 3 | How many seconds to wait for a server response |
| `APP1_URL`, `APP2_URL`, `APP3_URL` | `http://appN:8000` | Address of each server |
| `RATE_LIMIT_RPS` | 50 | Allowed requests per second (token refill rate) |
| `RATE_LIMIT_BURST` | 100 | Max requests accepted at once (bucket size) |
| `LOW_PRIORITY_RESERVE` | 0.5 | `low` requests are rejected when tokens are below this fraction of the bucket |
| `CONCURRENCY_PER_BACKEND` | 30 | Concurrent requests per server |
| `AIMD_INCREASE_PER_S` | 5 | How much the limit recovers per second |
| `AIMD_DECREASE_FACTOR` | 0.5 | Multiplier applied to the limit on failure |

## Tests

```powershell
python -m unittest discover -s tests/e2e -v
```

## Person 3: monitoring, load tests, AI, final experiment

Details in `docs/person3.md`. Everything in one go:

```powershell
docker compose up -d --build
pip install -r requirements-p3.txt
python experiments/run_all.py        # about 25 minutes
```

→ `experiments/results/report.html` (results page), `three-mode.md` (table), `postmortems/` (incident reports).

Website (replay of real failures): `site/index.html` → https://andrewoh077.github.io/Software-project/

Final results (real services, same failures run three times, `docs/experiments.md`):

| | No protection | Rules | Rules + AI |
|---|---|---|---|
| Errors users saw during failures | 3.3% | 0.7% | **0.6%** |
| Full recovery of a dead server | never | 15.0 s | 16.2 s |
| Detecting an "alive but sick" server | no | no | **20 s** |
