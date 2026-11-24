# Self-Healing Platform — Person 1 (Week 1–8)

서버가 죽으면 **자동으로 알아채고, 트래픽에서 빼고, 다시 살리고, 조금씩 복귀시키는** 시스템.

## 폴더에 뭐가 있나

| 폴더/파일 | 뭐 하는 거 | 주차 |
|---|---|---|
| `app/` | 웹 서버 코드. 같은 코드로 서버 3개(app1~3)를 띄움 | 1–2 |
| `docker-compose.yml` | 전부를 한 번에 켜는 설정 (출근표) | 1–2 |
| `chaos/chaos.py` | 서버를 일부러 망가뜨리는 도구 | 3 |
| `scripts/traffic.py` | 서버에 요청을 계속 보내는 도구 | 3 |
| `controller/` | **자동 복구 관리자**. 1초마다 서버를 검사하고 고장 나면 살림 | 4–5 |
| `gateway/` | **실제 Gateway**. 요청 분배와 Controller 관리 명령을 처리 | 1–9 |
| `tests/unit/` | Controller 판단 로직 테스트 17개 (Docker 필요 없음) | 4–8 |
| `tests/e2e/` | **시나리오 12개 자동 테스트** (진짜로 서버를 죽이고 확인) | 6 |
| `.github/workflows/ci.yml` | GitHub에 올리면 위 테스트를 자동으로 돌려줌 | 7 |
| `docs/` | 설명서: Controller 원리, 시나리오 표, 팀원과의 약속(API) | – |
| `prometheus/`, `grafana/`, `monitoring/` | 모니터링: 숫자 수집 + 대시보드 (Person 3) | 1–3, 7 |
| `loadtest/` | Locust 부하 테스트: 평소 / 폭주 / 고장 (Person 3) | 1–3 |
| `experiments/` | MTTR 계산, 3가지 모드 비교 실험, 결과 (Person 3) | 4–5, 11–12 |
| `ai/` | AI 조기 경고 + 장애 사후 분석 보고서 (Person 3) | 8–10 |
| `data/` | 기록 파일이 쌓이는 곳 (`events.jsonl`, `chaos.jsonl`, `faults/`) | – |

## 실행 (Windows)

Docker Desktop을 켜고, 이 폴더에서 터미널을 열어서:

```powershell
docker compose up -d --build
```

| 주소 | 내용 |
|---|---|
| http://localhost:8001 | app1 (`{"server":"app1"...}` 나오면 성공) |
| http://localhost:9100/status | **Controller가 보는 서버 상태** |
| http://localhost:8080/admin/backends | Gateway의 서버 제외/복귀/비율 상태 |
| http://localhost:8080/work?ms=50 | Gateway를 통한 사용자 작업 요청 |
| http://localhost:3000 | Grafana 대시보드 |

끄기: `docker compose down`

## Week 4 확인: 서버 죽이면 알아서 살아나나?

터미널 두 개를 열어요.

```powershell
# 터미널 1: controller가 뭘 하는지 실시간으로 보기
docker compose logs -f controller

# 터미널 2: app2 죽이기
python chaos/chaos.py kill app2
python chaos/chaos.py status
```

터미널 1에서 이런 순서가 보이면 성공이에요 (약 10초):

```
HEALTHY → SUSPECT → DOWN        3번 연속 대답 없음 → 고장 판정, Gateway에서 제외
DOWN → RESTARTING               docker restart app2
RESTARTING → WARMING            다시 살아남, 트래픽 10% → 25% → 50% → 100%
WARMING → HEALTHY               복구 완료 (recovery_s = 걸린 시간)
```

`pause`(얼리기), `notready`(살아 있지만 일 못 함)도 해보세요.
`notready`는 바로 재시작하지 않고 15초 기다리는 게 정상이에요 (이유는 `docs/controller.md`).

## Week 5 확인: 재시작 제한 + 비상 모드

**비상 모드**: 서버 3개를 다 죽이면

```powershell
docker kill app1 app2 app3
```

→ 로그에 `EMERGENCY MODE ON` → 다 살아나고 10초 뒤 `Emergency mode off`.
http://localhost:8080/admin/backends 에서 `"mode"`가 `emergency` → `normal`로 바뀌는 것도 확인.

**재시작 제한**: 같은 서버를 2분 안에 계속 죽이면

```powershell
python chaos/chaos.py kill app3     # 살아나면 또
python chaos/chaos.py kill app3     # 살아나면 또
python chaos/chaos.py kill app3     # 살아나면 또
python chaos/chaos.py kill app3
```

→ 4번째에는 더 이상 재시작하지 않고 `QUARANTINED`(격리) + 알림 + 비상 모드.
사람이 "고쳤다"고 풀어주기 (정상 서버에 쓰면 재시작 기록만 지워요):

```powershell
curl.exe -X POST localhost:9100/admin/release/app3
```

## Week 6: 시나리오 8개 자동 테스트

계획서의 "실제로 테스트할 상황" 8개를 자동으로 확인해요. 스택이 켜져 있어야 해요.

