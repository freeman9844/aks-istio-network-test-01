# AKS 네트워크 통제 검증 — Linux 관리 VM·az CLI 실행판 설계

작성일: 2026-11-20 · 기준 설계: `2026-11-19-aks-network-control-validation-design.md` (이하 "기존 설계")

## 1. 의도와 배경

기존 검증(`aks-network-validation/`)은 Windows 로컬의 PowerShell 7 에서 `az`·`kubectl` 을 호출한다. 이를 **VNet 안의 Linux 관리 VM 에서 bash + az CLI 중심으로 실행**하는 형태로 바꾼다.

- 사용자 요청: "설치 테스트 스크립트를 ps1 이 아니라 linux 관리 VM 을 하나 띄워서 주로 az cli 형태로"
- 사용자 결정(2026-11-20):
  - 판정 로직: **az/kubectl 호출은 bash, 판정만 Python + pytest**
  - 관리 VM 접속: **공인 IP 없이 `az vm run-command`** (Bastion·SSH 공개 없음)
  - AKS: **Private Cluster 로 바꿔서 검증**
  - 실행 방식: **A안 — 로컬 얇은 실행기 + 관리 VM 비동기 실행**
- 효과: API 서버가 VNet 안에서만 열리는 PRD 운영 형태(관리 VM 에서 운영)를 재현하고, Windows·PowerShell 의존을 없앤다.

## 2. 목표 · 비목표 · 반목표

**목표**
1. 기존 T1\~T14(T12b·T12c·T13b 포함)를 Private Cluster 에서 같은 판정 규칙으로 재현한다.
2. 클러스터 생성·매니페스트 적용·검증을 관리 VM 안에서 bash + az CLI + kubectl 로 실행한다.
3. 판정 로직은 Python 순수 함수로 옮기고 pytest 로 기존 Pester 단위 테스트와 같은 사례를 검증한다.
4. 로컬에는 `az` 와 bash 만 있으면 된다(Windows 는 Git Bash 또는 Azure Cloud Shell).
5. API 서버 비공개 여부를 새 테스트 T15 로 판정한다.

**비목표**
- 기존 PowerShell 판(`aks-network-validation/`)의 수정·삭제. 그대로 보존한다.
- 매니페스트 내용 변경. 기존 `aks-network-validation/manifests/` 를 그대로 쓴다.
- 성능 검증, TLS 종단, Descheduler.
- Bastion, 공인 IP, Entra ID 기반 kubectl 인증(Azure RBAC for Kubernetes).

**반목표(이러면 실패)**
- run-command 출력 4KB 제한 때문에 검증 결과 일부가 사라지거나 잘린 채로 PASS 처리되는 것.
- 관리 VM·`snet-mgmt` 추가가 T1\~T3(NSG) 판정 결과에 영향을 주는 것.
- 판정 규칙이 기존 PowerShell 판과 달라지는 것(같은 입력에 다른 판정).

## 3. 제약(공식 문서 확인)

| 항목 | 내용 | 출처 |
|---|---|---|
| run-command(action) | 출력은 마지막 4,096바이트만, 최소 약 20초, VM 당 한 번에 하나, 취소 불가, 최대 90분, 결과 반환에 VM 아웃바운드 443(AzureCloud) 필요 | learn.microsoft.com/azure/virtual-machines/linux/run-command |
| 기본 아웃바운드 | 2026-03-31 이후 API 로 만든 새 VNet 은 기본 private 서브넷. VM 은 명시적 아웃바운드(NAT Gateway 등)가 필요 | learn.microsoft.com/azure/virtual-network/ip-services/default-outbound-access |
| Private Cluster | API 서버 private endpoint 는 첫 노드 풀의 서브넷(`snet-aks-app`)에 생성. 기본으로 private·public FQDN 과 DNS 영역을 함께 만든다 | learn.microsoft.com/azure/aks/private-clusters |

