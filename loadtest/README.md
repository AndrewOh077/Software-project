# Load tests

**Owner:** Person 3

| File | What |
|---|---|
| `common.py` | The fake user: ~1 req/s, 90% normal / 10% critical, separates rejections from errors |
| `stages.py` | Scenario timelines as plain tables (users over time) |
| `scenario_normal.py` | 1 — steady 20 users |
| `scenario_spike.py` | 2 — 10 → 150 → 10 users |
| `scenario_failure.py` | 3 — kill app-2 at 60 s, restore at 120 s, logs `server_killed` |

Run with `make normal | make spike | make failure`. `SCENARIO_SCALE=0.2` makes them 5x shorter.
See [`docs/person3-guide.md`](../docs/person3-guide.md).
