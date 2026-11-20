# AI early warning (Week 8–9)

**Owner:** Person 3

Health checks ask a server "are you ready?". A server can say "yes" and still fail 30% of
requests. This folder watches the **numbers** instead and logs `anomaly_detected` when a
server looks sick. The AI only reports; it never acts (actions are Contract 5c, Week 10).

| File | What it does |
|---|---|
| `anomaly.py` | pull metrics from Prometheus, z-score + Isolation Forest, evaluate, live watch |
| `report.py` + `report.css` | turn evaluation results into one HTML page: a tab per run, error and latency charts with alerts marked, a readout under the pointer (arrow keys work too), works with JavaScript off |
| `requirements.txt` | numpy, scikit-learn |

## Run it (after `make up`)

```bash
make ai-normal   # 3 min normal traffic -> learn "normal" -> ai/models/detector.pkl
make ai-eval     # 3 min with app-3 secretly failing 30% -> scores + experiments/results/ai-report.html
make ai-watch    # live: prints and logs anomaly_detected while you run any scenario
```

## Results (fake stack, 2026-11-20)

| Run | Detector (2 in a row) | False alarms | Caught | AI noticed | Health check noticed |
|---|---|---|---|---|---|
| Traffic spike, no fault | z-score | 0 | – | – | – |
| Traffic spike, no fault | Isolation Forest | 1 | – | – | – |
| app-2 killed | both | 0 | 0/1 | never | 0.1 s |
| app-3 fails 30%, says "ready" | both | 0 | 1/1 | 14 s | never |

Full table and chart: `experiments/results/2026-11-20-ai-early-warning.html`.
Why these choices: `docs/decisions.md` D-011.