추가 제약
- 기존 NSG 규칙은 출발지를 `snet-appgw-sim`(10.50.3.0/27)·`snet-other`(10.50.3.32/27)로 한정하므로 `snet-mgmt` 는 규칙에 걸리지 않는다.
- `01-infra` 는 관리 ID `id-aksnetval` 에 VNet Network Contributor 를 할당한다. 관리 VM 의 ID 에도 역할 할당 권한이 필요하다.
- vCPU: 관리 VM `Standard_B2s_v2` 1대가 추가되어 DSv5 22 + BSv2 6 = **총 28 vCPU**.

## 4. 접근 방식(검토한 안)

| 안 | 내용 | 장점 | 단점 | 결정 |
|---|---|---|---|---|
| **A. 로컬 얇은 실행기 + VM 비동기 실행** | 로컬 bash 가 관리 VM 을 만들고 코드를 넣은 뒤 run-command 로 백그라운드 실행을 시작하고, 로그·결과를 run-command 로 나눠 가져온다 | 추가 리소스 없음, 구현 단순, az 만 사용 | 진행 로그가 수십 초 늦게 보임, 결과 회수에 청크 처리 필요 | **채택** |
| B. 관리형 run-command + Blob 출력 | `az vm run-command create --async-execution --output-blob-uri` | 비동기·출력 보존을 Azure 가 맡음 | SAS·출력용 Storage 접근 경로 설계 필요, 검증용 Storage(공용 접근 차단)와 분리 필요 | 기각 |
| C. Bastion + SSH | 사람이 접속해 직접 실행 | 대화형 디버깅 편함 | 비용, 사용자 결정(공인 접속 없음)과 다름 | 기각 |

판정 로직 처리도 bash+jq 전체 재작성 / 판정만 Python / VM 에서 pwsh 로 기존 모듈 재사용 중 **판정만 Python** 을 사용자가 골랐다.

## 5. 구성

### 5.1 토폴로지(기존 설계 대비 변경점만)

```
vnet-aksnetval 10.50.0.0/16
├─ (기존) snet-aks-app / snet-ingress / snet-pe / snet-appgw-sim / snet-other
└─ (추가) snet-mgmt 10.50.3.64/27   VM vm-mgmt (Ubuntu 22.04, Standard_B2s_v2, 공인 IP 없음)
                                     NAT Gateway nat-mgmt + 공인 IP pip-nat-mgmt (이 서브넷에만 연결)
AKS aks-aksnetval: --enable-private-cluster --disable-public-fqdn 추가 (그 외 옵션 기존과 동일)
```

- `snet-mgmt` 에는 NSG 를 두지 않는다(기존 NSG 규칙과 독립).
- NAT Gateway 는 관리 VM 의 패키지 설치와 run-command 결과 반환용이다. 다른 서브넷에는 연결하지 않아 기존 VM·노드의 아웃바운드 경로를 바꾸지 않는다.
- Private Cluster 의 private DNS 영역은 AKS 가 클러스터 VNet 에 자동 연결하므로 관리 VM 이 API 서버 FQDN 을 사설 IP 로 해석한다.

### 5.2 권한

| 주체 | 범위 | 역할 | 이유 |
|---|---|---|---|
| 로컬 사용자 | 구독 | 리소스 그룹 생성 + 역할 할당 가능(Owner 또는 Contributor + User Access Administrator) | 부트스트랩 |
| `vm-mgmt` 시스템 할당 관리 ID | `rg-aksnetval-linux-krc` | Contributor | VNet·NSG·Storage·PE·VM·AKS 생성, 다른 VM run-command, `aks get-credentials` |
| `vm-mgmt` 관리 ID | `rg-aksnetval-linux-krc` | Role Based Access Control Administrator, **조건: Network Contributor 만 할당 가능** | `id-aksnetval` 에 VNet Network Contributor 할당 |

리소스 그룹 생성·삭제는 관리 VM 이 하지 않는다(자기 자신이 그 안에 있으므로). 부트스트랩과 정리는 로컬에서만 한다.

### 5.3 실행 흐름(A안)

