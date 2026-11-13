# Fakes (test doubles)

**Owner:** Person 3

Stand-ins so monitoring, dashboards and load tests can be built **before** the real services exist
(timeline Week 1–3: "가짜 서버로 시험"). Both follow `docs/contracts.md` exactly, so swapping in the
real app servers (Person 1) and the real gateway (Person 2) needs no change to Prometheus, Grafana
or Locust.

| File | Pretends to be | Contracts |
|---|---|---|
| `fake_app.py` | One app server | 1 (work), 2 (`/health`), 3 (`/ready`), 4 (`/metrics`) |
| `stub_gateway.py` | The gateway | 4 (gateway metrics), 5a (429/503 bodies), 5b (`/admin/status`) |
| `events.py` | Shared event logger | Incident log line (`events.jsonl`) |

## Chaos switch (fake app only)

```bash
curl -X POST localhost:8002/chaos/down                  # app-2 "dies": /health 503, work 503
curl -X POST "localhost:8002/chaos/slow?latency_ms=800" # every request +800 ms
curl -X POST "localhost:8002/chaos/error?error_rate=0.5" # 50% of requests return 500
curl -X POST localhost:8002/chaos/ok                    # back to normal (warms up first)
curl localhost:8002/chaos                               # current mode
```

The real chaos scripts (killing containers) are Person 1's job in `chaos/`. This switch is just the
fake-server version, so load tests can create failures without Docker access.
