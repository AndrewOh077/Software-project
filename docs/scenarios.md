# Week 6: The 8 Scenarios (integration week)

Automated tests built from section "4. Situations to actually test" in the project plan. File: `tests/e2e/test_scenarios.py`

```powershell
docker compose up -d --build
python -m unittest discover -s tests/e2e -v          # all (5–8 minutes)
python -m unittest discover -s tests/e2e -v -k s4    # just one
```

| # | Situation | What the system must do | Test | Owner | Status |
|---|---|---|---|---|---|
| 1 | Sudden user surge | Apply rate limiting | `test_s1_traffic_spike_is_rate_limited` | **Person 2** | Placeholder only (skip) |
| 2 | Server B shuts down | Remove B, distribute to A·C, return B | `test_s2_one_server_killed` | Person 1 | ✅ |
| 3 | Two servers shut down at once | Hold up with the remaining server + recover both | `test_s3_two_servers_killed` | Person 1 | ✅ |
| 3b | 〃 | Shrink the limit to match remaining capacity (AIMD) | `test_s3b_limit_shrinks_with_capacity` | **Person 2** | Placeholder only (skip) |
| 4 | Server recovery fails | Quarantine + Emergency after 3 restarts; recovers once a human releases it | `test_s4_recovery_fails_then_emergency` | Person 1 | ✅ |
| 5 | Server response slows down | Don't restart for slowness (detection/limiting is the Gateway/AI's job) | `test_s5_slow_server_is_not_restarted` | Person 1 (+2, 3) | ✅ |
| 6 | Server works normally again | Gradual return 10→25→50→100% after readiness check | `test_s6_gradual_return_after_recovery` | Person 1 | ✅ |
| 7 | All app servers down | Emergency + admin alert, cleared after everything recovers | `test_s7_all_servers_down` | Person 1 | ✅ |
| 7b | 〃 | Respond 503 to normal requests | `test_s7b_gateway_returns_503_for_normal_requests` | **Person 2** | Placeholder only (skip) |
| 8 | AI stops working | Recover with rules alone, without AI | `test_s8_recovery_works_without_ai` | Person 1 | ✅ |
| 9 | (Week 10) Incident record | Each incident is recorded from start to end, with its duration | `test_s9_incident_is_recorded_with_duration` | Person 1 | ✅ |

+ Week 8 early warning: 2 `EarlyWarning` class tests ✅
+ Person 3: measures scenarios 1·2·5 and the "sick but alive server" with load tests; the AI detects #5 (slowdown) in 10 seconds → `docs/experiments.md`

## For Person 2
- The 3 skipped tests (`s1`, `s3b`, `s7b`) have `TODO(Person 2)` notes describing what to check.
- Once the real Gateway is running, run with `REAL_GATEWAY=1` to un-skip them:
  ```powershell
  $env:REAL_GATEWAY="1"; python -m unittest discover -s tests/e2e -v
  ```
- The Gateway admin API defaults to `http://localhost:8080`; if it's different, change it with the `GATEWAY_ADMIN` environment variable.

## New failure type: `break` / `fix`
Scenario 4 needs "a failure that restarting doesn't fix", so we added it to the chaos tool.
```powershell
python chaos/chaos.py break app2   # as if a config file were corrupted: keeps dying even after restart
python chaos/chaos.py fix app2     # as if a human fixed it
```
While the file `data/faults/app2.broken` exists, app2's `/health` keeps returning 503.
