# Self-Healing Platform (ResiliGuard)

> A local system that keeps a website alive under traffic spikes and server failures —
> it detects broken servers, routes around them, restarts them, and explains what happened.

**Status:** 🚧 Week 0 — repo setup and API contracts

---

## What it does

- **Protects** app servers from traffic spikes (rate limiting, priority queue, 429/503)
- **Detects** failed servers with health checks and removes them from rotation
- **Recovers** by restarting containers and slowly sending traffic back
- **Falls back** to Emergency Mode when recovery keeps failing
- **Predicts** trouble early with anomaly detection (z-score → Isolation Forest)
- **Explains** incidents with an LLM assistant that can only pick from a safe, pre-approved action list
- **Measures** everything: the same failure scenarios run in 3 modes and are compared with numbers

## Architecture

```
[Locust load] ──▶ [Gateway: load balancing + rate limit + priority queue]
                        │
              ┌─────────┼─────────┐
           [App 1]   [App 2]   [App 3]        ← Docker Compose
                        │
[Chaos Injector] ── kill / delay / CPU stress
                        │
              [Prometheus] ──▶ [Grafana]
                        │
              [Controller + AI] ── detect → decide → act → report
```

A full diagram will live in [`docs/architecture.md`](docs/architecture.md).

## Experiment: 3 modes

| Metric | Mode A — No protection | Mode B — Rule-based | Mode C — AI-assisted |
|---|---|---|---|
| MTTR (mean time to recovery) | – | – | – |
| Error rate | – | – | – |
| p99 latency | – | – | – |
| Critical request success rate | – | – | – |
| False positive rate (AI only) | – | – | – |

Results will be filled in Week 11–12. See [`experiments/`](experiments/).

## Tech stack

| Area | Tools |
|---|---|
| Backend / Gateway | Python 3.12, FastAPI |
| Containers | Docker, Docker Compose |
| Monitoring | Prometheus, Grafana |
| Load testing | Locust |
| ML | scikit-learn, pandas, NumPy |
| LLM (optional) | Claude / OpenAI API or Ollama (local) |
| CI | GitHub Actions |

## Repo layout

```
app/            FastAPI app servers (/health, /ready, /metrics)       — Person 1
chaos/          Chaos scripts (kill / delay / CPU stress)              — Person 1
controller/     Self-healing controller (detect → restart → return)   — Person 1
gateway/        Custom gateway (round robin, health check, rate limit) — Person 2
monitoring/     Prometheus + Grafana config                            — Person 3
loadtest/       Locust scenarios                                       — Person 3
ai/             Anomaly detection + LLM incident assistant             — Person 3
experiments/    MTTR calculator, mode A/B/C runs, results              — Person 3
docs/           Contracts, decisions, architecture, team plan          — Everyone
tests/          Automated tests (run in CI)                            — Everyone
```

## Quick start

> Works once Week 1–2 is done. Until then, only the docs exist.

```bash
git clone https://github.com/AndrewOh077/Software-project.git
cd Software-project
cp .env.example .env          # fill in your own values, never commit .env
docker compose up --build
```

| Service | URL |
|---|---|
| Gateway | http://localhost:8000 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |
| Locust | http://localhost:8089 |

## Team

| Role | Owns | Name |
|---|---|---|
| Person 1 | App servers, recovery, chaos, controller | _TBD_ |
| Person 2 | Gateway, rate limiting, circuit breaker | _TBD_ |
| Person 3 | Monitoring, load tests, experiments, AI | _TBD_ |

How we work together: [`CONTRIBUTING.md`](CONTRIBUTING.md)

## Safety note

All tests run only against our own local Docker containers. We never send load to anyone else's servers.
