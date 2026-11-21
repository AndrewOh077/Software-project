# Person 3 guide — monitoring, load tests, baselines, AI (Week 1–9)

What exists, how to run it, and how it connects to everyone else's work.

## The whole flow, in order

```bash
# once: Python tools on your machine (the AI and the result scripts run outside Docker)
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt

make up          # 1. start fake apps, stub gateway, Prometheus, Grafana (http://localhost:3000)
make results     # 2. ~25 min: Week 5 baseline + AI training/scoring + all result files
make ai-watch    # 3. optional, live: the AI watches while you run any scenario in another tab
make down
```

`make results` runs `experiments/run_all.sh`, which does these steps in order, so nothing has
to be remembered or run by hand:

| Step | What runs | What it leaves behind |
|---|---|---|
| Week 5, Mode A | gateway health checks **off**; normal, spike, failure | `raw/modeA-*_stats.csv` |
| Week 5, Mode B | gateway health checks **on**; the same 3 scenarios | `raw/modeB-*_stats.csv`, `raw/modeB-*_metrics.csv` |
| Week 8 train | AI learns "normal" from the Mode B normal run | `ai/models/detector.pkl` |
| Week 8 score | sick run (app-3 fails 30% quietly), then score spike, failure, sick | `raw/*_eval.json` |
| Week 7 save | comparison table, AI report, MTTR CSV | `mode-baseline.md`, `ai-report.html`, `raw/mttr.csv` |

Quick rehearsal (~8 min): `SCENARIO_SCALE=0.3 make results`. No Docker? `NATIVE=1 make results`
(needs a `prometheus` binary; see the top of `experiments/run_all.sh`).

## What was built

| Timeline item | Where | Status |
|---|---|---|
| Week 1–2: fake server | `fakes/fake_app.py` | ✅ follows Contracts 1–4, has a chaos switch |
| Week 1–2: Locust | `loadtest/common.py` | ✅ 1 user ≈ 1 req/s, 10% critical traffic |
| Week 1–2: Prometheus + Grafana connected | `monitoring/` | ✅ auto-provisioned, dashboard loads on start |
| Week 3: 3 Locust scenarios | `loadtest/scenario_{normal,spike,failure}.py` | ✅ |
| Week 3: gateway graphs | `monitoring/grafana/build_dashboard.py` | ✅ 10 gateway panels |
| (extra) stub gateway | `fakes/stub_gateway.py` | ✅ test double until Person 2's gateway is ready |
| (extra) incident log | `fakes/events.py` → `events.jsonl` | ✅ ready for the Week 4 MTTR calculator |
| Week 4: MTTR calculator | `experiments/mttr.py` | ✅ `make mttr` |
| Week 5: Mode A vs Mode B baseline | `experiments/compare_modes.py`, `HEALTH_CHECKS` switch | ✅ `make results`, `make compare` |
| Week 7: dashboard finished, results saved | AI row in Grafana, `experiments/run_all.sh` | ✅ |
| Week 8–9: AI early warning | `ai/` | ✅ `make ai-normal`, `make ai-eval`, `make ai-watch` |

## Run it (needs Docker Desktop)

```bash
make up          # fake apps + stub gateway + Prometheus + Grafana
open http://localhost:3000       # dashboard "ResiliGuard — Overview"
make normal      # scenario 1, 3 minutes
make spike       # scenario 2 — try with a limit: make down; RATE_LIMIT_RPS=60 make up
make failure     # scenario 3 — watch app-2 turn red at 1:00 and green after 2:00
make down
```

Quick version (each scenario ~36 s instead of 3 min): `SCENARIO_SCALE=0.2 make failure`

Results land in `experiments/results/raw/` (git-ignored):
- `<scenario>_stats.csv` — Locust summary (requests, failures, p50…p99)
- `events.jsonl` — `server_killed`, `backend_marked_unhealthy`, `backend_marked_healthy`

Without Docker (what CI and the verification run used):

```bash
pip install -r requirements-dev.txt
SERVER_ID=app-1 uvicorn fakes.fake_app:app --port 8001 &   # repeat for app-2/8002, app-3/8003
BACKENDS="app-1=http://localhost:8001,app-2=http://localhost:8002,app-3=http://localhost:8003" \
  uvicorn fakes.stub_gateway:app --port 8000 &
cd loadtest && CHAOS_TARGET=http://localhost:8002 SCENARIO_SCALE=0.2 \
  locust -f scenario_failure.py --headless --host http://localhost:8000
```

## The three scenarios

| # | File | Shape | What it proves |
|---|---|---|---|
| 1 | `scenario_normal.py` | 20 users for 3 min | Baseline: what "calm" looks like |
| 2 | `scenario_spike.py` | 10 → **150** → 10 users | Protection: extra traffic is refused fast (429) instead of slowing everyone |
| 3 | `scenario_failure.py` | 20 users, app-2 killed at 60 s, back at 120 s | Self-healing: the other servers keep answering |

