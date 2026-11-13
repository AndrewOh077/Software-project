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
