# AKS 네트워크 통제 검증 — Linux 관리 VM·az CLI 실행판

AIPortal PRD 의 AKS 네트워크 통제(인바운드 경로, Istio 인가, NetworkPolicy, Private Endpoint egress, 영역 분산·축소, API 서버 비공개)를
**Private Cluster** 에서 재현하고 판정합니다. 리소스 그룹 안의 Linux 관리 VM(`vm-mgmt`)에서 bash + az CLI 로 실행합니다.
매니페스트는 `manifests/` 에 모두 있습니다. 처음 만든 PowerShell 판은 이 폴더로 합치면서 삭제했으며 git 이력에 남아 있습니다.

- 로컬(WSL 또는 Cloud Shell)은 부트스트랩·업로드·진행 로그 조회·결과 회수만 합니다.
- 실제 생성·적용·검증은 `vm-mgmt` 안에서 `az vm run-command` + `systemd-run` 으로 백그라운드 실행합니다.
- 판정은 Python(`aksnetval/`, 표준 라이브러리만)으로 하고 결과는 `results/<시각>.json`·`.md` 로 남습니다.

## 구조

```
vnet-aksnetval 10.50.0.0/16 (koreacentral)
├─ snet-aks-app   10.50.4.0/22   Private AKS aks-aksnetval (Azure CNI Overlay + Cilium, Istio 애드온)
│                                 system 풀 + userz1/userz2/userz3(영역 1/2/3, CAS 1~3)
├─ snet-ingress   10.50.1.0/27   Istio 내부 Gateway ILB 10.50.1.10, NSG nsg-ingress
├─ snet-pe        10.50.2.0/24   Blob PE  10.50.2.4 (staksnetval…)   ← bff-b 만 허용
│                                 Blob2 PE 10.50.2.5 (staksnetvalb…)  ← bff-a 만 허용
├─ snet-appgw-sim 10.50.3.0/27   VM vm-appgw-sim (App Gateway 출발지 모사)
├─ snet-other     10.50.3.32/27  VM vm-other (허용되지 않은 출발지)
└─ snet-mgmt      10.50.3.64/27  VM vm-mgmt (관리 ID, 공인 IP 없음) + NAT Gateway nat-mgmt
```

```
[로컬 bash]                                    [vm-mgmt, root, /opt/aksnetval]
00-bootstrap.sh  사전 점검·RG·VNet·snet-mgmt·NAT·vm-mgmt·역할·도구 설치
run.sh <단계>   ─ 코드 번들 업로드(sha256 확인) → systemd-run 으로 시작 → 로그 폴링 → 결과 회수
                                               01-infra.sh   NSG·Storage 2·PE 2(고정 IP)·VM 2·Private AKS·풀
                                               02-apply.sh   Istio 내부 Gateway, 매니페스트, NetworkPolicy
                                               03-verify.sh  TN1~TN22·TS1~TS5 → results/<시각>.json·.md
99-cleanup.sh    로컬에서 RG 삭제
```

`vm-mgmt` 관리 ID 권한은 RG 범위 `Contributor` 와, **Network Contributor 만 할당 가능하도록 조건을 건**
`Role Based Access Control Administrator` 입니다. RG 생성·삭제는 로컬에서만 합니다.

## 사전 요구

- bash 환경: WSL(Ubuntu) 또는 Azure Cloud Shell. `az`, `python3`(3.10 이상), `jq`, `curl`, `base64`, `sha256sum`
- `az login` 후 대상 구독 선택. 구독에서 RG 생성과 역할 할당이 가능해야 합니다(Owner, 또는 Contributor + User Access Administrator).
- 할당량(koreacentral): Standard DSv5 Family 22, Standard Bsv2 Family 6, Total Regional vCPUs 28 이상 잔여. 부트스트랩이 먼저 확인합니다.
- RG 이름을 바꾸려면 `export AKSNETVAL_RG=<이름>` (기본 `rg-aksnetval-linux-krc`).

## 실행 순서

```bash
cd aks-network-validation-linux
local/00-bootstrap.sh --force      # 구독 확인 프롬프트 생략. 대화형이면 --force 없이 실행
local/run.sh infra                 # 약 25분(AKS·노드 풀 생성)
local/run.sh apply                 # 약 5분
local/run.sh verify                # 약 110분(TS5 의 CAS 축소 대기 약 40분 + TS3 축소 6회 약 70분)
```

