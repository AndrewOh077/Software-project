# Week 6: 시나리오 8개 (통합 주간)

계획서 "4. 실제로 테스트할 상황"을 자동 테스트로 만든 것. 파일: `tests/e2e/test_scenarios.py`

```powershell
docker compose up -d --build
python -m unittest discover -s tests/e2e -v          # 전부 (5~8분)
python -m unittest discover -s tests/e2e -v -k s4    # 하나만
```

| # | 상황 | 시스템이 해야 하는 일 | 테스트 | 담당 | 상태 |
|---|---|---|---|---|---|
| 1 | 사용자 갑자기 증가 | Rate Limiting 적용 | `test_s1_traffic_spike_is_rate_limited` | **Person 2** | 자리만 있음 (skip) |
| 2 | Server B 종료 | B 제외, A·C로 분배, B 복귀 | `test_s2_one_server_killed` | Person 1 | ✅ |
| 3 | 서버 2개 동시 종료 | 남은 서버로 버팀 + 둘 다 복구 | `test_s3_two_servers_killed` | Person 1 | ✅ |
| 3b | 〃 | 남은 용량에 맞게 제한 축소 (AIMD) | `test_s3b_limit_shrinks_with_capacity` | **Person 2** | 자리만 있음 (skip) |
| 4 | 서버 복구 실패 | 재시작 3번 후 격리 + Emergency, 사람이 풀면 복구 | `test_s4_recovery_fails_then_emergency` | Person 1 | ✅ |
| 5 | 서버 응답속도 저하 | 느리다고 재시작하지 않음 (감지·제한은 Gateway/AI 몫) | `test_s5_slow_server_is_not_restarted` | Person 1 (+2, 3) | ✅ |
| 6 | 서버 다시 정상 작동 | Readiness 확인 후 10→25→50→100% 점진적 복귀 | `test_s6_gradual_return_after_recovery` | Person 1 | ✅ |
| 7 | 모든 App 서버 다운 | Emergency + 관리자 경고, 전부 복구 후 해제 | `test_s7_all_servers_down` | Person 1 | ✅ |
| 7b | 〃 | 일반 요청에 503 응답 | `test_s7b_gateway_returns_503_for_normal_requests` | **Person 2** | 자리만 있음 (skip) |
| 8 | AI 작동 중단 | AI 없이도 규칙만으로 복구 | `test_s8_recovery_works_without_ai` | Person 1 | ✅ |
| 9 | (Week 10) 장애 기록 | 장애 하나가 시작부터 끝까지, 걸린 시간과 함께 기록됨 | `test_s9_incident_is_recorded_with_duration` | Person 1 | ✅ |

+ Week 8 조기 경고: `EarlyWarning` 클래스 2개 ✅
+ Person 3: 시나리오 1·2·5와 "아프지만 살아 있는 서버"를 부하 테스트로 측정하고, AI가 5번(느려짐)을 10초 만에 감지 → `docs/experiments.md`

## Person 2에게
- skip된 3개(`s1`, `s3b`, `s7b`)에 `TODO(Person 2)`로 무엇을 확인하면 되는지 적어뒀어요.
- 진짜 Gateway를 띄운 다음 `REAL_GATEWAY=1`로 실행하면 skip이 풀려요:
  ```powershell
  $env:REAL_GATEWAY="1"; python -m unittest discover -s tests/e2e -v
  ```
- Gateway 관리 API 기본 주소는 `http://localhost:8080`이며, 다르면 `GATEWAY_ADMIN` 환경변수로 바꾸면 돼요.

## 장애 종류 추가: `break` / `fix`
4번 시나리오에는 "재시작해도 안 고쳐지는 고장"이 필요해서 chaos 도구에 추가했어요.
```powershell
python chaos/chaos.py break app2   # 설정 파일이 망가진 것처럼: 재시작해도 계속 죽음
python chaos/chaos.py fix app2     # 사람이 고친 것처럼
```
`data/faults/app2.broken` 파일이 있으면 app2의 `/health`가 계속 503을 돌려줘요.