```
[로컬 bash]                                   [vm-mgmt 안, root, /opt/aksnetval]
00-bootstrap.sh  RG·VNet·snet-mgmt·NAT·vm-mgmt·역할
                 └ run-command: 도구 설치(az, kubectl, python3-venv, jq) + az login --identity
run.sh <단계>   ─ 코드 tar.gz → base64 청크 → run-command 로 업로드·검증(sha256)
                ─ run-command: systemd-run --unit aksnetval-<단계> ./<단계>.sh   ← 즉시 반환
                ─ 주기적 run-command: 상태(systemctl is-active)·로그 tail(4KB 이내)   → 로컬 화면
                ─ 종료 후 run-command: 결과 파일 base64 청크 회수 → 로컬 results/ 에 재조립·sha256 확인
                                              01-infra.sh   VNet 나머지·NSG·Storage·PE·VM 2대·Private AKS·풀
                                              02-apply.sh   Istio 내부 Gateway, 매니페스트 적용
                                              03-verify.sh  T1~T15 → results/<ts>.json·.md (판정: python -m aksnetval.verdict)
99-cleanup.sh    로컬에서 az group delete --no-wait (이름 입력 확인, --force 로 생략)
```

- 백그라운드 실행은 `systemd-run` 으로 run-command 프로세스와 분리한다(run-command 가 끝나도 작업이 계속되고 90분 제한을 받지 않는다).
- 같은 단계를 이미 실행 중이면 `run.sh` 는 새로 시작하지 않고 기존 실행의 상태만 따라간다.
- 진행 로그 조회 간격은 기본 30초(`--poll` 로 변경).
- 결과 회수는 청크마다 3,000바이트 이하 base64 로 받아 4KB 제한 안에 둔다. 재조립 후 VM 쪽 sha256 과 비교해 다르면 실패로 끝낸다.
- run-command 는 VM 당 하나만 실행되므로 `03-verify.sh` 가 `vm-appgw-sim`·`vm-other` 에 거는 run-command 와 로컬의 `vm-mgmt` 폴링은 서로 다른 VM 이라 충돌하지 않는다.

### 5.4 파일 구성(새 폴더, 기존 폴더 보존)

```
aks-network-validation-linux/
├─ README.md
├─ config.sh                 # 기존 config.ps1 의 $Cfg 와 같은 값 + snet-mgmt·vm-mgmt·NAT
├─ local/
│  ├─ 00-bootstrap.sh        # 로컬 실행
│  ├─ run.sh                 # 로컬 실행: 업로드·시작·폴링·회수 (단계: infra|apply|verify [--only ...])
│  └─ 99-cleanup.sh          # 로컬 실행
├─ vm/
│  ├─ 01-infra.sh
│  ├─ 02-apply.sh
│  ├─ 03-verify.sh           # 수집(bash) → JSON 관측값 → python 판정 → 결과 저장
│  └─ lib/common.sh          # az·kubectl 래퍼, 재시도, curl 래퍼(rc 표기), 영역 집계
├─ aksnetval/                # Python 판정 패키지(표준 라이브러리만)
│  ├─ verdict.py             # Verdict.psm1 의 9개 함수 대응
│  ├─ parse.py               # Common.psm1 의 Get-CurlCode·Get-FirstIPv4·ConvertFrom-CurlRun·ConvertTo-ZoneKey 대응
│  └─ report.py              # 결과 JSON·MD 작성(열: Id, From, To, Expect, Observed, Verdict, Note)
└─ tests/                    # pytest
   ├─ test_verdict.py        # Verdict.Tests.ps1 사례 이식
   ├─ test_parse.py          # Common.Tests.ps1 사례 이식
   ├─ test_manifests.py      # Manifests.Tests.ps1 사례 이식(대상: ../aks-network-validation/manifests)
   └─ test_shell.py          # bash 스크립트 정적 검사(shellcheck 있으면 실행, 없으면 skip)
```

- 매니페스트는 복사하지 않고 `../aks-network-validation/manifests/` 를 참조한다. 업로드 tar 에 함께 넣는다.
- 판정 결과 형식·판정 값(PASS/FAIL/INCONCLUSIVE/INFO)은 기존과 같다.

### 5.5 테스트 매트릭스

T1\~T14 는 기존 설계의 출발지·대상·기대값·판정 규칙을 그대로 쓴다. 바뀌는 것은 **실행 위치**뿐이다.

