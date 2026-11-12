# Architecture

> Final diagram is made in Week 11–12 (Person 2). This is the working draft.

```mermaid
flowchart LR
    L[Locust load] --> G[Gateway<br/>round robin · health check<br/>rate limit · priority queue]
    G --> A1[App 1]
    G --> A2[App 2]
    G --> A3[App 3]
    C[Chaos Injector] -.kill / delay / CPU.-> A2
    A1 & A2 & A3 & G --> P[Prometheus]
    P --> GR[Grafana]
    P --> AI[AI: anomaly detection<br/>+ LLM assistant]
    AI -->|suggest| S[Safety Controller]
    S -->|allowed actions only| CT[Self-Healing Controller]
    CT -->|restart / remove / set limit| G
    CT -->|docker restart| A2
```

## Components

| Component | Folder | Owner | Job |
|---|---|---|---|
| Gateway | `gateway/` | P2 | Front door: picks a server, rejects extra traffic |
| App servers | `app/` | P1 | Do the actual work |
| Controller | `controller/` | P1 | Finds broken servers and fixes them |
| Chaos | `chaos/` | P1 | Breaks things on purpose to test recovery |
| Monitoring | `monitoring/` | P3 | Collects and shows numbers |
| Load test | `loadtest/` | P3 | Fake users |
| AI | `ai/` | P3 | Early warning + explanations |
