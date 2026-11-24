# Controller 설명서 (Week 4–10)

## 파일별 역할

| 파일 | 하는 일 |
|---|---|
| `core.py` | **핵심 두뇌.** 서버 상태를 판단하고 무엇을 할지 결정 |
| `adapters.py` | 바깥세상과 연결: 서버 검사(HTTP), `docker restart`, Gateway 호출, 알림 |
| `config.py` | 숫자 설정 (몇 번 실패하면 고장? 몇 번까지 재시작?) |
| `events.py` | 일어난 일을 `data/events.jsonl`에, 장애 하나하나를 `data/incidents.jsonl`에 기록 (Week 10) |
| `api.py` | `localhost:9100`: `/status`, `/metrics`, `/events`, `/incidents`, `POST /warning`, `POST /admin/release/{서버}` |
| `__main__.py` | 시작점: 전부 연결하고 1초마다 `tick()` 반복 |

## 서버 하나가 거치는 상태

```
STARTING ──ready──▶ HEALTHY ◀────────────── WARMING (트래픽 10→25→50→100%)
                     │  ▲                       ▲
             1번 실패 ▼  │ 회복                   │ ready
                    SUSPECT                     │
                     │ 3번 연속 실패              │
                     ▼                          │
                    DOWN ──재시작──▶ RESTARTING ─┘
                     │ ▲                 │ 30초 안에 안 살아남
                     │ └─────────────────┘ (기다리는 시간 2→4→8초로 늘림)
                     │ 2분 안에 3번 재시작해도 안 됨
                     ▼
                 QUARANTINED (재시작 중단, 알림, 비상 모드)   ← Week 5
```

**비상 모드 (Week 5)**: 일하는 서버가 0개이거나 격리된 서버가 있으면 ON → Gateway에 "중요한 요청만 처리" 지시.
서버 2개 이상이 10초 연속 정상이면 OFF.

## 왜 이렇게 만들었나 (면접에서 물어볼 것들)

1. **`/health`와 `/ready`를 왜 나눴나?**
   `/health` 실패 = 프로세스가 죽음 → 재시작해야 함.
   `/ready` 실패 = 살아는 있는데 바쁨 → 재시작하면 오히려 서버가 더 줄어듦. 트래픽만 빼고 15초 기다려줌.
   Kubernetes의 liveness/readiness probe와 같은 개념.

2. **왜 한 번 실패로 바로 고장 처리 안 하나?**
   네트워크가 잠깐 튀는 일은 흔해서. 3번 연속을 기준으로 해서 헛경보를 줄임.
   빨리 감지하기 vs 헛경보 줄이기의 트레이드오프.

3. **복귀할 때 왜 트래픽을 조금씩 늘리나?**
   막 켜진 서버는 아직 느릴 수 있고, 원인이 안 고쳐졌으면 바로 다시 죽음.
   10%로 먼저 확인하면 다시 터져도 피해가 작음 (slow start / canary).

4. **재시작 횟수는 왜 제한하나?** (Week 5)
   코드 버그 같은 건 재시작해도 안 고쳐짐. 무한 재시작(crash loop)은 자원 낭비.
   2분에 3번 넘으면 멈추고 사람을 부름. 재시도 간격도 점점 늘림 (exponential backoff).

5. **Gateway가 죽으면?**
   Gateway 호출이 실패해도 서버 재시작은 계속함. 보낸 지시를 기억해뒀다가 실패한 건 다음에 다시 보냄.

6. **Controller가 죽으면? (감시자는 누가 감시하나)**
   Controller 자신은 Docker의 `restart: unless-stopped`가 살려줌.
   상태는 매초 서버를 직접 검사해서 다시 만들어지니까 재시작돼도 금방 원래대로.

7. **AI 경고는 왜 재시작을 못 하게 했나?** (Week 8)
   ML은 틀릴 수 있어요. 경고가 할 수 있는 일은 "30초 동안 트래픽 50%"라는 되돌릴 수 있는 조치뿐이라,
   헛경고가 나도 피해가 작고 저절로 원래대로 돌아와요. **AI는 조언하고, 규칙이 실행한다.**
   그리고 AI가 꺼져도 규칙 기반 복구는 그대로 동작해요 (시나리오 8).