| 구분 | 기존 | 변경 |
|---|---|---|
| Pod 내부 curl(T4\~T11, T14) | 로컬 kubectl exec | vm-mgmt 의 kubectl exec(Private API 경유) |
| VM curl(T1\~T3) | 로컬 → `az vm run-command` → vm-appgw-sim/vm-other | vm-mgmt → 같은 명령. 관리 ID 로 호출 |
| 스케일(T12\~T13b) | 로컬 kubectl patch·관찰 | vm-mgmt 에서 같은 동작 |

**T15(추가): API 서버 비공개**

| 확인 | 기대 | 판정 |
|---|---|---|
| `az aks show` 의 `apiServerAccessProfile.enablePrivateCluster` | `true` | 조건 |
| vm-mgmt 에서 API 서버 FQDN 해석 | 사설 IP 1개 이상이 `snet-aks-app`(10.50.4.0/22) 안 | 조건 |
| vm-mgmt 에서 `kubectl get --raw /readyz` | `ok` (대조군) | 조건 |
| `az aks show` 의 `apiServerAccessProfile.enablePrivateClusterPublicFqdn` | `false` (AKS 생성 시 `--disable-public-fqdn`, 사용자 결정) | 조건 |
| **로컬**에서 API 서버 FQDN 해석 + TCP 443 접속(`run.sh` 가 verify 후 실행) | 해석 실패(NXDOMAIN) 또는 접속 실패 | 조건 |

판정 우선순위(위에서부터 먼저 적용):
1. FAIL: Private 설정이 `true` 가 아님, public FQDN 이 켜져 있음, vm-mgmt 해석 IP 가 `snet-aks-app` 밖, 또는 로컬에서 공인 IP 로 해석됨.
2. INCONCLUSIVE: 대조군(`/readyz`) 실패, 또는 로컬에서 접속 성공(VPN·피어링 등으로 로컬이 VNet 에 닿는 경우로 간주).
3. PASS: 나머지 조건이 모두 기대대로일 때.

로컬 확인은 `run.sh` 가 수행해 결과 JSON 에 T15 행으로 합친다. Observed 에 로컬 해석 결과(NXDOMAIN/IP)와 접속 결과를 함께 적는다.

### 5.6 사전 점검(로컬 `00-bootstrap.sh` 첫 단계, 기존 Test-Preflight 대응)

관리 VM 의 관리 ID 는 RG 범위 권한만 있어 구독 범위 조회(`vm list-usage`, `vm list-skus`)를 못 하므로, 사전 점검은 로컬에서 vm-mgmt 생성 전에 한다(작성자 판단).

- 구독 이름 출력 후 확인(`--force` 로 생략)
- 할당량: 남은 양(한도 − 현재 사용) ≥ `Standard DSv5 Family` 22, `Standard BSv2 Family` 6(vm-mgmt 포함 3대), Total Regional vCPUs 28
- SKU·영역 제한(`az vm list-skus`)
- 부족하면 생성 전에 중단

### 5.7 리소스 그룹과 재실행

- 기본 RG 는 `rg-aksnetval-linux-krc` 로 PowerShell 판(`rg-aksnetval-krc`)과 분리한다(작성자 판단). 리소스 이름은 기존과 같다.
- 단계는 기존처럼 "이미 있으면 건너뜀"으로 멱등하게 만든다. 단, 기존 AKS 가 있고 `enablePrivateCluster` 가 `true` 가 아니면 즉시 중단한다(공개 → 비공개 제자리 전환 불가).

## 6. 결정 사항 정리

