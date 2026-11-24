# Final experiment: 3 protection modes, same scenarios

A = no protection (controller off, gateway limits off) · B = rules (controller + gateway limits) · C = rules + AI early warning.
Real services on a 2-CPU test machine, 3-minute scenarios, 2026-11-23. Latency is only comparable within this table.

| Scenario | Mode | Requests | Errors | Rejected on purpose | p99 | Critical OK | MTTR |
|---|---|---|---|---|---|---|---|
| Normal traffic | A, no protection | 3246 | 0.0% | 0.0% | 320 ms | 100.0% | – |
| Normal traffic | B, rules | 3248 | 0.0% | 0.0% | 320 ms | 100.0% | – |
| Normal traffic | C, rules + AI | 3245 | 0.0% | 0.0% | 320 ms | 100.0% | – |
| Traffic spike (scenario 1) | A, no protection | 6602 | 0.0% | 0.0% | 2200 ms | 100.0% | – |
| Traffic spike (scenario 1) | B, rules | 10095 | 0.0% | 48.1% | 1400 ms | 100.0% | – |
| Traffic spike (scenario 1) | C, rules + AI | 10217 | 0.0% | 49.6% | 920 ms | 100.0% | – |
| app2 killed (scenario 2) | A, no protection | 3570 | 6.6% | 0.0% | 420 ms | 93.8% | never (no controller) |
| app2 killed (scenario 2) | B, rules | 3570 | 0.6% | 0.9% | 330 ms | 98.6% | 14.1 s |
| app2 killed (scenario 2) | C, rules + AI | 3570 | 0.0% | 0.0% | 310 ms | 100.0% | 16.2 s |
| app3 fails 30%, still 'healthy' | A, no protection | 3570 | 3.5% | 0.0% | 310 ms | 95.2% | – |
| app3 fails 30%, still 'healthy' | B, rules | 3570 | 2.7% | 9.0% | 330 ms | 94.5% | – |
| app3 fails 30%, still 'healthy' | C, rules + AI | 3570 | 1.8% | 6.9% | 320 ms | 97.7% | – |
| app3 +800 ms (scenario 5) | A, no protection | 3553 | 0.0% | 0.0% | 1100 ms | 100.0% | – |
| app3 +800 ms (scenario 5) | B, rules | 3570 | 0.0% | 0.0% | 1100 ms | 100.0% | – |
| app3 +800 ms (scenario 5) | C, rules + AI | 3570 | 0.0% | 0.0% | 1100 ms | 100.0% | – |
