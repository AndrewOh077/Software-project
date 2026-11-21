# Experiments

**Owner:** Person 3

One command makes every result here: `make up`, then `make results` (about 25 min).

| Path | What |
|---|---|
| `run_all.sh` | Runs everything in order: Week 5 Mode A/B, AI training and scoring, then the result files (`make results`) |
| `compare_modes.py` | Week 5: Mode A (no protection) vs Mode B (rules) table from Locust CSVs (`make compare`) |
| `mttr.py` | Week 4: `events.jsonl` → detection time + MTTR per incident (`make mttr`) |
| `sample_data/events_sample.jsonl` | Hand-written fake log with tricky cases (startup noise, other servers, broken line, never-recovered) |

## Saved results (committed)

| File | What it shows |
|---|---|
| `results/mode-baseline.md` | Week 5: failed requests, p99, critical success, MTTR per mode and scenario |
| `results/ai-report.html` | Week 8–9: can the AI spot a sick server the health check misses? Open in a browser |
| `results/2026-11-13-fake-stack-smoke.md` | Week 1–3: first run of the three scenarios |

`results/raw/` holds the raw Locust CSVs, `events.jsonl`, metrics and evaluation JSON
(git-ignored, rebuilt by `make results`).

Next: Week 11–12, the final 3-mode experiment (A, B, and B + AI) on the real services.
