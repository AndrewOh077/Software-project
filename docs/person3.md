# Person 3 — Monitoring, Load Testing, MTTR, AI, Final Experiment

The role of **showing with numbers that the system Person 1 (servers·Controller) and Person 2 (Gateway) built really holds up**.
Every tool attaches to the team's real services (app1–3, gateway, controller).

## Everything in one go (Windows PowerShell)

```powershell
docker compose up -d --build              # full stack (Grafana: http://localhost:3000)
pip install -r requirements-p3.txt        # first time only: Locust, scikit-learn, etc.
python experiments/run_all.py             # about 25 minutes: 3 modes x 5 situations + AI + report
```

Each scenario runs 90 seconds. To run at the original length (3 minutes, about 55 minutes total), set `$env:SCENARIO_SCALE="1"`.
To only regenerate results: `python experiments/run_all.py --results-only`.

When it finishes you get:

| File | Contents |
|---|---|
| `experiments/results/report.html` | **Results page** (open in a browser): 3-mode comparison + AI results |
| `experiments/results/three-mode.md` | The same comparison as a table |
| `experiments/results/postmortems/*.md` | Postmortem report written automatically for each incident (Week 10) |
| `experiments/results/raw/` | Raw numbers (not uploaded to GitHub) |
| `site/` | **Project website**: replay of real failures + results table. Open `site/index.html` in a browser |

To rebuild only the website: `python experiments/build_site.py` (needs the experiment results in `raw/`).
When pushed to main, GitHub Actions (`.github/workflows/pages.yml`) automatically publishes it to
https://andrewoh077.github.io/Software-project/. The first time only, the repository admin (Andrew) must
change Settings → Pages → Source to "GitHub Actions".

## What was built each week

| Week | File | What it does | Run on its own |
|---|---|---|---|
| 1–3 | `prometheus/prometheus.yml`, `monitoring/gateway_exporter.py` | Prometheus scrapes app·controller·gateway·AI metrics every 2 seconds | `docker compose up -d` |
| 1–3, 7 | `scripts/gen_dashboard.py` → `grafana/.../overview.json` | Adds Gateway and AI rows to the Grafana dashboard | `python scripts/gen_dashboard.py` |
| 1–3 | `loadtest/` | Creates fake users with Locust: normal, spike, and 3 failure types | See "Load testing" below |
| 4 | `experiments/mttr.py` | Time from failure → detection → recovery (MTTR) | `python experiments/mttr.py` |
| 5, 11–12 | `experiments/run_all.py`, `experiments/compare.py` | No protection / rules / rules+AI comparison experiment | `python experiments/run_all.py` |
| 8–9 | `ai/anomaly.py` | Anomaly detection (z-score, Isolation Forest) → early warning to the Controller | See "AI" below |
| 10 | `ai/incident.py` | Root-cause estimation + postmortem reports (LLM optional) | `python -m ai.incident` |
| 11–12 | `ai/report.py` | Results page (charts) | Run automatically by `run_all.py` |

💡 **Why is a Gateway exporter needed?** The Gateway's `/metrics` is JSON, which Prometheus can't read.
Since the e2e tests use that JSON, we leave the Gateway code alone and run a small separate program
alongside it that converts it to Prometheus text format.

## Load testing (Locust)

All requests go to the real Gateway (`localhost:8080/work`). 1 user ≈ 1 request per second.
10% are `critical` requests (e.g. payments), which the Gateway never blocks.

```powershell
$env:SCENARIO="normal"   # normal | spike | kill | errors | slow
python -m locust -f loadtest/scenario.py --headless --host http://localhost:8080
```

| SCENARIO | Situation |
|---|---|
| `normal` | 20 users, normal load |
| `spike` | 10 → 150 users for 1 minute (scenario 1: rate limiting) |
| `kill` | app2 dies at 60 s, turned back on at 120 s (scenario 2) |
| `errors` | app3 returns 30% errors from 60–120 s while still reporting "healthy" (only the AI can see it) |
| `slow` | app3 +800 ms from 60–120 s (scenario 5) |

Failures are injected with Person 1's `chaos/chaos.py`. That way the ground truth is recorded
automatically in `data/chaos.jsonl`, which MTTR and AI scoring use.

## AI early warning (Week 8–9)

```powershell
python -m ai.anomaly collect --minutes 4 --out experiments/results/raw/normal.csv   # after 3 minutes of normal load
python -m ai.anomaly train experiments/results/raw/normal.csv                       # learns what "normal" is
python -m ai.anomaly watch                                                         # live monitoring
```

- Every 5 seconds it looks at each server's **error rate** and **p95 response time**.
- If it's more than 3x off from normal (z > 3), twice in a row, it judges the server "sick".
- Then it sends `POST /warning` to the Controller. The Controller cuts that server's traffic to 50% for 30 seconds,
  and if it's still sick the AI resends every 30 seconds.
- **The AI only warns. It never restarts anything.** If the AI is off, the rules (Controller) keep working (scenario 8).
- The "AI" row at the bottom of Grafana shows the numbers the AI is looking at.

## Incident postmortems (Week 10)

```powershell
python -m ai.incident          # writes a report for every incident in data/
```

- Works without an LLM (rule-based estimation). To use an LLM, copy `.env.example` to `.env` and fill it in:
  Ollama (free, on your own laptop) or an OpenAI-compatible API.
- **Safeguard**: the LLM can only choose from an allowlist (`restart_server`, `lower_traffic`, `adjust_rate_limit`, `add_server`,
  `none`). Anything outside the list is discarded and logged. **It only suggests; it never executes.**
  Every decision is recorded in `data/ai.jsonl`.
- The estimator only sees symptoms (Controller logs, AI warnings, metrics). The true cause (chaos log) appears only at the very end of
  the report, for a "was it right?" check.

## Tests

```powershell
python -m pytest -q tests/p3          # 1 second, no Docker
```

The `person3` job in GitHub Actions checks these tests, linting, and whether the dashboard is up to date on every push/PR.
