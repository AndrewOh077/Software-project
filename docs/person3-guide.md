# Person 3 guide — monitoring, load tests (Week 1–3)

What exists, how to run it, and how it connects to everyone else's work.

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

## Next for Person 3 (Week 4–5)

- MTTR calculator: read `events.jsonl`, pair `server_killed` → next `backend_marked_healthy`
  for the same `server_id` (sample data already produced by `make failure`)
- Mode A vs Mode B baseline runs with these same 3 scenarios
