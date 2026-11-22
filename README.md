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
| `mock_gateway/` | 가짜 Gateway. Person 2의 진짜 Gateway가 나오면 삭제 | 4 |
| `tests/unit/` | Controller 판단 로직 테스트 17개 (Docker 필요 없음) | 4–8 |
| `tests/e2e/` | **시나리오 8개 자동 테스트** (진짜로 서버를 죽이고 확인) | 6 |
| `.github/workflows/ci.yml` | GitHub에 올리면 위 테스트를 자동으로 돌려줌 | 7 |
| `docs/` | 설명서: Controller 원리, 시나리오 표, 팀원과의 약속(API) | – |
| `prometheus/`, `grafana/` | 모니터링 (Person 3 담당) | – |
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
| http://localhost:9000/admin/backends | Gateway(가짜)가 받은 지시 |
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
http://localhost:9000/admin/backends 에서 `"mode"`가 `emergency` → `normal`로 바뀌는 것도 확인.

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

5~8분 걸려요. 마지막에 `OK (skipped=3)`이 나오면 성공이에요.
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
→ http://localhost:9000/admin/backends 에서 app1의 `weight`가 50이 됐다가 30초 뒤 100으로 돌아와요.
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