- 모든 단계는 멱등입니다. 실패하면 원인을 고친 뒤 **그 단계만** 다시 실행합니다.
- 같은 단계가 이미 VM 에서 실행 중이면 새로 시작하지 않고 기존 실행의 진행만 따라갑니다.
- `--only all|network|hpa|floor|zone|spread`: 검증 일부만 실행합니다(`network` 는 TN1~TN22, 약 6분).
- `--poll <초>`: 진행 로그 조회 간격(기본 30초).

## 테스트 목록

검증 ID는 네트워크 검증 `TN#`, 스케일링 검증 `TS#` 로 나눕니다. 이전 번호와의 대응은 다음과 같습니다.

| 이전 | 현재 |
|---|---|
| T1·T2·T2c·T3 | TN1·TN2·TN3·TN4 |
| T4~T11 | TN5~TN12 |
| T14·T15 | TN13·TN14 |
| T16~T23 | TN15~TN22 |
| T12·T12b·T12c·T13·T13b | TS1·TS2·TS3·TS4·TS5 |

| Id | 출발지 → 대상 | 기대 | 확인하는 통제 |
|---|---|---|---|
| TN1 | vm-appgw-sim → Gateway ILB(Host web-a.test) | 200 | 허용된 인바운드 경로 |
| TN2 | vm-other → Gateway ILB | 000 | NSG 로 비허용 출발지 차단 |
| TN3 | vm-other → Blob PE | ≠000 | TN2 대조군(vm-other 자체 통신 정상) |
| TN4 | vm-appgw-sim → Gateway ILB | 200 | NSG Deny 규칙이 있어도 허용 경로 유지 |
| TN5·TN7 | web-a → bff-a, web-b → bff-b | 200 | Istio AuthorizationPolicy 허용 |
| TN6·TN8 | web-a → bff-b, web-b → bff-a | 403 | Istio 교차 호출 차단 |
| TN9 | nomesh/probe → bff-a | 000 | mTLS STRICT(메시 밖 차단) |
| TN10 | web-a → web-b(Ingress 우회) | 403 | web 간 직접 호출 차단 |
| TN11 | web-a → Blob PE | 000 | web 네임스페이스 PE egress 차단 |
| TN12 | bff-b → Blob PE(10.50.2.4) | ≠000 | bff-b 전용 PE 허용 |
| TN13 | kubelet readinessProbe | Ready + 15020 rewrite | Istio probe rewrite |
| TN14 | vm-mgmt + 로컬 → API 서버 | 사설 IP·로컬 접근 불가 | Private Cluster |
| TN15 | bff-a → Blob PE(10.50.2.4) | 000 | 다른 Pod 의 PE 는 차단(대조군 TN12) |
| TN16 | bff-a → Blob2 PE(10.50.2.5) | ≠000 | bff-a 전용 PE 허용 |
| TN17 | bff-b → Blob2 PE(10.50.2.5) | 000 | 다른 Pod 의 PE 는 차단(대조군 TN16) |
| TN18 | bff-b → CoreDNS | 해석 성공 | 기본 egress 유지(DNS) |
| TN19 | bff-b → 15021 ready | 200 | 기본 egress 유지(istiod xDS). Pod 가 정책 생성 이후 기동했을 때만 근거로 인정 |
| TN20 | bff-b → mcr.microsoft.com | ≠000 | 기본 egress 유지(인터넷) |
| TN21 | bff-b → 자기 노드 kubelet(:10250) | ≠000 | 기본 egress 유지(노드, Cilium host 엔터티). 대조군 nomesh/probe |
| TN22 | bff-b → 다른 노드 kubelet(:10250) | ≠000 | 기본 egress 유지(노드, Cilium remote-node 엔터티). 대조군 nomesh/probe |
| TS1 | hpa-demo minReplicas 5→2 | 1분에 1개씩 축소 | HPA scaleDown 정책 |
| TS2 | hpa-demo 축소 후 영역 분포 | 기록 | (판정 없음) Pod 2개라 빈 영역 1개는 필연 |
| TS3 | hpa-demo 5·9·12개 → minReplicas 3, 각 2회 | 삭제된 Pod = ReplicaSet 삭제 규칙 예측 | 축소 때 어떤 Pod 가 지워지는지(영역 균등은 보장 대상 아님) |
| TS4 | zone-fill 6개 | 영역별 노드 차이 ≤1 | CAS 영역 균형 확장 |
| TS5 | zone-spread 6개(DoNotSchedule, minDomains 3) | Pod 2/2/2, Pending 없음 | 영역 강제 분산 + CAS |

### app 네임스페이스 PE egress 분리(TN15~TN22)

