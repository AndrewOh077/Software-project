# Self-Healing Platform: 3-Mode Experiment Results

## 1. Experiment Overview
본 실험은 자가 치유 플랫폼의 보호 수준에 따른 시스템 안정성과 트래픽 방어 능력을 검증하기 위해 수행되었습니다.

## 2. Three Modes Comparison
| 모드 이름 | Controller 상태 | Gateway 상태 | 특징 및 결과 |
| :--- | :--- | :--- | :--- |
| **Mode 1: No Protection** | 비활성화 (재시작 안 함) | 기본 라우팅만 수행 | 서버 장애 또는 트래픽 폭주 시 연쇄 장애(Cascading Failure) 발생 및 전체 서비스 중단[cite: 11, 12]. |
| **Mode 2: Rule-based Only** | 활성화 (자동 재시작 & 램프업) | 기본 라우팅만 수행 | 장애 서버는 자동 복구되나, 폭주하는 트래픽을 제어하지 못해 일시적 병목 및 지연 발생[cite: 11, 12]. |
| **Mode 3: Full Self-Healing** | 활성화 (격리 및 경고 제어 포함)