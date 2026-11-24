# Self-Healing Platform: 3-Mode Experiment Results (Person 3, Week 11–12)

We ran the same traffic and the same failures **three times**, changing only the protection level. Results page:
`experiments/results/report.html` (open in a browser). Table: `experiments/results/three-mode.md`.

To rerun: `docker compose up -d --build` → `python experiments/run_all.py` (about 25 minutes).

## 1. The three modes

| Mode | Controller | Gateway | AI |
|---|---|---|---|
| **A: No protection** | Off (no restarts) | Rate limit and concurrency limit off (`AIMD_DECREASE_FACTOR=1`) | None |
| **B: Rules** | On (detect → remove → restart → return at 10·25·50·100%) | Default settings (50/s, 30 per server, AIMD) | None |
| **C: Rules + AI** | Same as B | Same as B | Warns the Controller when it sees a sick server (repeats every 30 s) |

## 2. Five situations (3 minutes each, 20 users; spike only goes 10 → 150 users)

`normal`, `spike` (scenario 1), `kill` (app2 dies at 60 s, scenario 2), `errors` (app3 returns 30% errors from 60–120 s
while still reporting "healthy"), `slow` (app3 +800 ms, scenario 5). Load: `loadtest/scenario.py`, failures: `chaos/chaos.py`.

## 3. Results (2026-11-23; B's kill/errors re-recorded on 2026-11-24; real services, 2-CPU test machine)

| Situation | Metric | A No protection | B Rules | C Rules + AI |
|---|---|---|---|---|
| app2 dies | Errors users saw | 6.6% | **0.0%** | **0.0%** |
| | Critical request success | 93.8% | **100%** | **100%** |
| | Full recovery (MTTR) | never (no Controller) | 15.0 s | 16.2 s |
| app3 30% errors | Errors users saw | 3.5% | 2.2% | **1.8%** |
| | Deliberately rejected (429) | 0% | 9.2% | 6.9% |
| | Critical request success | 95.2% | 96.1% | **97.7%** |
| Traffic spike | p99 response time | 2200 ms | 1400 ms | 920 ms |
| | Deliberately rejected (429) | 0% | 48.1% | 49.6% |
| | Requests handled | 6,602 | 10,095 | 10,217 |
| app3 slows down | p99 response time | 1100 ms | 1100 ms | 1100 ms |

Across the three failures combined, errors users saw: **A 3.3% → B 0.7% → C 0.6%**.

AI on its own (scored on Mode B logs, where the AI takes no action):

| Failure | Time for AI to notice | Controller health check | False alarms |
|---|---|---|---|
| app3 30% errors | 20 s | never noticed | 0 |
| app3 slows down | 10 s | never noticed | 0 |
| app2 dies | – (traffic stops, so no numbers to judge) | 1.0 s | 0 |

## 4. What we learned

1. **The rules catch dead servers.** The Controller notices within 1 second and recovers within 15 seconds. Without protection,
   errors continue until the server is turned back on.
2. **Only the AI sees "alive but sick" servers.** Even while returning 30% errors, `/health` is OK, so the Controller never
   finds out. The AI notices in 20 seconds, and the warned Controller cuts traffic, bringing errors from 2.2% → 1.8%.
3. **During a spike, the Gateway limits protect everything else.** By quickly rejecting nearly half with 429, p99 drops from 2.2 s → 0.9–1.4 s,
   more requests get handled, and not a single critical request is blocked.
4. **Slow servers still have room for improvement.** The AI notices in 10 seconds, but traffic is only cut to 50%, so the
   slowest 1% (p99) stays the same.

## 5. For teammates (findings from the experiment)

- **Person 2 (Gateway):** AIMD halves the concurrency limit **on every single failed response**. When one server
  returns 30% errors, even requests bound for healthy servers get rejected 9% of the time (B's errors row). Reducing per server,
  or batching failures over a short window before reducing, would likely help.
- **Person 1 (Controller):** 50% on warning isn't enough for "slow servers" (p99 unchanged). Consider cutting further
  based on the warning `score`. The AI now resends warnings every 30 seconds (matching `warn_duration_s`).

## 6. Limitations

- The test machine has 2 CPUs, so compare response-time numbers **only within this table** (a single request itself takes 65 ms).
- Each situation was run only once. Differences of 1% or less (e.g. kill: B 15.0 s vs C 16.2 s; running the same B twice gave 14.1 s and 15.0 s) may be chance.
- The app servers simulate work with `sleep`, so the CPU doesn't blow up under a spike. For a real service, A's spike would be worse.
