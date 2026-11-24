# Person 3 — 모니터링, 부하 테스트, MTTR, AI, 최종 실험

Person 1(서버·Controller)과 Person 2(Gateway)가 만든 시스템이 **정말로 잘 버티는지 숫자로 보여주는**
역할이에요. 모든 도구는 팀의 진짜 서비스(app1~3, gateway, controller)에 붙어서 돌아가요.

## 한 번에 전부 (Windows PowerShell)

```powershell
docker compose up -d --build              # 전체 스택 (Grafana: http://localhost:3000)
pip install -r requirements-p3.txt        # 처음 한 번: Locust, scikit-learn 등
python experiments/run_all.py             # 약 25분: 3가지 모드 x 5가지 상황 + AI + 보고서
```

시나리오 하나가 90초예요. 원래 길이(3분, 약 55분)로 돌리려면 `$env:SCENARIO_SCALE="1"`.
결과만 다시 만들려면 `python experiments/run_all.py --results-only`.

끝나면 이게 생겨요:

| 파일 | 내용 |
|---|---|
| `experiments/results/report.html` | **결과 페이지** (브라우저로 열기): 3가지 모드 비교 + AI 결과 |
| `experiments/results/three-mode.md` | 같은 비교를 표로 |
| `experiments/results/postmortems/*.md` | 장애마다 자동으로 쓴 사후 분석 보고서 (Week 10) |
| `experiments/results/raw/` | 원본 숫자 (GitHub에는 안 올라감) |

## 주차별로 만든 것

| 주차 | 파일 | 하는 일 | 따로 돌리기 |
|---|---|---|---|
| 1–3 | `prometheus/prometheus.yml`, `monitoring/gateway_exporter.py` | Prometheus가 app·controller·gateway·AI 숫자를 2초마다 수집 | `docker compose up -d` |
| 1–3, 7 | `scripts/gen_dashboard.py` → `grafana/.../overview.json` | Grafana 대시보드에 Gateway 줄, AI 줄 추가 | `python scripts/gen_dashboard.py` |
| 1–3 | `loadtest/` | Locust로 가짜 사용자 만들기: 평소, 폭주, 고장 3가지 | 아래 "부하 테스트" |
| 4 | `experiments/mttr.py` | 고장 → 감지 → 복구까지 걸린 시간 (MTTR) | `python experiments/mttr.py` |
| 5, 11–12 | `experiments/run_all.py`, `experiments/compare.py` | 보호 없음 / 규칙 / 규칙+AI 비교 실험 | `python experiments/run_all.py` |
| 8–9 | `ai/anomaly.py` | 이상 감지 (z-score, Isolation Forest) → Controller에 조기 경고 | 아래 "AI" |
| 10 | `ai/incident.py` | 장애 원인 추정 + 사후 분석 보고서 (LLM 선택) | `python -m ai.incident` |
| 11–12 | `ai/report.py` | 결과 페이지 (그래프) | `run_all.py`가 자동으로 |

💡 **Gateway exporter가 왜 필요해요?** Gateway의 `/metrics`는 JSON이라서 Prometheus가 못 읽어요.
e2e 테스트가 그 JSON을 쓰니까 Gateway 코드는 그대로 두고, 옆에서 Prometheus 글자로 바꿔주는
작은 프로그램을 따로 띄워요.

## 부하 테스트 (Locust)

모든 요청은 진짜 Gateway(`localhost:8080/work`)로 가요. 사용자 1명 ≈ 초당 요청 1개.
10%는 `critical`(예: 결제) 요청이라서 Gateway가 절대 막지 않아요.

```powershell
$env:SCENARIO="normal"   # normal | spike | kill | errors | slow
python -m locust -f loadtest/scenario.py --headless --host http://localhost:8080
```

| SCENARIO | 상황 |
|---|---|
| `normal` | 20명, 평소 |
| `spike` | 10 → 150명 1분 동안 (시나리오 1: 요청 제한) |
| `kill` | 60초에 app2 죽음, 120초에 다시 켬 (시나리오 2) |
| `errors` | 60~120초 app3가 30% 에러, 그래도 "건강함"이라고 대답 (AI만 볼 수 있음) |
| `slow` | 60~120초 app3 +800 ms (시나리오 5) |

고장은 Person 1의 `chaos/chaos.py`로 내요. 그래서 `data/chaos.jsonl`에 정답이
자동으로 남고, MTTR과 AI 채점이 그걸 써요.

## AI 조기 경고 (Week 8–9)

```powershell
python -m ai.anomaly collect --minutes 4 --out experiments/results/raw/normal.csv   # 평소 3분 돌린 뒤
python -m ai.anomaly train experiments/results/raw/normal.csv                       # "평소"를 배움
python -m ai.anomaly watch                                                         # 실시간 감시
```

- 서버마다 5초에 한 번 **에러 비율**과 **p95 응답 시간**을 봐요.
- 평소보다 3배 이상 이상하면(z > 3), 그리고 두 번 연속이면 "아프다"고 판단해요.
- 판단하면 Controller에 `POST /warning`을 보내요. Controller가 그 서버 트래픽을 30초 동안 50%로 줄이고,
  아직 아프면 AI가 30초마다 다시 보내요.
- **AI는 경고만 해요. 재시작은 절대 안 해요.** AI가 꺼져도 규칙(Controller)은 그대로 동작해요 (시나리오 8).
- Grafana 맨 아래 "AI" 줄에서 AI가 보는 숫자가 보여요.

## 장애 사후 분석 (Week 10)

```powershell
python -m ai.incident          # data/ 의 모든 장애에 대해 보고서 작성
```

- LLM 없이도 돌아가요 (규칙으로 추정). LLM을 쓰려면 `.env.example`을 `.env`로 복사해서 채워요:
  Ollama(무료, 내 노트북) 또는 OpenAI 호환 API.
- **안전장치**: LLM은 허용 목록(`restart_server`, `lower_traffic`, `adjust_rate_limit`, `add_server`,
  `none`) 중에서만 고를 수 있어요. 목록 밖이면 버리고 기록해요. **실행은 안 하고 제안만 해요.**
  모든 판단은 `data/ai.jsonl`에 남아요.
- 추정하는 쪽은 증상(Controller 기록, AI 경고, 숫자)만 봐요. 진짜 원인(chaos 기록)은 보고서 맨 끝에
  "맞았나?" 확인용으로만 나와요.

## 테스트

```powershell
python -m pytest -q tests/p3          # Docker 없이 1초
```

GitHub Actions의 `person3` 작업이 push/PR마다 이 테스트, 코드 검사, 대시보드 최신 여부를 확인해요.
