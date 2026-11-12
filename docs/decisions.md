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