8. **느린 서버는 왜 재시작 안 하나?** (시나리오 5)
   느린 건 고장이 아니라 바쁜 거예요. 바쁜 서버를 재시작하면 일할 서버가 더 줄어서 상황이 나빠져요.
   느린 서버에 트래픽을 덜 보내는 건 Gateway(Person 2)와 이상 감지(Person 3)의 몫이에요.

9. **왜 외부 패키지를 하나도 안 썼나?**
   Controller는 시스템에서 제일 튼튼해야 하는 부품이라 의존성을 최소화.
   그리고 바깥 연결(검사, docker, gateway, 시계)을 전부 갈아 끼울 수 있게 만들어서,
   테스트가 Docker 없이 0.01초 만에 돌아감 (`tests/unit/test_core.py`).

## 조기 경고 (Week 8) — Person 3에게 공유할 것

Person 3의 이상 감지(z-score, Isolation Forest)가 "이 서버 곧 문제 생길 것 같아"라고 판단하면 이렇게 호출해요.

```powershell
curl.exe -X POST localhost:9100/warning -H "Content-Type: application/json" `
  -d '{\"server\": \"app2\", \"score\": 0.93, \"reason\": \"p99 latency 3x baseline\", \"source\": \"zscore\"}'
```

```python
# Python에서 (Person 3 코드에 넣을 것)
import json, urllib.request
req = urllib.request.Request("http://localhost:9100/warning", method="POST",
    data=json.dumps({"server": "app2", "score": 0.93, "reason": "p99 3x", "source": "zscore"}).encode(),
    headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req).read())
```

| 응답 | 의미 |
|---|---|
| 200 `{"accepted": true, "action": "reduce_weight", "weight": 50, "duration_s": 30}` | 받아들임: 30초 동안 트래픽 50% |
| 409 `{"accepted": false, ...}` | 이미 장애 처리 중이거나 없는 서버 이름 |
| 400 | `server`가 빠짐 |

- 경고는 `data/events.jsonl`에 `"type": "warning"`으로 기록돼요. 나중에 "경고가 실제 장애보다 몇 초 먼저 왔나",
  "헛경고(false positive)가 몇 번이었나"를 계산할 때 `data/chaos.jsonl`(실제로 장애를 넣은 시각)과 비교하면 돼요.
- 설정: `WARN_WEIGHT` (기본 50), `WARN_DURATION_S` (기본 30)

## 운영자 명령: release

```powershell
curl.exe -X POST localhost:9100/admin/release/app2
```
- 격리(QUARANTINED)된 서버: 재시작 기록을 지우고 다시 살려봄 ("고쳤으니 다시 해봐")
- 정상 서버: 재시작 기록만 지움 ("지난 실패는 잊어줘"). 테스트가 같은 서버를 계속 죽일 때 씀

## Gateway와의 약속 (Person 2에게 공유할 것)

Controller는 Gateway에 이 5개를 호출해요. Week 0에 정한 약속과 다르면 `adapters.py`의 `HttpGateway`를 고치면 돼요.

| 호출 | 의미 |
|---|---|
| `GET /admin/backends` | 현재 상태 조회 |
| `POST /admin/backends/{id}/disable` | 이 서버로 요청 보내지 마 |
| `POST /admin/backends/{id}/enable` | 다시 보내 |
| `POST /admin/backends/{id}/weight` body `{"weight": 0-100}` | 트래픽 비율 |
| `POST /admin/mode` body `{"mode": "normal" 또는 "emergency"}` | 비상 모드 켜기/끄기 |

## 기록 파일 (Person 3에게 공유할 것)

- `data/events.jsonl`: Controller가 한 일. 예: `{"time": ..., "type": "recovered", "server": "app2", "recovery_s": 9.1}`
- `data/chaos.jsonl`: chaos 도구로 언제 무슨 장애를 넣었는지 (정답 기록)
- 복구 시간(MTTR) = chaos.jsonl의 장애 시각부터 events.jsonl의 `recovered` 시각까지
- `data/incidents.jsonl` (Week 10): 장애 하나당 한 줄. `started`, `ended`, `duration_s`(MTTR), `servers`, `restarts`, `emergency`, `timeline`.
  장애는 서버가 DOWN이 되거나 비상 모드가 켜질 때 열리고, 모든 서버가 정상 + 비상 모드 해제일 때 닫혀요.
  `localhost:9100/incidents`로도 볼 수 있어요.