Timelines are plain tables in `loadtest/stages.py`, so they're easy to read and change.

## How failures are counted

| Locust label | Meaning | Counts as |
|---|---|---|
| `rejected:429:rate_limited` | gateway refused **on purpose** (body has Contract 5a `error`) | protection working |
| `rejected:503:no_backend` | no server was ready | protection working, but bad |
| `error:503`, `error:500` | an app server failed | real error |

Keeping these apart matters for the Week 5 Mode A vs B comparison: a 429 in 2 ms is a *good*
outcome during a spike; a timeout is not.

## Swapping in the real services (Integration #1, Week 3 Friday)

Nothing in `monitoring/` or `loadtest/` changes. Only the services behind the names do:

1. Person 1's real app servers replace `app-1..3` (same name, port 8000, Contracts 1–4).
2. Person 2's real gateway replaces `gateway` (same name, port 8000, Contracts 4–5).
3. Move the `prometheus`, `grafana`, `locust` blocks from `docker-compose.dev.yml` into the main
   `docker-compose.yml`.
4. For `make failure`: set `CHAOS_TARGET=""` and run Person 1's chaos script on the same timer
   (kill at 60 s, restart at 120 s) — real apps don't have `/chaos`.

Checklist for real services to "plug in":
- [ ] `GET /metrics` uses the exact names/labels in `docs/contracts.md` (Contract 4)
- [ ] gateway exposes `gateway_backend_healthy{server_id}` and `gateway_rejected_total{reason}`
- [ ] gateway writes `backend_marked_unhealthy/healthy` lines to `events.jsonl`
- [ ] 429/503 bodies have the Contract 5a `error` field

## Changing the dashboard

1. Edit `monitoring/grafana/build_dashboard.py` (each panel = one function call with its PromQL)
2. `make dashboard` → rewrites the JSON
3. `make test` — fails if the JSON is stale or a query uses a metric not in the contract

## Week 4: MTTR calculator

`experiments/mttr.py` reads `events.jsonl` and, for every `server_killed`, finds the next
`backend_marked_unhealthy` and `backend_marked_healthy` for the **same** server.

```
server_killed ──► backend_marked_unhealthy ──► backend_marked_healthy
     t0                    t1                          t2
detection time = t1 - t0        MTTR = t2 - t0   (docs/contracts.md definition)
```

```bash
make failure     # produces events.jsonl
make mttr        # prints the report + saves experiments/results/raw/mttr.csv
python experiments/mttr.py experiments/sample_data/events_sample.jsonl   # fake log demo
```

Example output (real run, 2026-11-13):

```
 #  server   killed at (UTC)          detected      MTTR
 1  app-2    2026-11-13 14:59:46.300     0.18s    15.24s
```

Rules it follows: events before a kill are ignored (startup noise), other servers don't count,
broken lines are skipped and reported, a server killed again before recovering leaves the first
incident as NOT RECOVERED (excluded from the mean, shown in the count).

## Week 5: Mode A (no protection) vs Mode B (rules)

Same three scenarios twice. The only change is the gateway's health checks:

- **Mode A** (`HEALTH_CHECKS=off`): the gateway never checks `/ready`, so a dead server keeps
  getting a third of the traffic until it comes back by itself.
- **Mode B** (`HEALTH_CHECKS=on`, the normal setting): 3 failed checks in a row take a server
  out of rotation; a good `/ready` puts it back.

`make results` runs both and writes `experiments/results/mode-baseline.md`. To rebuild the
table from runs you already have: `make compare`. Decision and numbers: D-012.

When Person 1's controller and Person 2's real gateway (AIMD, circuit breaker) land, run the
same command again: Mode B then means *their* rules, and the table shows what they add.

## Week 7: dashboard finished, results saved

- Grafana has a third row, **AI early warning**: the AI's verdict per server (OK / SICK) and
  how far from normal each server is, with the alarm line at 3. It fills while `make ai-watch`
  runs (`ai_anomaly_flag`, `ai_anomaly_zscore` in Contract 4).
- Every result file is produced by one command (`make results`) and listed in
  `experiments/README.md`.

## Week 8–9: AI early warning

The new scenario `make sick` makes app-3 fail 30% of requests while `/ready` still says yes.
The gateway keeps sending it traffic, so users see errors and the health check never notices.

1. `make ai-normal`: learn "normal" from a clean run.
2. `make ai-eval`: run the sick scenario, score both detectors, open `experiments/results/raw/ai-report-sick.html`.
   (`make results` does steps 1–2 for you and builds the full report with all three runs.)
3. `make ai-watch`: the live version for Person 1's controller to read (`anomaly_detected` in `events.jsonl`).

Details and results: `ai/README.md`. Decision: D-011.
