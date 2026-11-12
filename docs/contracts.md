# API Contracts ("response shapes")

**Week 0 job: all three of us agree on this file before writing code.**
After that, each person can build their part using fake data that has exactly these shapes,
and the parts will fit together in the integration week.

> Status: ✏️ DRAFT — change anything during the Week 0 meeting, then mark as ✅ AGREED.

---

## Contract 1 — App server: normal response

`GET /` (or any work endpoint, e.g. `GET /work`) on each app server.

```json
{
  "server_id": "app-2",
  "status": "ok",
  "data": "hello",
  "processed_ms": 12.4
}
```

| Field | Type | Meaning |
|---|---|---|
| `server_id` | string | Which server answered (`app-1`, `app-2`, `app-3`) — proves load balancing works |
| `status` | `"ok"` \| `"error"` | Result |
| `data` | any | The actual payload |
| `processed_ms` | number | Time the server spent on this request |

Header the gateway may send: `X-Priority: critical | normal` (default `normal`).

---

## Contract 2 — App server: `GET /health` (liveness: "am I alive?")

```json
{ "server_id": "app-2", "status": "alive" }
```

- `200` = process is alive.
- No answer / timeout / `5xx` = dead → gateway counts a failure.

## Contract 3 — App server: `GET /ready` (readiness: "can I take traffic?")

```json
{ "server_id": "app-2", "ready": true, "reason": null }
```

```json
{ "server_id": "app-2", "ready": false, "reason": "warming_up" }
```

- `200` when `ready: true`, `503` when `ready: false`.
- Allowed `reason` values: `null`, `"warming_up"`, `"overloaded"`, `"shutting_down"`.

> Why two endpoints? A server can be **alive** but not **ready** (just restarted, still loading).
> The gateway only sends traffic to servers that are ready.

---

## Contract 4 — Metrics: `GET /metrics` (Prometheus text format)

Every service exposes the same metric **names**, so Grafana panels and the AI read one format.

| Metric name | Type | Labels | Who exposes |
|---|---|---|---|
| `http_requests_total` | counter | `service`, `server_id`, `status_code`, `priority` | app, gateway |
| `http_request_duration_seconds` | histogram | `service`, `server_id` | app, gateway |
| `app_in_flight_requests` | gauge | `server_id` | app |
| `gateway_backend_healthy` | gauge (0/1) | `server_id` | gateway |
| `gateway_rejected_total` | counter | `reason` (`rate_limited`, `queue_full`, `no_backend`, `circuit_open`) | gateway |
| `gateway_queue_length` | gauge | `priority` | gateway |
| `gateway_rate_limit_current` | gauge | – | gateway |
| `controller_restarts_total` | counter | `server_id` | controller |
| `controller_emergency_mode` | gauge (0/1) | – | controller |

Useful PromQL (for Grafana):

```promql
# requests per second
sum(rate(http_requests_total{service="gateway"}[1m]))

# error rate
sum(rate(http_requests_total{service="gateway",status_code=~"5.."}[1m]))
  / sum(rate(http_requests_total{service="gateway"}[1m]))

# p99 latency
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{service="gateway"}[1m])) by (le))
```

---

## Contract 5 — Gateway: error responses + admin API

### 5a. Rejections (what the user gets when the gateway says no)

```json
{
  "error": "rate_limited",
  "message": "Too many requests, try again shortly",
  "retry_after_sec": 1
}
```

| HTTP code | `error` value | When |
|---|---|---|
| `429` | `rate_limited` | Token bucket is empty |
| `503` | `queue_full` | Waiting line is full |
| `503` | `no_backend` | No ready app servers |
| `503` | `circuit_open` | Circuit breaker is open |

Header: `Retry-After: <seconds>`.

### 5b. `GET /admin/status`

```json
{
  "mode": "B",
  "emergency_mode": false,
  "rate_limit_current": 50,
  "backends": [
    { "server_id": "app-1", "url": "http://app-1:8001", "healthy": true,  "ready": true,  "consecutive_failures": 0, "circuit": "closed" },
    { "server_id": "app-2", "url": "http://app-2:8002", "healthy": false, "ready": false, "consecutive_failures": 3, "circuit": "open" }
  ],
  "queue": { "critical": 0, "normal": 12 }
}
```

### 5c. Admin actions (used by controller and, later, the AI Safety Controller)

`POST /admin/action`

```json
{ "action": "set_rate_limit", "target": null, "value": 30, "requested_by": "controller", "reason": "error rate 12%" }
```

Response:

```json
{ "accepted": true, "action_id": "a-0042", "message": "rate limit set to 30" }
```

Allowed `action` values (the AI may ONLY pick from this list):

| action | target | value |
|---|---|---|
| `remove_backend` | server_id | – |
| `add_backend` | server_id | – |
| `restart_backend` | server_id | – |
| `set_rate_limit` | – | number |
| `set_emergency_mode` | – | true / false |

`requested_by`: `"controller"` \| `"ai"` \| `"human"`.

---

## Incident log line (shared by controller, chaos, AI)

One JSON object per line in `experiments/results/raw/events.jsonl` — Person 3's MTTR calculator reads this.

```json
{ "ts": "2026-11-20T15:04:05.123Z", "source": "chaos", "event": "server_killed", "server_id": "app-2", "detail": {} }
```

| `event` | Written by |
|---|---|
| `server_killed`, `latency_injected`, `cpu_stress_started` | chaos |
| `backend_marked_unhealthy`, `backend_marked_healthy` | gateway |
| `restart_started`, `restart_succeeded`, `restart_failed`, `emergency_mode_on`, `emergency_mode_off` | controller |
| `anomaly_detected`, `action_suggested` | ai |

**MTTR** = time from `server_killed` → matching `backend_marked_healthy`.

---

## Change log

| Date | Change | Approved by |
|---|---|---|
| | Initial draft | |
