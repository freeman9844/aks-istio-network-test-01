# TS6 영역별 노드 풀 + 영역별 Deployment 축소 검증 설계

## 의도

단일 `hpa-demo`(영역 강제 분산 + HPA)는 축소할 때 지울 Pod를 ReplicaSet 삭제 규칙이 고르므로 영역 균등이 보장되지 않는다(TS2 0/1/1, TS3 참조). 영역당 최소 1개를 구조적으로 보장하는 대안, 즉 **영역별 노드 풀 + 영역별 Deployment·HPA** 구성이 그 요건을 충족하는지 실측한다.

## 목표 / 비목표

- 목표: 영역별 HPA를 3·4개로 늘렸다 minReplicas 1로 낮출 때, 축소 중 어느 시점에도 각 영역 Running ≥ 1, 최종 1/1/1, Pod가 자기 영역 노드에만 있음을 확인한다.
- 비목표: 영역 간 부하 분배, CAS 동작(TS4·TS5에서 다룸), 기존 TS1~TS5 변경.

## 접근

| 안 | 내용 | 판단 |
|---|---|---|
| A (선택) | `hpa-z1/z2/z3` Deployment + HPA 각 1개, `nodeSelector: agentpool=userzN` | 기존 영역별 풀을 그대로 사용, 인프라 변경 없음 |
| B | 영역 라벨(`topology.kubernetes.io/zone`) nodeAffinity | 풀 경계가 아니라 영역 경계라 같은 효과지만, 요청이 "영역별 node pool"이라 A가 더 직접적 |
| C | 단일 Deployment + PodDisruptionBudget 등 | 축소 시 HPA/ReplicaSet은 PDB를 따르지 않아 보장 불가 |

## 설계

- 매니페스트 `40-scale.yaml`에 `hpa-z1~3`(pause, CPU 100m, SA 공유 `hpa-zonal`) 및 HPA(min=max=1, scaleDown Pods 1/60s, 기본 안정화 300초) 추가.
- `03-verify.sh`에 `zonal_test`와 `--only zonal`. `all`에 포함.
  - 라운드: 시작 3, 4를 각 2회(총 4라운드). 세 HPA를 동시에 min=max=N으로 올리고 Ready N/N/N 대기 → min 1, max N으로 낮춤 → 5초 간격으로 영역별 Running 표본 → 1/1/1 도달 또는 시간 초과.
  - 판정은 Python(`verdict.zonal_floor_verdict`): 모든 표본 영역별 최소 ≥ 1, 최종 1/1/1, 노드 영역 불일치 0 → PASS. 위반 시 FAIL, 시간 초과·데이터 없음은 INCONCLUSIVE.
  - 종료 시 세 HPA를 1/1로 복원.
- `report.ORDER`에 TS6 추가.
- 트레이드오프(README·장표 기록): 영역별 HPA는 부하를 영역 간에 자동 분배하지 않고, 최소 Pod 수가 영역 수의 배수가 된다.

## 빌더 재량

표본 수집 방식, 시간 초과 값(라운드당 약 12분), 로그 문구.
