# 실측 증적

이 폴더에는 두 실행판의 원본 결과가 있습니다. 메인은 **Linux 관리 VM판**이고, PowerShell판은 이전 판으로 보존합니다.

- [`linux/`](linux/): Linux 관리 VM판(Private Cluster, bash + az CLI), TN1~TN22·TS1~TS6. **27건 PASS + TS2 INFO**(최신: 07 전체 2026-11-24, 08 TS6 2026-11-25)
- 이 폴더의 `01`~`11`, `logs/`: PowerShell판(공개 API 서버, `03-verify.ps1`), TN1~TN13·TS1~TS5. 17건 PASS + TS2 INFO

증적 파일의 검증 ID와 파일명은 새 번호(네트워크 `TN#`, 스케일링 `TS#`)로 바꿨습니다(2026-11-24). 이전 번호와의 대응은 [루트 README](../README.md#검증-id)를 참고하세요. 측정값·판정은 바꾸지 않았습니다.

## Linux 관리 VM판 (`linux/`)

2026-11-20 koreacentral, `rg-aksnetval-linux-krc`에서 `local/run.sh verify`로 실행한 결과입니다. `vm-mgmt`가 만든 `results/<시각>.md`·`.json`과 TN14 VM 측 근거(`.tn14-vm.json`)를 옮겼습니다.

| 순서 | 파일 | 범위 | 결과 |
|---|---|---|---|
| 01 | `01-full-run_20261120-050457` | TN1~TN20·TS1~TS5 전체 | 23건 PASS + TS2 INFO, **TN19만 INCONCLUSIVE**. 정책 생성과 app Pod 재시작이 같은 초(초 단위 타임스탬프)라 "정책 이후 기동" 근거가 성립하지 않음 |
| 02 | `02-network-rerun_20261120-052231` | `--only network`(TN1~TN20) | `02-apply.sh`가 `sleep 2` 후 재시작하도록 고치고 apply를 다시 실행한 뒤 **20건 PASS**(TN19 포함) |
| 03 | `03-node-egress-fail_20261120-075659` | `--only network`(TN1~TN22), 새 클러스터 | 20건 PASS, **TN21·TN22 FAIL**(bff-b → 노드 :10250이 000, 대조군 probe 401). Cilium 기본값에서 NetworkPolicy `ipBlock`이 노드 IP에 매칭되지 않음 |
| 04 | `04-node-egress-fix_20261120-082121` | `--only network`(TN1~TN22) | CiliumNetworkPolicy `app-egress-nodes`(`toEntities: [host, remote-node]`) 추가 후 **22건 PASS**(TN21·TN22 401) |
| 05 | `05-ts3-rules_20261120-131301` | `--only floor`(TS3), 새 클러스터 | 5·9·12 → 3 각 2회, 6회 모두 1/1/1, 삭제 34건 모두 ReplicaSet 규칙 예측과 일치 → **PASS**. 첫 판정은 Ready 동률 비이행 비교 1건 때문에 FAIL이었고, 판정 로직을 고친 뒤 같은 원시 데이터(`.rounds.json`)로 재판정 |
| 06a | `06a-ts3-predictor-gap_20261122-103327` | 코드 리뷰 수정 후 재빌드, `--only floor`(TS3) | **FAIL**. 12개 축소에서 예측 후보에 없던 Pod가 먼저 삭제됨. 클러스터 이상이 아니라 판정기 한계(삽입 정렬에서 동률이 사슬로 이어지는 경우를 한 단계만 고려). 판정기를 고친 뒤 같은 원시 데이터(`.rounds.json`)로 재판정하면 PASS |
| 06 | `06-ts3-rules_20261122-114525` | 판정기 수정 후 `--only floor`(TS3) 재실행 | 2/2/1·3/3/3·4/4/4 → 6회 모두 1/1/1, 삭제 34건 모두 예측과 일치(후보 2개 이상 20건, Ready 동률 비이행 비교 5건, 한 번에 2개 삭제 1회) → **PASS**. 이 실행의 원시 `.rounds.json`은 VM 삭제 전에 회수하지 못해 판정 결과(`.md`·`.json`)만 보관 |
| 07 | `07-full-run-renumbered_20261124-061859` | 검증 ID 재번호(TN#·TS#) 후 새 클러스터에서 전체 실행 | **26건 PASS + TS2 INFO**. 결과가 TN1~TN22 → TS1~TS5 순으로 기록됨. TS3 6회 모두 1/1/1, 삭제 34건 모두 예측과 일치(후보 2개 이상 26건, 한 번에 2개 삭제 1회). 원시 `.rounds.json` 포함 |
| 08 | `08-ts6-zonal_20261125-115943` | TS6 추가 후 새 클러스터에서 `--only zonal`(TS6) | 영역별 Deployment·HPA `hpa-z1~3`을 3/3/3 → 1/1/1 2회, 4/4/4 → 1/1/1 2회 축소. 5초 표본 334개 모두 영역별 Running ≥1, 영역 불일치 0 → **PASS**. 원시 `.rounds.json` 포함 |

종합 **27건 PASS + TS2 INFO**는 07(2026-11-24 전체 재실행, 26건 PASS + TS2 INFO)과 08(2026-11-25 TS6 PASS)의 결과입니다.

### linux/logs/

| 파일 | 내용 |
|---|---|
| `00-bootstrap.log` | 로컬 부트스트랩(사전 점검, vm-mgmt 생성, 권한 부여) |
| `01-infra.log`, `01-infra-rerun.log` | `vm/01-infra.sh`. 첫 실행에서 PE DNS zone group이 만들어지지 않아(az가 없는 리소스에 `{}`·rc 0을 반환) 판정 함수를 고친 뒤 재실행 |
| `02-apply.log`, `02-apply-rerun.log` | `vm/02-apply.sh`. 재실행은 TN19 재시작 타이밍 수정 후 |
| `03-verify-full.log` | 01 전체 검증 로그(HPA 감소 시각, 노드·Pod 추이 포함) |
| `03-verify-network-rerun.log` | 02 네트워크 재실행 로그 |
| `03-verify-node-egress.log` | 03 실행 로그(새 클러스터 부트스트랩·infra·apply·verify 전체) |
| `diag-node-egress.sh` | 03 이후 vm-mgmt run-command로 실행한 진단 스크립트(ipBlock·CNP 조건별 bff-b → 노드 :10250) |
| `02-apply-node-egress-fix.log` | `app-egress-nodes` 추가 후 `vm/02-apply.sh` 재실행 로그 |

Linux판은 Storage 계정 이름(`staksnetval<hash>`) 외에 구독 이름(`<subscription>`)과 API 서버 사설 FQDN(`<cluster>.<guid>.privatelink…`)도 마스킹했습니다.

## PowerShell판

2026-11-19 koreacentral 클러스터에서 `03-verify.ps1`을 실행한 원본 결과입니다. 파일 이름은 `<순서>-<의미>_<실행 시각>`이며, 각 실행마다 `.md`(표)와 `.json`(기계 판독용)이 있습니다.

| 순서 | 파일 | 범위 | 결과 |
|---|---|---|---|
| 01 | `01-network-baseline_20261119-183104` | TN1~TN12 | 12건 PASS |
| 02 | `02-network-netpol-removed_20261119-183316` | TN1~TN12, NetworkPolicy `deny-web-to-data` 삭제 | **TN11만 FAIL**(400). 부정 검증 |
| 03 | `03-network-netpol-reapplied_20261119-183538` | TN1~TN12, 정책 재적용 | 12건 PASS |
| 04 | `04-network-rerun_20261119-184233` | TN1~TN12 | 12건 PASS |
| 05 | `05-hpa-only_20261119-184952` | TS1 | PASS, 간격 60.4초·59.7초 |
| 06 | `06-zone-1200m-saturated_20261119-185218` | TS4, `zone-fill` 1200m | 3/3/3. 모든 풀이 최대치라 균형 근거로 쓰지 않음 |
| 07 | `07-zone-800m_20261119-190729` | TS4, `zone-fill` 800m | 3/2/2 PASS |
| 08 | `08-final-full-run_20261119-192119` | TN1~TN12·TS1·TS4 전체 | **14건 PASS** (v1 최종) |
| 09 | `09-v3-full-run_20261119-233942` | TN1~TN13·TS1·TS2·TS4·TS5 전체, PRD 권장 YAML 반영 | **16건 PASS + TS2 INFO**(축소 뒤 1/0/1, Pod 2개라 필연). 새 클러스터에서 재실행 |
| 10 | `10-v4-full-run_20261120-100357` | TN1~TN13·TS1~TS5 전체 | TS3만 스크립트 오류로 INCONCLUSIVE, 나머지 16건 PASS + TS2 INFO |
| 11 | `11-v4-ts3-rerun_20261120-100846` | TS3(`-Only Floor`) | **PASS** — minReplicas 3 축소 3회 모두 1/1/1 |

### logs/(PowerShell판)

| 파일 | 내용 |
|---|---|
| `02-apply.log`, `02-apply-rerun.log` | `02-apply.ps1` 실행 로그 |
| `20-istio.rendered.yaml` | Istio 리비전을 채운 뒤 실제 적용한 매니페스트 |
| `run-hpa.log` | 05 실행 로그 |
| `run-zone.log`, `run-zone2.log` | 06, 07 실행 로그 |
| `run-all.log` | 08 최종 전체 실행 로그(HPA 감소 시각과 노드 증가 추이 포함) |
| `02-apply-v3.log` | v3 재검증용 `02-apply.ps1` 실행 로그 |
| `run-all-v3.log` | 09 전체 실행 로그(TS2 분포, TS5 Pod·노드 추이 포함) |
| `02-apply-v4.log` | v4 재검증용 `02-apply.ps1` 실행 로그 |
| `run-all-v4.log` | 10 전체 실행 로그 |
| `run-floor-v4.log` | 11 TS3 재실행 로그(회차별 축소 전→후 분포) |

로컬 경로는 `<repo>`로, Storage 계정 이름은 `staksnetval<hash>`로 마스킹했습니다. 리소스 그룹은 검증 후 삭제했습니다.