```powershell
python -m unittest discover -s tests/e2e -v
```

5~8분 걸려요. 마지막에 `OK`가 나오면 성공이에요.
skip 3개는 Person 2의 Gateway가 있어야 하는 테스트예요. 자세한 표는 `docs/scenarios.md`.

새로 생긴 장애 종류 (4번 시나리오용):
```powershell
python chaos/chaos.py break app2   # 재시작해도 안 고쳐지는 고장
python chaos/chaos.py fix app2     # 사람이 고친 것처럼
```

## Week 7: GitHub Actions

`.github/workflows/ci.yml`이 들어 있어서, GitHub에 올리면 **push나 PR마다 자동으로**
① 단위 테스트 → ② Docker로 전체를 띄우고 시나리오 테스트를 돌려요.
결과는 GitHub 저장소의 **Actions** 탭에서 봐요. 초록 ✓ = 통과, 빨간 ✗ = 뭔가 망가짐.
실패하면 그 실행 페이지 아래 `chaos-run-logs`를 받아서 로그를 볼 수 있어요.

## Week 8: 조기 경고 연결

Person 3의 이상 감지가 "이 서버 곧 문제 생길 것 같아"라고 보내면, Controller가
**30초 동안 그 서버의 트래픽을 50%로 줄여요.** 재시작은 절대 안 해요 (AI가 틀릴 수도 있으니까).

직접 보내보기:
```powershell
curl.exe -X POST localhost:9100/warning -H "Content-Type: application/json" -d '{\"server\": \"app1\", \"score\": 0.9, \"reason\": \"test\"}'
```
→ http://localhost:8080/admin/backends 에서 app1의 `weight`가 50이 됐다가 30초 뒤 100으로 돌아와요.
→ http://localhost:9100/status 에서 app1의 `"warned": true`

Person 3에게 줄 사용법은 `docs/controller.md`의 "조기 경고" 부분.

## 단위 테스트 (Docker 없이도 됨)

```powershell
python -m unittest discover -s tests/unit -v
```

17개 전부 `ok`면 통과. Controller 코드를 고친 다음엔 항상 돌려보세요.

## 설정 바꾸기

`docker-compose.yml`의 controller `environment:`에 추가하면 돼요. 예:

| 설정 | 기본값 | 의미 |
|---|---|---|
| `FAIL_THRESHOLD` | 3 | 몇 번 연속 실패하면 고장으로 볼지 |
| `MAX_RESTARTS` | 3 | 몇 번까지 재시작할지 ... |
| `RESTART_WINDOW_S` | 120 | ... 몇 초 안에 |
| `RAMP_WEIGHTS` | 10,25,50,100 | 복귀할 때 트래픽 단계 |
| `NOT_READY_GRACE_S` | 15 | 살아 있지만 일 못 하는 서버를 몇 초 기다려줄지 |
| `EXIT_STABLE_S` | 10 | 비상 모드 끄기 전에 몇 초 안정적이어야 하는지 |
| `READY_TIMEOUT_S` | 15 | 재시작 후 몇 초 안에 살아나야 하는지 |
| `WARN_WEIGHT` | 50 | 경고 받은 서버의 트래픽 비율 |
| `WARN_DURATION_S` | 30 | 경고 효과가 몇 초 갈지 |

# Person 2 — Gateway (Week 1–10)

Gateway는 모든 사용자 요청이 지나가는 **하나뿐인 입구**예요 (`gateway/gateway.py`, FastAPI).
요청을 app1~3에 나눠주고, 과부하로부터 시스템을 지키고, Controller의 명령대로 서버를 빼거나 되돌려요.

> 중요한 설계: Gateway는 **스스로 서버를 제외하지 않아요.** 서버가 고장 났는지 판단하는 건 Controller이고,
> Gateway는 Controller가 보내는 `disable / enable / weight / mode` 명령만 따라요.

## Gateway가 하는 일

| 기능 | 하는 일 |
|---|---|
| 가중치 Round robin | `/work` 요청을 켜져 있는(enabled) 서버에 weight 비율대로 돌아가며 전달하고, 서버의 실제 응답을 그대로 돌려줌 |
| Failover | 서버에 연결이 안 되거나 타임아웃이면 다른 서버로 한 번씩 더 시도. 다 실패하면 `503` |
| Admin API | Controller가 서버를 제외/복귀/비율 조절하고 모드를 바꾸는 창구 |
| Rate limiting | Token bucket + 우선순위(`critical`/`normal`/`low`). 한도 초과 시 `429` + `Retry-After: 1` |
| AIMD 동시 처리 제한 | 동시에 처리하는 요청 수의 상한을 서버 용량에 맞춰 자동 조절. 한도 초과 시 `429` |
| 비상 모드 | `mode`가 `emergency`이면 `critical` 요청만 받고 나머지는 `503` |
| 에러 처리 | 아래 표 참고 |

### 요청 우선순위

`/work?priority=critical`, `/work?priority=normal`(기본), `/work?priority=low`

