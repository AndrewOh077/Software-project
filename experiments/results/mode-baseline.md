# Week 5 baseline: Mode A (no protection) vs Mode B (rules)

Fake stack, 2026-11-21. Same Locust scenarios; only the gateway's health checks change.

| Scenario | Mode | Requests | Failed | p99 | Critical ok | MTTR |
|---|---|---|---|---|---|---|
| normal | Mode A, no protection | 3260 | 0.0% | 42 ms | 100.0% | – |
| normal | Mode B, rules | 3260 | 0.0% | 46 ms | 100.0% | – |
| spike | Mode A, no protection | 10263 | 0.0% | 180 ms | 100.0% | – |
| spike | Mode B, rules | 10257 | 0.0% | 190 ms | 100.0% | – |
| failure | Mode A, no protection | 3590 | 11.2% | 50 ms | 90.2% | n/a (never removed) |
| failure | Mode B, rules | 3590 | 0.2% | 48 ms | 99.7% | 63.8 s |

## What changed (B compared with A)

- **normal**: failed 0.0% → 0.0%, critical ok 100.0% → 100.0%.
- **spike**: failed 0.0% → 0.0%, critical ok 100.0% → 100.0%.
- **failure**: failed 11.2% → 0.2%, critical ok 90.2% → 99.7%.
