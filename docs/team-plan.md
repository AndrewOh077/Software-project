# Team Plan (3 people, same row = same time)

| Week | Person 1 — App servers + recovery | Person 2 — Gateway | Person 3 — Monitoring + tests + AI |
|---|---|---|---|
| 0 | **Together:** agree on 5 contracts in `docs/contracts.md`, create repo | ← | ← |
| 1–2 | 3 app servers + Docker Compose, `/health` `/ready` `/metrics` | Gateway skeleton + round robin (test with fake servers) | Fake server + Locust, Prometheus + Grafana connected |
| 3 | Chaos script (kill servers on purpose) | Health check + exclude dead servers, `/admin` API | 3 Locust scenarios, gateway graphs in Grafana |
| 3 (Fri) | **Integration #1:** kill one server, the others keep answering | ← | ← |
| 4 | Controller v1: detect → restart → return | Token bucket + priority + queue (429 / 503) | MTTR calculator (built on fake logs first) |
| 5 | Restart limit + Emergency Mode | AIMD + circuit breaker | Baseline runs: Mode A (no protection) vs Mode B (rules) |
| 6 | **Together:** integration week — pass all 8 scenarios (no new features) | ← | ← |
| 7 | GitHub Actions chaos test | Bug fixes, measure throughput limit | Finish dashboard, save test results |
| 8–9 | Connect "early warning" to controller | AI control API + Safety Controller | z-score → Isolation Forest, measure false positives |
| 10 | Incident log collector (optional) | Admin approval flow (optional) | LLM root-cause + postmortem (optional) |
| 11–12 | CI + "can a stranger reproduce it?" check | README + architecture diagram | Final 3-mode experiment + result graphs |
| 12 | **Together:** demo video | ← | ← |

## The 8 test scenarios

| # | Situation | System must |
|---|---|---|
| 1 | Sudden traffic spike | Apply rate limiting |
| 2 | Server B killed | Remove B, send traffic to A and C |
| 3 | 2 servers killed at once | Lower the limit to fit remaining capacity |
| 4 | Restart keeps failing | Switch to Emergency Mode |
| 5 | Server slows down | Detect overload, adjust limit |
| 6 | Server healthy again | Check readiness, return traffic gradually |
| 7 | All app servers down | Return 503 + alert |
| 8 | AI stops working | Rule-based controller keeps protecting |
