# 2026-11-13 — Fake stack smoke run (Week 1–3 verification)

**Setup:** 3 × `fakes/fake_app.py` + `fakes/stub_gateway.py` (`RATE_LIMIT_RPS=60`,
health check every 1 s) + Prometheus 3.1.0 scraping every 5 s. Scenarios at
`SCENARIO_SCALE=0.2` (each ~36 s instead of 3 min). Run natively (no Docker), same code.

> These are fake servers on one laptop — numbers check that the pipeline works,
> not real performance. Real baselines come in Week 5.

## Locust results

| Scenario | Requests | Failures | p50 | p99 | Notes |
|---|---|---|---|---|---|
| normal | 613 | 0 (0.00%) | 26 ms | 38 ms | calm baseline |
| spike | 2082 | 1071 (51.4%) — **all** `rejected:429:rate_limited` | 37 ms | 110 ms | accepted requests stayed fast |
| failure | 706 | 7 (0.99%) — `error:503` from app-2 | 34 ms | 51 ms | other servers kept answering |

## Failure timeline (events.jsonl)

```
14:59:46.300  chaos    server_killed             app-2
14:59:46.476  gateway  backend_marked_unhealthy  app-2   <- detected in 0.18 s
15:00:01.536  gateway  backend_marked_healthy    app-2   <- back after restart + 3 s warm-up
```

- Detection time: **0.18 s** (first run: 0.73 s) — depends on where the 1 s health-check tick lands
- Time to healthy: 15.2 s = 12 s "dead" (scaled 60 s) + 3 s warm-up + check delay
- The 7 errors are requests sent to app-2 in the gap before detection.
  Week 4–5 ideas to shrink this: retry on 5xx in the gateway, faster health checks.

## Dashboard check

All 18 PromQL queries in `resiliguard-overview.json` returned data from the live Prometheus.
`min_over_time(gateway_backend_healthy[3m])` hit 0 for app-2 (red bar),
`increase(gateway_rejected_total{reason="rate_limited"}[10m])` ≈ 1144 (spike), peak ≈ 74 req/s.