| 항목 | 결정 | 결정 주체 |
|---|---|---|
| 실행 위치 | Linux 관리 VM, bash + az CLI | 사용자 |
| 판정 | Python + pytest | 사용자 |
| 접속 | 공인 IP 없이 run-command | 사용자 |
| AKS | Private Cluster | 사용자 |
| 실행 방식 | A안 | 사용자 |
| 관리 VM OS·크기 | Ubuntu 22.04, `Standard_B2s_v2` | 작성자 판단(기존 테스트 VM 과 동일) |
| 관리 VM 아웃바운드 | `snet-mgmt` 전용 NAT Gateway | 작성자 판단(기본 아웃바운드 은퇴 대응) |
| 로컬 실행 환경 | bash(Git Bash, WSL, Cloud Shell, macOS/Linux) | 작성자 판단 |
| 매니페스트 | 기존 폴더 참조, 복사 안 함 | 작성자 판단(이중 관리 방지) |
| T15 | 추가 | 작성자 판단(Private Cluster 효과 판정) |
| public FQDN | 끔(`--disable-public-fqdn`) | 사용자 |
| 사전 점검 위치 | 로컬 `00-bootstrap.sh` | 작성자 판단(관리 ID 는 RG 범위 권한만) |
| 리소스 그룹 | `rg-aksnetval-linux-krc`, 공개 AKS 발견 시 중단 | 작성자 판단(PowerShell 판과 분리) |
| 기존 PowerShell 판 | 보존, 수정 없음 | 작성자 판단(원본 보존 원칙) |

## 7. 구현자에게 맡기는 것

- bash 세부 구조(함수 분리, 재시도 횟수·대기 시간). 단, `set -euo pipefail` 과 `az` 오류 시 명확한 메시지는 필수.
- 업로드 청크 크기(run-command 스크립트 인자 크기 한도 내에서), 폴링 출력 형식.
- 도구 설치 방법(apt 저장소 vs 설치 스크립트). 설치 버전은 README 에 기록.
- Python 모듈 내부 구조. 단, 판정 함수는 순수 함수(입력 → 판정·사유)로 두고 외부 호출을 하지 않는다.

## 8. 검증 계획

1. pytest 전체 통과(기존 Pester 93개 사례가 모두 대응되어야 함, 대응표를 테스트 파일 주석에 남김).
2. 실제 실행: bootstrap → infra → apply → verify 전체 1회. 결과 T1\~T15, 기대: 18 PASS + T12b INFO.
3. 기존 PowerShell 판 결과(2026-11-20)와 T1\~T14 판정 비교.
4. 정리 후 `az group exists` 가 false 인지 확인.

## 9. 위험과 남은 질문

- `snet-mgmt` 를 NAT Gateway 에 연결해도, 기본 아웃바운드가 없는 VNet 이면 **기존 테스트 VM 의 run-command 결과 반환이 실패**할 수 있다(이번 PowerShell 실행에서는 동작함). 실패하면 테스트 VM 서브넷에도 같은 NAT Gateway 를 연결한다. 이 경우 NSG 경로는 바뀌지 않는다.
- Istio 서비스 메시 애드온과 Private Cluster 조합은 문서상 제한을 찾지 못했지만 실측으로 확인한다.
- (실측 확인) AKS Cilium 기본 설정에서 표준 NetworkPolicy 의 ipBlock 은 노드 IP 에 매칭되지 않아 `0.0.0.0/0` 으로도 노드(kubelet 등) egress 가 막힌다(T22·T23 FAIL). CiliumNetworkPolicy `toEntities: [host, remote-node]` 로 보완해 PASS 를 확인했다.
- run-command 스크립트 인자 크기 한도는 문서에서 확인하지 못했다. 업로드 청크 크기는 구현 중 실측으로 정한다.

## 10. 폴더 통합 (2026-11-20 개정)

- 사용자 요청으로 PowerShell 판(`aks-network-validation/`)을 `aks-network-validation-linux/` 로 합쳤다. 위 2절의 "PowerShell 판 보존"과 4절의 "`../aks-network-validation/manifests/` 참조"는 이 개정으로 대체한다.
- 공통 매니페스트 7개와 Linux 전용 `31-app-pe-egress.yaml` 은 `aks-network-validation-linux/manifests/` 한 곳에 둔다(`manifests-linux/` 폐지). `config.sh` 의 `MANIFESTS_DIR` 은 항상 `<폴더>/manifests` 이고, 번들은 이 폴더를 그대로 넣는다.
- PowerShell 판 스크립트(`*.ps1`, `lib/`, Pester 테스트, README)는 삭제했다. git 이력과 GitHub `evidence/` 의 실측 결과로 남는다. 로컬의 미추적 실행 결과는 `aks-network-validation-linux/results/powershell/` 로 옮겼다.