`manifests/31-app-pe-egress.yaml` 의 NetworkPolicy 3개와 CiliumNetworkPolicy 1개로 구성합니다. 표준 NetworkPolicy 는 허용 규칙의 **합집합**이므로,
공통 정책에서 PE 서브넷만 빼고 Pod 별로 필요한 PE IP 만 다시 허용합니다.

| 정책 | 대상 Pod | egress 허용 |
|---|---|---|
| `app-egress-default` | app 네임스페이스 전체 | 클러스터 내부(`namespaceSelector: {}`) + `0.0.0.0/0` except `10.50.2.0/24` |
| `allow-bff-b-to-blob` | `app=bff-b` | `10.50.2.4/32` TCP 443 |
| `allow-bff-a-to-blob2` | `app=bff-a` | `10.50.2.5/32` TCP 443 |
| `app-egress-nodes`(CiliumNetworkPolicy) | app 네임스페이스 전체 | `toEntities: [host, remote-node]` (노드 IP) |

**노드 egress(TN21·TN22)**: AKS Azure CNI powered by Cilium 은 기본 설정(`policy-cidr-match-mode` 미설정)에서 표준 NetworkPolicy 의 ipBlock 을 Pod·노드 IP 에 매칭하지 않습니다([Cilium 문서](https://docs.cilium.io/en/stable/network/kubernetes/policy/)).
그래서 `0.0.0.0/0` 을 허용해도 노드 IP(kubelet, hostNetwork Pod, NodePort 등)로 가는 연결은 막힙니다. 실측 결과는 다음과 같습니다.

| 조건 | bff-b → 노드 :10250 | 대조군 nomesh/probe |
|---|---|---|
| NetworkPolicy 3개만 | 000(차단) | 401 |
| + NetworkPolicy ipBlock `10.50.4.0/24` | 000(차단) | 401 |
| + CiliumNetworkPolicy `toEntities: [host, remote-node]` | 401(연결) | 401 |

- PE 는 고정 IP(`--ip-config … private-ip-address`)로 만들고, 생성 후 NIC 의 실제 IP 가 기대값과 다르면 중단합니다.
- TN11·TN12·TN15~TN17 은 DNS 가 기대 PE IP 로 해석될 때만 판정합니다(다르면 INCONCLUSIVE).
- `02-apply.sh` 는 정책 적용 후 다음 초로 넘긴 뒤 app 을 재시작합니다. 타임스탬프가 초 단위라 같은 초에 재시작하면 TN19 근거가 성립하지 않기 때문입니다.

### TN14: API 서버 비공개

`enablePrivateCluster=true`, `enablePrivateClusterPublicFqdn=false`, vm-mgmt 에서 FQDN 이 `snet-aks-app` 의 사설 IP 로 해석되고
`/readyz` 가 `ok`(대조군), 로컬에서는 해석 실패 또는 접속 실패여야 PASS 입니다. 로컬 확인은 `run.sh verify` 가 끝난 뒤 로컬에서 수행해 결과에 합칩니다.
로컬이 VPN·피어링으로 VNet 에 닿아 접속에 성공하면 INCONCLUSIVE 입니다.

## 최신 결과(2026-11-24, koreacentral)

**26 PASS + TS2 INFO.** 검증 ID를 TN#·TS#로 다시 매긴 뒤 새 클러스터에서 전체(`local/run.sh verify`, `20261124-061859`)를 다시 실행한 결과입니다.

| Id | From | To | Observed | Verdict | Note |
|---|---|---|---|---|---|
| TN1 | vm-appgw-sim | 10.50.1.10:80 Host web-a.test | 200 | PASS |  |
| TN2 | vm-other | 10.50.1.10:80 Host web-a.test | 000 | PASS | curl rc=28 |
| TN3 | vm-other | Blob PE :443 | 400 | PASS | TLS 연결 성공 |
| TN4 | vm-appgw-sim | 10.50.1.10:80 | 200 | PASS | Deny 규칙과 서브넷 연결 확인, TN1 재사용 |
| TN5 | web/web-a | bff-a:8080 | 200 | PASS |  |
| TN6 | web/web-a | bff-b:8080 | 403 | PASS |  |
| TN7 | web/web-b | bff-b:8080 | 200 | PASS |  |
| TN8 | web/web-b | bff-a:8080 | 403 | PASS |  |
| TN9 | nomesh/probe | bff-a:8080 | 000 | PASS | curl rc=56 |
| TN10 | web/web-a | web-b:8080 | 403 | PASS |  |
| TN11 | web/web-a | Blob (10.50.2.4) | 000 | PASS | DNS=10.50.2.4, rc=28 |
| TN12 | app/bff-b | Blob (10.50.2.4) | 400 | PASS | DNS=10.50.2.4 |
| TN13 | kubelet | readinessProbe 4개 | Ready 4/4, rewrite 4/4 | PASS |  |
| TN14 | vm-mgmt + 로컬 | API 서버 :443 | private=True, publicFqdn=False, vm=10.50.4.4, readyz=ok, local=NXDOMAIN | PASS |  |
| TN15 | app/bff-a | Blob (10.50.2.4) | 000 | PASS | DNS=10.50.2.4, rc=28 |
| TN16 | app/bff-a | Blob2 (10.50.2.5) | 400 | PASS | DNS=10.50.2.5 |
| TN17 | app/bff-b | Blob2 (10.50.2.5) | 000 | PASS | DNS=10.50.2.5, rc=28 |
| TN18 | app/bff-b | CoreDNS | 10.0.0.1 | PASS |  |
| TN19 | app/bff-b | istiod(:15021 ready) | 200 | PASS | 정책 생성 이후 기동한 Pod |
| TN20 | app/bff-b | mcr.microsoft.com:443 | 200 | PASS |  |
| TN21 | app/bff-b | 자기 노드 :10250 (kubelet) | 401 | PASS | 대조군 nomesh/probe=401 |
| TN22 | app/bff-b | 다른 노드 :10250 (kubelet) | 401 | PASS | 대조군 nomesh/probe=401 |
| TS1 | — | hpa-demo minReplicas 5→2 | 5,4,3,2 (61·61초 간격) | PASS |  |
| TS2 | — | hpa-demo 영역 분포 | 1/2/2 → 0/1/1 | INFO | Pod 2개·영역 3개 |
| TS3 | — | hpa-demo 5·9·12개 → minReplicas 3, 각 2회 | 2/2/1·3/3/3·4/4/4 → 6회 모두 1/1/1 | PASS | 삭제 34건 모두 ReplicaSet 삭제 규칙 예측과 일치(로그 구간 경계·완전 동률로 후보 2개 이상 26건, 한 번에 2개 삭제 1회). 1/1/1 은 영역당 노드 1개에서 노드 혼잡 규칙이 작동한 결과이며, 축소 뒤 영역 균등은 보장되지 않음 |
| TS4 | — | userz1/2/3 | 노드 1/1/1 → 2/2/2 | PASS | balanceSimilarNodeGroups=true, Running 6/6, 124초 |
| TS5 | — | zone-spread 6개 | Pod 2/2/2, 노드 1/1/1 → 2/2/2 | PASS | Running 6/6, 126초 |

## 문제 해결

| 증상 | 원인·조치 |
|---|---|
| WSL 에서 `login.microsoftonline.com` 이 `Network is unreachable`/000 | Global Secure Access 등 사내 클라이언트가 합성 IP(예: 6.6.0.2)를 돌려주는데 WSL 트래픽은 그 터널을 타지 않습니다. 네트워크 관리자와 협의해 WSL 경로를 열거나, 승인된 범위에서 WSL `/etc/hosts` 를 임시 조정하고 작업 후 원복합니다. |
| WSL 재시작 후 `/etc/hosts` 조정이 사라짐 | WSL 이 hosts 를 다시 생성합니다. `/etc/wsl.conf` 에 `[network]` `generateHosts=false` 를 넣으면 유지됩니다. |
| TN21·TN22 FAIL(대조군은 401) | 노드 IP 로 가는 egress 가 막힌 상태입니다. `app-egress-nodes`(CiliumNetworkPolicy)가 적용됐는지 확인합니다(`kubectl get cnp -n app`). |
| `AADSTS50173` | WSL 의 az 토큰 만료. `az login --use-device-code` 로 다시 로그인합니다. |
| 비대화형 실행에서 부트스트랩이 멈춤 | 구독 확인 프롬프트(`read -p`) 때문입니다. `--force` 를 붙입니다. |
| TN19 INCONCLUSIVE "Pod 가 정책 적용 전에 기동" | `local/run.sh apply` 를 다시 실행해 app Pod 를 정책 이후에 재기동합니다. |

## 정리

```bash
local/99-cleanup.sh            # RG 이름을 입력해 확인. --force 로 생략
az group exists -n rg-aksnetval-linux-krc   # false 가 될 때까지 확인
```

## 개발

```bash
python3 -m pytest -q           # 판정·보고·셸 정적 검사·가짜 az/kubectl 로 verify 흐름 검사
```