- **critical**: Rate limit과 동시 처리 제한을 건너뛰고 비상 모드에서도 통과해요.
- **normal**: 토큰이 있으면 통과해요.
- **low**: 토큰이 버킷 용량의 50% 아래로 내려가면 먼저 거절돼요 (`LOW_PRIORITY_RESERVE`).

### AIMD가 동작하는 방식

- 상한 = `CONCURRENCY_PER_BACKEND` × (켜져 있는 서버들의 weight 합 ÷ 100). 서버가 빠지면 상한이 **즉시** 줄어요.
- 서버가 돌아오면 초당 `AIMD_INCREASE_PER_S`씩 **천천히** 늘어나요 (Additive Increase).
- 응답이 5xx이거나 서버에 못 닿으면 현재 한도에 `AIMD_DECREASE_FACTOR`를 곱해서 **확 줄여요** (Multiplicative Decrease).

### 에러 처리

| 상황 | 응답 |
|---|---|
| 한도 초과 (Token bucket) | `429` `rate limit exceeded` |
| 동시 처리 한도 초과 (AIMD) | `429` `concurrency limit reached` |
| 비상 모드에서 `critical`이 아닌 요청 | `503` `emergency mode accepts critical requests only` |
| 켜진 서버가 하나도 없거나 전부 연결 실패 | `503` `no reachable enabled backend` |
| 없는 서버 이름으로 admin 명령 | `404` |
| weight가 0–100 정수가 아님 | `400` |
| 서버가 5xx로 응답 | 그 응답을 그대로 전달 (다른 서버로 재시도하지 않음) |

## 엔드포인트

| 주소 | 설명 |
|---|---|
| `GET /work` | 사용자 요청. `?priority=`와 나머지 쿼리는 서버로 그대로 전달 |
| `GET /route` | 다음에 선택될 서버 이름과 주소 확인 (디버깅용) |
| `GET /health` | Gateway 자신이 살아 있는지 |
| `GET /admin/backends` | 서버별 `enabled`/`weight`와 현재 `mode` |
| `POST /admin/backends/{name}/disable` | 서버를 분배에서 제외 |
| `POST /admin/backends/{name}/enable` | 서버를 다시 포함 |
| `POST /admin/backends/{name}/weight` | 트래픽 비율 설정 (`{"weight": 0~100}`) |
| `POST /admin/mode` | `{"mode": "normal"}` 또는 `{"mode": "emergency"}` |
| `GET /admin/calls` | Controller가 보낸 최근 명령 200개 기록 |
| `GET /metrics` | 현재 mode, 켜진 서버 수, 동시 처리 한도/상한/진행 중 요청 수 |

## 실행해 보기

```powershell
docker compose up -d --build
curl.exe "http://localhost:8080/work?ms=50"
curl.exe "http://localhost:8080/work?ms=50&priority=critical"
curl.exe http://localhost:8080/admin/backends
curl.exe http://localhost:8080/metrics
```

Controller 없이 직접 명령해 보기:

```powershell
curl.exe -X POST http://localhost:8080/admin/backends/app2/disable
curl.exe -X POST http://localhost:8080/admin/backends/app2/weight -H "Content-Type: application/json" -d '{\"weight\": 50}'
curl.exe -X POST http://localhost:8080/admin/backends/app2/enable
```

## Gateway 설정

`docker-compose.yml`의 `gateway` 서비스에 `environment:`로 추가하면 돼요.

| 설정 | 기본값 | 의미 |
|---|---|---|
| `REQUEST_TIMEOUT` | 3 | 서버 응답을 몇 초까지 기다릴지 |
| `APP1_URL`, `APP2_URL`, `APP3_URL` | `http://appN:8000` | 각 서버 주소 |
| `RATE_LIMIT_RPS` | 50 | 초당 허용 요청 수 (토큰이 차오르는 속도) |
| `RATE_LIMIT_BURST` | 100 | 한꺼번에 받을 수 있는 최대 요청 수 (버킷 크기) |
| `LOW_PRIORITY_RESERVE` | 0.5 | `low` 요청은 토큰이 버킷의 이 비율 아래면 거절 |
| `CONCURRENCY_PER_BACKEND` | 30 | 서버 1대당 동시에 처리할 요청 수 |
| `AIMD_INCREASE_PER_S` | 5 | 한도가 초당 얼마씩 회복되는지 |
| `AIMD_DECREASE_FACTOR` | 0.5 | 실패 시 한도에 곱하는 값 |

## 테스트

```powershell
python -m unittest discover -s tests/e2e -v
```

## Person 3: 모니터링, 부하 테스트, AI, 최종 실험

자세한 설명은 `docs/person3.md`. 한 번에 전부:

```powershell
docker compose up -d --build
pip install -r requirements-p3.txt
python experiments/run_all.py        # 약 60분. 빨리: $env:SCENARIO_SCALE="0.3"
```

→ `experiments/results/report.html`(결과 페이지), `three-mode.md`(표), `postmortems/`(장애 보고서).

<!-- results-summary -->

