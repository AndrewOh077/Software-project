# Monitoring

**Owner:** Person 3

| Path | What |
|---|---|
| `prometheus/prometheus.yml` | Scrapes `gateway:8000` and `app-1..3:8000` every 5 s |
| `grafana/provisioning/` | Auto-connects Grafana to Prometheus and loads dashboards on start |
| `grafana/build_dashboard.py` | Dashboard as code — edit panels here, then `make dashboard` |
| `grafana/dashboards/resiliguard-overview.json` | Generated dashboard (17 panels: gateway + app servers) |

See [`docs/person3-guide.md`](../docs/person3-guide.md).
