# TS6 영역별 Deployment 축소 검증 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 영역별 노드 풀(userz1/2/3)에 고정한 영역별 Deployment·HPA(`hpa-z1~3`)가 축소 중에도 영역당 Running ≥ 1을 지키는지 TS6으로 실측한다.

**Architecture:** 판정·집계는 `aksnetval/kube.py`·`verdict.py`의 순수 함수로 두고 pytest로 고정한다. `vm/03-verify.sh`의 `zonal_test`가 HPA 패치·5초 표본 수집만 하고 판정은 Python에 맡긴다(TS1·TS3와 같은 방식).

**Tech Stack:** bash, jq, kubectl, Python 3(표준 라이브러리), pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-aksnetval-ts6-zonal-deployments-design.md`

## Global Constraints

- 작업 폴더: `aks-network-validation-linux/`. 기존 TS1~TS5 동작은 바꾸지 않는다.
- 이름: Deployment·HPA `hpa-z1`, `hpa-z2`, `hpa-z3`; 공통 라벨 `group: hpa-zonal`, 영역 라벨 `zone: "1"|"2"|"3"`; SA `hpa-zonal`; 네임스페이스 `scale`.
- 배치: `nodeSelector: {kubernetes.azure.com/agentpool: userzN}`. pause:3.9, CPU 요청 100m.
- HPA: 평소 min=max=1, CPU 70%, scaleDown Pods 1 / 60s, 안정화 기본값.
- 라운드: 시작 3, 4 각 2회(순서 3,3,4,4). 축소 목표 minReplicas 1.
- 판정: 모든 표본 영역별 Running 최소 ≥ 1 이고 영역 불일치 0, 최종 1/1/1 → PASS. 위반 → FAIL. 위반 없이 1/1/1 미도달·표본 없음 → INCONCLUSIVE.
- 결과 ID `TS6`, `--only zonal`, `all`에 포함. 원시 표본은 `$VM_ROOT/results/ts6-rounds-<stamp>.json`.
- 문구에 "PoC 필요"를 쓰지 않는다. 커밋마다 Copilot 트레일러.

## Review Focus

- 축소 중 삭제 중(deletionTimestamp) Pod는 Running으로 세지 않아야 한다 → Task 1 `test_zonal_counts_excludes_deleting`.
- 다른 영역 노드에 뜬 Pod(라벨 zone ≠ 노드 zone)는 불일치로 세야 한다 → Task 1 `test_zonal_counts_mismatch`.
- 표본 중 한 영역이 0이 된 순간이 있으면 최종 1/1/1이어도 FAIL → Task 1 `test_zonal_floor_fail_on_zero_sample`.
- 시작 조건(N/N/N Ready) 미충족 라운드는 INCONCLUSIVE로 끝나고 HPA는 1/1로 복원 → Task 3 `restore_zonal` 호출 확인 테스트.
- `report.ORDER`에서 TS6이 TS5 뒤 → Task 3 `test_order_ts6_after_ts5`.

---

### Task 1: 집계·판정 함수

**Files:**
- Modify: `aksnetval/kube.py` (함수 추가), `aksnetval/verdict.py` (함수 추가)
- Test: `tests/test_kube.py`, `tests/test_verdict.py`

**Interfaces:**
- Produces: `kube.zonal_counts(nodes: dict, pods: dict) -> dict` → `{"counts": {"1": int, "2": int, "3": int}, "mismatch": int, "pending": int}`. counts는 `_alive` 이고 phase Running인 Pod의 노드 영역 기준; mismatch는 라벨 `zone` ≠ 노드 영역인 살아 있는 Pod 수.
- Produces: `verdict.zonal_floor_verdict(rounds: list[dict]) -> dict` → `{"verdict", "observed", "reason"}`. 입력 라운드 `{"start": int, "before": counts, "samples": [zonal_counts 결과...], "final": zonal_counts 결과}`.
  observed 예: `"3/3/3→1/1/1, 3/3/3→1/1/1, 4/4/4→1/1/1, 4/4/4→1/1/1"`, reason 예: `"표본 412개 모두 영역별 Running ≥1(최소 1), 영역 불일치 0"`.

- [ ] **Step 1: 실패 테스트 작성**
  - `test_zonal_counts_excludes_deleting`: 영역1 Running 2개 중 1개 deleting → counts["1"] == 1.
  - `test_zonal_counts_mismatch`: 라벨 zone "2" Pod가 영역1 노드 → mismatch == 1.
  - `test_zonal_floor_pass`: 4라운드, 표본 최소 1, final 1/1/1 → PASS, observed 위 예와 같음.
  - `test_zonal_floor_fail_on_zero_sample`: 한 표본 {"1":0,...} → FAIL, reason에 "영역1" 포함.
  - `test_zonal_floor_fail_on_mismatch`: mismatch 1 → FAIL.
  - `test_zonal_floor_inconclusive_not_reached`: 위반 없음, final 2/1/1 → INCONCLUSIVE.
  - `test_zonal_floor_inconclusive_empty`: `[]` → INCONCLUSIVE.
- [ ] **Step 2:** `wsl ... python3 -m pytest -q tests/test_kube.py tests/test_verdict.py` → 새 테스트 FAIL(AttributeError).
- [ ] **Step 3:** 두 함수 구현(CLI는 기존 `_cli.run(vars(module))`가 자동 노출).
- [ ] **Step 4:** 같은 명령 → PASS.
- [ ] **Step 5:** 커밋 `feat(ts6): 영역별 Deployment 집계·판정 함수`.

### Task 2: 매니페스트

**Files:** Modify `manifests/40-scale.yaml`; Test `tests/test_manifests.py`

- [ ] **Step 1: 실패 테스트** `test_zonal_deployments(n)` (n=1,2,3): yaml에 `name: hpa-zN` Deployment가 있고 `kubernetes.azure.com/agentpool: userzN`, `zone: "N"`, `group: hpa-zonal`, replicas 없음; `test_zonal_hpas`: `kind: HorizontalPodAutoscaler` 중 `name: hpa-z` 3개, 각 `minReplicas: 1`·`maxReplicas: 1`.
- [ ] **Step 2:** pytest → FAIL.
- [ ] **Step 3:** SA `hpa-zonal`, Deployment·HPA 3쌍 추가(상단 주석에 TS6 설명·트레이드오프 한 줄).
- [ ] **Step 4:** pytest → PASS. `kubectl apply --dry-run=client`는 클러스터가 없으므로 생략, `python3 -c "import yaml; list(yaml.safe_load_all(...))"`로 파싱 확인.
- [ ] **Step 5:** 커밋.

### Task 3: 검증 스크립트·보고·문서

**Files:** Modify `vm/03-verify.sh`, `aksnetval/report.py`, `local/run.sh`(--only 허용값 있으면), `README.md`; Test `tests/test_report.py`, `tests/test_shell.py`

**Interfaces:** Consumes `kube zonal_counts`, `verdict zonal_floor_verdict` (Task 1).

- [ ] **Step 1: 실패 테스트**
  - `test_order_ts6_after_ts5`: 입력 `[TS6, TN1, TS5]` → `[TN1, TS5, TS6]`.
  - `test_only_accepts_zonal`: `03-verify.sh`의 `--only` case에 `zonal`이 있고 `run_test TS6`가 `restore_zonal`로 감싸져 있음(본문 문자열 검사, 기존 `test_floor_test_stops_watch_on_exit` 방식).
- [ ] **Step 2:** pytest → FAIL.
- [ ] **Step 3:** 구현
  - `report.ORDER` 끝을 `range(1, 7)`로.
  - `zonal_set N MAX`(세 HPA 패치), `zonal_snapshot`(nodes·pods `-l group=hpa-zonal` → `kube zonal_counts`), `zonal_ready N`(세 Deployment replicas·ready == N 그리고 counts N/N/N), `restore_zonal`(1/1), `zonal_test`.
  - `zonal_test`: 라운드마다 `zonal_set N N` → `wait_cond 600 5 zonal_ready N`(실패 시 TS6 INCONCLUSIVE 후 return) → before 스냅샷 → `zonal_set 1 N` → 5초마다 스냅샷을 samples에 추가, 1/1/1이면 10초 더 표본 후 종료, 최대 1200초 → final. rounds를 `ts6-rounds-*.json`으로 저장 후 판정, `add TS6 '—' 'scale/hpa-z1·z2·z3 (영역별 Deployment, 3·4개 → minReplicas 1, 각 2회)' '축소 중 영역별 Running ≥1, 최종 1/1/1' ...`.
  - 실행부: `zonal`·`all`에서 `PENDING_RESTORE=restore_zonal; run_test TS6 ... zonal_test; restore_zonal`. `--only` 허용값·사용법 주석에 `zonal`.
  - `local/run.sh`가 TS6 원시 파일도 회수(`ts6-rounds` 최신 파일 → `$results/$name.ts6-rounds.json`, 실패는 경고만).
  - README: 테스트 목록에 TS6 행, `--only` 설명, 소요 시간(+약 40분), 트레이드오프 문장.
- [ ] **Step 4:** 전체 `python3 -m pytest -q -p no:cacheprovider` → 기존 + 신규 모두 PASS, `bash -n` 전 스크립트 OK.
- [ ] **Step 5:** 커밋.

### Task 4: 실측·증적·장표·동기화

- [ ] **Step 1:** `local/00-bootstrap.sh --force && local/run.sh infra && local/run.sh apply && local/run.sh verify --only zonal` (약 1시간 30분). 기대: TS6 PASS.
- [ ] **Step 2:** 결과 회수 확인(`.json`·`.md`·`.ts6-rounds.json`), 원시 데이터로 `verdict zonal_floor_verdict` 재판정 일치.
- [ ] **Step 3:** 리소스 그룹 삭제(`az group delete --no-wait`).
- [ ] **Step 4:** README 최신 결과에 TS6 행 추가, 로컬 커밋.
- [ ] **Step 5:** 장표 v11(v10 복사 → 스케일링 매트릭스·결과 장표에 TS6 추가) 빌드·시각 확인.
- [ ] **Step 6:** GitHub 동기화: 코드·README, `evidence/linux/08-ts6-zonal_<stamp>.*`(스토리지 해시·API FQDN 마스킹), 증적 README 행, 루트 README 결과 표·장표 링크 v11.
