# Decision Log

Every time we choose one option over another, write 2–3 lines here.
Interview questions like "why token bucket?" are answered directly from this file.

Format:

```
## D-00X — <title>  (YYYY-MM-DD, who)
**Chose:** ...
**Instead of:** ...
**Why:** ...
```

---

## D-001 — Build our own gateway instead of configuring Nginx  (2026-11, team)
**Chose:** Custom FastAPI gateway.
**Instead of:** Nginx / HAProxy config.
**Why:** Writing load balancing, health checks, rate limiting and priority handling ourselves shows we understand how they work, and gives us full control for the experiment modes.

## D-002 — AI is not on the request path  (2026-11, team)
**Chose:** AI only detects and suggests; fixed code performs actions.
**Instead of:** Letting an LLM route or approve every request.
**Why:** LLMs are slow and non-deterministic. The system must keep working even if the AI is down.

## D-003 — Grafana instead of a custom React dashboard  (2026-11, team)
**Chose:** Grafana.
**Instead of:** Building a React dashboard.
**Why:** Saves weeks; that time goes to the AI and experiments, which are the unique part.

## D-004 — Local Docker Compose, cloud optional  (2026-11, team)
**Chose:** Run everything locally with Docker Compose.
**Instead of:** Deploying to AWS/GCP from the start.
**Why:** C$0 cost, anyone can reproduce it from the README.

## D-005 — Build monitoring against fakes first  (2026-11-13, Person 3)
**Chose:** Fake app servers + a stub gateway that speak the exact contracts.
**Instead of:** Waiting for the real app servers and gateway.
**Why:** All three people work in parallel (timeline Week 1–3). Because everything depends only on the contract names, swapping in the real services changes nothing in Prometheus, Grafana or Locust.

## D-006 — Grafana dashboard as code  (2026-11-13, Person 3)
**Chose:** A Python script generates the dashboard JSON; a test checks it is up to date.
**Instead of:** Clicking panels in the Grafana UI and exporting.
**Why:** Every PromQL query is reviewable in a PR, and a test fails if a panel uses a metric name that is not in docs/contracts.md.

## D-007 — 1 Locust user ≈ 1 request per second  (2026-11-13, Person 3)
**Chose:** `constant_pacing(1)` for every user.
**Instead of:** Random waits between requests.
**Why:** "150 users" then means "about 150 req/s", which makes scenarios and graphs easy to compare and explain.

## D-008 — Count on-purpose rejections separately from errors  (2026-11-13, Person 3)
**Chose:** A 429/503 is a "rejection" only if its body has the Contract 5a `error` field; anything else is an error.
**Instead of:** Treating every non-200 as a failure.
**Why:** During a spike, a fast 429 means protection is working. Mixing it with crashes would make Mode B look worse than Mode A.

## D-009 — Readiness is not liveness  (2026-11-13, Person 3)
**Chose:** In the stub gateway, "warming up" or "overloaded" pauses traffic but does not count as a failure or write an incident event.
**Instead of:** Treating every non-ready answer as dead.
**Why:** Otherwise every restart or startup pollutes events.jsonl and makes the MTTR numbers wrong.

## D-010 — MTTR is measured from the event log, not from Grafana  (2026-11-14, Person 3)
**Chose:** Compute MTTR from `events.jsonl` timestamps (`server_killed` → `backend_marked_healthy`).
**Instead of:** Reading it off Prometheus graphs.
**Why:** Prometheus only samples every 5 s, so MTTR from graphs is off by up to 5 s. Event timestamps are exact to the millisecond, and the same file works for every mode (A/B/C) and every run.

## D-011 — AI watches error ratio and p95 latency, trained on a clean run  (2026-11-20, Person 3)
**Chose:** Two numbers per server every 5 s (5xx share, p95 latency). Both detectors learn "normal" from one normal-scenario run only. Default live detector: z-score, alert after 2 bad samples in a row.
**Instead of:** Request rate (a traffic spike is not a sick server), or Isolation Forest as the default.
**Why:** On real fake-stack runs both detectors caught the hidden 30%-error fault in 14 s with 0 false alarms, but Isolation Forest still raised 1 false alarm during the spike. With only two features the forest adds nothing; revisit when we add more features. Kills stay the health check's job (0.1 s).

**Update 2026-11-20:** the second full run found one more false alarm: at the very end of a spike
run, app-2 answered a handful of requests *faster* than normal (p95 24 ms vs 45 ms, z = −4) and
both detectors flagged it. Both are now one-sided: only more errors or slower counts. Re-scored on
the same saved data: 0 false alarms in all runs, sick server caught in 15 s, health check never.

## D-012 — Week 5 baseline: Mode B = health checks only, rate limit off in both  (2026-11-20, Person 3)
**Chose:** Mode A = stub gateway with `HEALTH_CHECKS=off`; Mode B = health checks on. Rate limiting off in both modes. Same three scenarios, full length, one command (`make results`).
**Instead of:** Turning the rate limiter on in Mode B.
**Why:** The fake app servers have no capacity limit, so a rate limit could only reject requests the servers would have handled; it would make Mode B look worse for a reason that isn't real. Result (fake stack): the failure scenario is where the modes differ: failed 11.2% → 0.2%, critical requests OK 90.2% → 99.7%. Normal and spike: 0% failed in both. MTTR in Mode B is 63.8 s because the scenario keeps app-2 dead for 60 s on purpose; a faster restart by Person 1's controller is what shrinks it. Re-run when the real gateway (AIMD, circuit breaker) and controller land.
