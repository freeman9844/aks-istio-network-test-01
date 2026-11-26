# AKS 네트워크 통제·HPA 축소·영역 균형 검증 샘플 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PRD 장표의 NSG·Istio AuthorizationPolicy·NetworkPolicy 통신 규칙, HPA 축소 정책, 영역 균형 오토스케일을 격리된 AKS 샘플에서 재현하고 T1\~T13(+T2c)을 PASS/FAIL/INCONCLUSIVE로 자동 판정한다.

**Architecture:** PowerShell 스크립트 4개(인프라 → 적용 → 검증 → 정리)와 K8s YAML. 판정 로직은 Azure 없이 테스트할 수 있도록 순수 함수 모듈 `lib/Verdict.psm1`로 분리하고 Pester로 TDD한다. Azure·kubectl 호출은 `lib/Common.psm1`에 모으고 실제 환경 실행으로 검증한다.

**Tech Stack:** PowerShell 7, Azure CLI, kubectl, Pester 5, AKS(Azure CNI Overlay + Cilium, Istio 추가 기능), 이미지 `nicolaka/netshoot:v0.13`, `registry.k8s.io/pause:3.9`

**Spec:** `docs/superpowers/specs/2026-11-19-aks-network-control-validation-design.md`

## Global Constraints

- 모든 산출물은 `aks-network-validation/` 아래. `results/`와 kubeconfig는 커밋하지 않는다(`.gitignore`).
- 리전 `koreacentral`, 리소스 그룹 기본값 `rg-aksnetval-krc`, 모든 이름은 `config.ps1`의 `$Cfg`로만 참조.
- CIDR: VNet `10.50.0.0/16`, `snet-ingress` `10.50.1.0/27`, `snet-pe` `10.50.2.0/24`, `snet-appgw-sim` `10.50.3.0/27`, `snet-other` `10.50.3.32/27`, `snet-aks-app` `10.50.4.0/22`, Pod CIDR `192.168.0.0/16`, Ingress ILB IP `10.50.1.10`.
- 노드: `system` `Standard_D4s_v5` 고정 1대(영역 1·2·3, taint `CriticalAddonsOnly=true:NoSchedule`), `userz1`/`userz2`/`userz3` `Standard_D2s_v5` 영역 1/2/3, 오토스케일 1\~3. 세 사용자 풀은 크기·레이블·taint 동일.
- CAS 프로필 `balance-similar-node-groups=true`. HPA: min 5 → 패치 후 2, max 5, CPU 70%, `scaleDown.policies: [{type: Pods, value: 1, periodSeconds: 60}]`, 안정화 기본값.
- 구독 ID·키·토큰을 파일에 쓰지 않는다. VM은 공인 IP 없음, `az vm run-command invoke`로만 명령 실행.
- web·app Service 포트 이름은 `http`(Istio HTTP 인식 필수).
- 판정 값은 `PASS`, `FAIL`, `INCONCLUSIVE` 세 가지만. 대조군 실패 시 차단 테스트는 `INCONCLUSIVE`.
- 문서·출력 메시지는 한국어, 리소스 이름·명령은 영어.

## Review Focus

1. **잘못된 구독·클러스터에서 실행** — 고객/PRD 구독이면 치명적이다. `01-infra.ps1`은 구독 이름을 출력하고 `-Force` 없이 `y` 확인을 받는다. `02`·`03`은 전용 kubeconfig(`results/.kubeconfig`)만 쓰고 현재 컨텍스트를 바꾸지 않는다. → Task 2 Step 6, Task 3 Step 6.
2. **Gateway SA 조회 실패로 빈 principal이 들어간 AuthorizationPolicy** — 모두 거부되어 T1이 엉뚱한 이유로 실패한다. 값이 비면 즉시 중단. → Task 1 `Assert-NotEmpty` 테스트, Task 3 Step 4.
3. **Blob FQDN이 공인 IP로 해석** — PE 경로가 아니므로 T10/T11은 `INCONCLUSIVE`. → Task 1 `Get-TlsVerdict` 테스트.
4. **HPA 샘플의 반복 값 또는 2단계 감소** — 5,5,4,4,3,2는 정상, 5→3 건너뛰기는 FAIL. → Task 1 `Get-HpaVerdict` 테스트.
5. **할당량·SKU 부족으로 Pending** — 사전 점검에서 막고, T13은 Pending이 남으면 FAIL + 사유 `Pending`. → Task 2 Step 2, Task 1 `Get-ZoneBalanceVerdict` 테스트.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `aks-network-validation/config.ps1` | 이름·리전·CIDR·크기 변수만 정의 (`$Cfg`) |
| `aks-network-validation/lib/Verdict.psm1` | 순수 판정 함수 (Azure 호출 없음) |
| `aks-network-validation/lib/Common.psm1` | az/kubectl 래퍼, 대기, kubeconfig, 결과 저장 |
| `aks-network-validation/01-infra.ps1` | 사전 점검 + Azure 리소스 생성 |
| `aks-network-validation/02-apply.ps1` | Istio Gateway 활성화, 매니페스트 적용 |
| `aks-network-validation/03-verify.ps1` | T1\~T13 실행, 결과 출력 (`-Only All\|Network\|Hpa\|Zone`) |
| `aks-network-validation/04-cleanup.ps1` | 리소스 그룹 삭제 |
| `aks-network-validation/manifests/*.yaml` | 네임스페이스, 워크로드, Istio, NetworkPolicy, HPA |
| `aks-network-validation/tests/*.Tests.ps1` | Pester 테스트 |
| `aks-network-validation/README.md` | 실행 순서, 시간·비용, 해석 주의 |

---

### Task 1: 판정 모듈 `Verdict.psm1` + 골격

**Files:**
- Create: `aks-network-validation/.gitignore`, `config.ps1`, `lib/Verdict.psm1`
- Test: `aks-network-validation/tests/Verdict.Tests.ps1`

**Interfaces:**
- Produces (`lib/Verdict.psm1` export, 판정 문자열은 `PASS`/`FAIL`/`INCONCLUSIVE`):
  - `Get-HttpVerdict -Code <string> -Expect <'200'|'403'|'blocked'> [-ControlPassed <bool> = $true]` → `blocked`는 Code `000`(시간 초과·리셋)과 일치. 대조군 실패 시 결과와 무관하게 `INCONCLUSIVE`.
  - `Test-PeIp -Ip <string>` → `[bool]` (10.50.2.0/24 여부)
  - `Get-TlsVerdict -ResolvedIp <string> -Code <string> -Expect <'ok'|'fail'> [-ControlPassed <bool> = $true]` → PE IP가 아니면 `INCONCLUSIVE`; `ok`는 Code ≠ `000`, `fail`은 Code = `000`.
  - `Get-HpaVerdict -Samples <object[]>` (각 `@{Time=[datetime]; Replicas=[int]}`) → `[pscustomobject]@{ Verdict; Sequence=[int[]]; IntervalsSec=[double[]]; Reason }`
  - `Get-ZoneBalanceVerdict -NodesByZone <hashtable> -PendingCount <int>` → `[pscustomobject]@{ Verdict; Reason }`
  - `Assert-NotEmpty -Value <string> -Name <string>` → 비면 `throw "<Name> 값이 비어 있습니다"`
- `config.ps1`: dot-source 시 `$Cfg` 생성. 키: `ResourceGroup, Location, Vnet, Subnets`(`aksApp, ingress, pe, appgwSim, other` 각 `@{Name; Prefix}`), `IngressIp, PodCidr, Aks, SystemVmSize, UserVmSize, UserPools=@('userz1','userz2','userz3'), UserMin=1, UserMax=3, VmSize='Standard_B2s_v2', VmAppgw='vm-appgw-sim', VmOther='vm-other', Storage`('staksnetval' + 구독·RG 기반 해시 6자, 소문자), `ResultsDir`.

- [ ] **Step 1: 사전 준비**

Run: `Install-Module Pester -MinimumVersion 5.5.0 -Scope CurrentUser -Force -SkipPublisherCheck; az aks install-cli` 후 새 셸에서 `kubectl version --client`
Expected: Pester 5.x 사용 가능, kubectl 클라이언트 버전 출력. (현재 환경: Pester 3.4만 있고 kubectl 없음)

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/Verdict.Tests.ps1` (`BeforeAll { Import-Module "$PSScriptRoot/../lib/Verdict.psm1" -Force }`)

```powershell
Describe 'Get-HttpVerdict' {
  It '기대 200, 받음 200 → PASS'  { Get-HttpVerdict -Code '200' -Expect '200' | Should -Be 'PASS' }
  It '기대 403, 받음 200 → FAIL'  { Get-HttpVerdict -Code '200' -Expect '403' | Should -Be 'FAIL' }
  It '차단 기대, 000 → PASS'      { Get-HttpVerdict -Code '000' -Expect 'blocked' | Should -Be 'PASS' }
  It '대조군 실패 → INCONCLUSIVE' { Get-HttpVerdict -Code '000' -Expect 'blocked' -ControlPassed $false | Should -Be 'INCONCLUSIVE' }
}
Describe 'Get-TlsVerdict' {
  It 'PE IP + 000 + fail 기대 → PASS' { Get-TlsVerdict -ResolvedIp '10.50.2.4' -Code '000' -Expect 'fail' | Should -Be 'PASS' }
  It 'PE IP + 400 + ok 기대 → PASS'   { Get-TlsVerdict -ResolvedIp '10.50.2.4' -Code '400' -Expect 'ok' | Should -Be 'PASS' }
  It '공인 IP 해석 → INCONCLUSIVE'    { Get-TlsVerdict -ResolvedIp '20.1.2.3' -Code '000' -Expect 'fail' | Should -Be 'INCONCLUSIVE' }
  It '10.50.20.4는 PE 아님'           { Test-PeIp '10.50.20.4' | Should -BeFalse }
}
Describe 'Get-HpaVerdict' {
  BeforeAll { function S($p){ $t0=[datetime]'2026-01-01'; $p | % { [pscustomobject]@{Time=$t0.AddSeconds($_[0]);Replicas=$_[1]} } } }
  It '5,5,4,4,3,2 / 60초 간격 → PASS' {
    $r = Get-HpaVerdict (S @(@(0,5),@(10,5),@(60,4),@(90,4),@(120,3),@(180,2)))
    $r.Verdict | Should -Be 'PASS'; $r.Sequence | Should -Be @(5,4,3,2) }
  It '간격 30초 → FAIL'  { (Get-HpaVerdict (S @(@(0,5),@(60,4),@(90,3),@(150,2)))).Verdict | Should -Be 'FAIL' }
  It '5→3 건너뜀 → FAIL' { (Get-HpaVerdict (S @(@(0,5),@(60,3),@(120,2)))).Verdict | Should -Be 'FAIL' }
  It '2 미도달 → FAIL, 사유에 2' { $r=Get-HpaVerdict (S @(@(0,5),@(60,4))); $r.Verdict | Should -Be 'FAIL'; $r.Reason | Should -Match '2' }
}
Describe 'Get-ZoneBalanceVerdict' {
  It '2/2/2 → PASS'            { (Get-ZoneBalanceVerdict @{'1'=2;'2'=2;'3'=2} 0).Verdict | Should -Be 'PASS' }
  It '3/2/1 → FAIL'            { (Get-ZoneBalanceVerdict @{'1'=3;'2'=2;'3'=1} 0).Verdict | Should -Be 'FAIL' }
  It '영역 누락은 0으로 → FAIL' { (Get-ZoneBalanceVerdict @{'1'=3;'2'=3} 0).Verdict | Should -Be 'FAIL' }
  It 'Pending 남음 → FAIL+사유' { $r=Get-ZoneBalanceVerdict @{'1'=2;'2'=2;'3'=2} 1; $r.Verdict | Should -Be 'FAIL'; $r.Reason | Should -Match 'Pending' }
}
Describe 'Assert-NotEmpty' {
  It '빈 값이면 throw' { { Assert-NotEmpty -Value '' -Name 'GatewaySA' } | Should -Throw '*GatewaySA*' }
}
```

- [ ] **Step 3: 실패 확인** — Run: `Invoke-Pester aks-network-validation/tests/Verdict.Tests.ps1 -Output Detailed` → 모듈 없음으로 FAIL.
- [ ] **Step 4: 구현** — `lib/Verdict.psm1`, `config.ps1`, `.gitignore`(`results/`). `Get-HpaVerdict`: 연속 중복 제거 → 값이 바뀐 시각 목록 → Sequence가 정확히 `5,4,3,2`이고 바뀐 시각 간격이 모두 ≥55초면 PASS. 영역 균형은 영역 `'1','2','3'` 고정, 없는 영역은 0.
- [ ] **Step 5: 통과 확인** — 같은 명령 → 전부 PASS.
- [ ] **Step 6: 커밋** — `git add aks-network-validation; git commit -m "feat(aksnetval): 판정 모듈과 설정 골격"`

---

### Task 2: 인프라 `01-infra.ps1`

**Files:**
- Create: `aks-network-validation/lib/Common.psm1`, `aks-network-validation/01-infra.ps1`

**Interfaces:**
- Consumes: `$Cfg`
- Produces (`lib/Common.psm1`):
  - `Invoke-Az -Arguments <string[]>` → JSON 출력이면 객체, 실패 시 stderr를 담아 throw
  - `Confirm-Subscription [-Force]` → 구독 이름 출력, `-Force`가 아니면 `y` 입력 요구, 아니면 종료
  - `Test-Preflight -Cfg <hashtable>` → `Standard DSv5 Family` 잔여 vCPU ≥ 22, `az vm list-skus -l koreacentral`에서 `Standard_D2s_v5`·`Standard_D4s_v5`가 영역 1·2·3에 제한 없음. 부족하면 무엇이 부족한지 담아 throw.
  - `Get-AksKubeconfig -Cfg <hashtable>` → `az aks get-credentials --file results/.kubeconfig --overwrite-existing` 후 경로 반환
- 리소스 생성 전 `az <type> show`로 확인하고 없을 때만 생성(재실행 안전).

- [ ] **Step 1: `Common.psm1` 작성** (위 4개 함수).
- [ ] **Step 2: 사전 점검** — Run (작업 폴더 `aks-network-validation`): `. ./config.ps1; Import-Module ./lib/Common.psm1; Test-Preflight $Cfg` → 할당량·SKU 요약 출력, 예외 없음. 부족하면 여기서 멈추고 사용자에게 보고.
- [ ] **Step 3: `01-infra.ps1` 작성** — 순서: `Confirm-Subscription` → `Test-Preflight` → RG → VNet·서브넷 5개 → NSG 2개와 설계 5.3 규칙(`nsg-aks-app` 100 Allow `10.50.3.0/27`→`10.50.1.10` TCP 80, 110 Deny `10.50.3.32/27`→`10.50.1.10` TCP 80; `nsg-ingress` 100 Deny `10.50.3.0/27`→`*` `*`) 서브넷 연결 → 사용자 할당 관리 ID + VNet에 `Network Contributor` → Storage(`--public-network-access Disabled`) + Blob PE(`snet-pe`) + `privatelink.blob.core.windows.net` 영역·VNet 링크·zone group → VM 2대(`Ubuntu2204`, `--public-ip-address '""'`, `--nsg '""'`) → AKS(`--network-plugin azure --network-plugin-mode overlay --network-dataplane cilium --pod-cidr 192.168.0.0/16 --vnet-subnet-id <snet-aks-app> --assign-identity <ID> --enable-azure-service-mesh --tier free --nodepool-name system --node-count 1 --node-vm-size Standard_D4s_v5 --zones 1 2 3 --nodepool-taints CriticalAddonsOnly=true:NoSchedule --cluster-autoscaler-profile balance-similar-node-groups=true`) → `userz1~3`(`--mode User --zones <n> --node-vm-size Standard_D2s_v5 --enable-cluster-autoscaler --min-count 1 --max-count 3`, 레이블·taint 없음).
- [ ] **Step 4: 실행** — `./01-infra.ps1` (약 20\~25분) → 종료 코드 0.
- [ ] **Step 5: 결과 확인**
  - `az aks show ... --query "{b:autoScalerProfile.balanceSimilarNodeGroups, mesh:serviceMeshProfile.mode}"` → `b: "true"`, `mesh: "Istio"`
  - `az aks nodepool list ... --query "[].{n:name,z:availabilityZones,s:vmSize,min:minCount,max:maxCount}" -o table` → 설계 5.2와 일치
  - Blob PE NIC IP가 `10.50.2.x`
- [ ] **Step 6: 재실행·구독 확인** — `./01-infra.ps1 -Force` 재실행 → "이미 있음"만 출력, 종료 코드 0. `-Force` 없이 실행해 `n` 입력 → 아무것도 만들지 않고 종료.
- [ ] **Step 7: 커밋** — `git commit -m "feat(aksnetval): 인프라 생성 스크립트"`

---

### Task 3: 매니페스트 + `02-apply.ps1`

**Files:**
- Create: `manifests/00-namespaces.yaml`, `10-web.yaml`, `11-app.yaml`, `12-nomesh.yaml`, `20-istio.yaml`, `30-netpol.yaml`, `40-scale.yaml`, `02-apply.ps1` (모두 `aks-network-validation/` 아래)
- Test: `aks-network-validation/tests/Manifests.Tests.ps1`

**Interfaces:**
- Consumes: `Get-AksKubeconfig`, `Assert-NotEmpty`, `$Cfg`
- Produces: 네임스페이스 `web`·`app`(`istio.io/rev=<rev>`), `nomesh`, `scale`; Deployment·SA·Service `web-a`, `web-b`, `bff-a`, `bff-b`(포트 8080, 이름 `http`), `nomesh/probe`; `scale/hpa-demo`(+HPA), `scale/zone-fill`(replicas 0); Gateway ILB `10.50.1.10`. Istio Gateway Host `web-a.test`, `web-b.test`.
- `20-istio.yaml`의 자리표시자 `__GATEWAY_PRINCIPAL__` (`cluster.local/ns/aks-istio-ingress/sa/<sa>`로 치환). 그 밖의 값은 정적.

이미지 결정: web/app/probe는 `nicolaka/netshoot:v0.13`(curl·nc·nslookup 포함), 명령 `socat TCP-LISTEN:8080,fork,reuseaddr SYSTEM:"printf 'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok'"`. `hpa-demo`·`zone-fill`은 `registry.k8s.io/pause:3.9`. `hpa-demo` requests cpu `100m`; `zone-fill` requests cpu `800m` + `nodeSelector kubernetes.azure.com/mode: user`, 분산 제약 없음. 업무 Deployment에는 `topologySpreadConstraints`(`topology.kubernetes.io/zone`, `maxSkew: 1`, `ScheduleAnyway`).

- [ ] **Step 1: 실패하는 정적 테스트** — `tests/Manifests.Tests.ps1`, 파일 텍스트 검사:
  - `10-web.yaml`·`11-app.yaml`: `kind: Service` 수와 `name: http` 수가 같음
  - 모든 `kind: Deployment` 파일에 Deployment 수만큼 `serviceAccountName:`
  - `40-scale.yaml`: `minReplicas: 5`, `maxReplicas: 5`, `type: Pods`, `value: 1`, `periodSeconds: 60`, `averageUtilization: 70`, `cpu: 800m`
  - `30-netpol.yaml`: `name: deny-web-to-data`, `namespace: web`, `except:` 뒤 `10.50.2.0/24`
  - `20-istio.yaml`: `cluster.local/ns/web/sa/web-a`, `cluster.local/ns/web/sa/web-b`, `mode: STRICT` 2회, `__GATEWAY_PRINCIPAL__`, `istio: aks-istio-ingressgateway-internal`
- [ ] **Step 2: 실패 확인** — `Invoke-Pester aks-network-validation/tests/Manifests.Tests.ps1` → FAIL.
- [ ] **Step 3: 매니페스트 작성**(설계 5.4\~5.7 그대로) → 같은 명령 PASS.
- [ ] **Step 4: `02-apply.ps1` 작성** — `Get-AksKubeconfig` → 모든 kubectl에 `--kubeconfig` → `az aks mesh enable-ingress-gateway --ingress-gateway-type internal` → `aks-istio-ingress/aks-istio-ingressgateway-internal` Service에 `service.beta.kubernetes.io/azure-load-balancer-internal-subnet=snet-ingress`, `service.beta.kubernetes.io/azure-load-balancer-ipv4=10.50.1.10` 어노테이션 → 리비전(`serviceMeshProfile.istio.revisions[0]`)으로 네임스페이스 레이블 → Gateway Pod `spec.serviceAccountName` 조회 → `Assert-NotEmpty` → 치환 적용 → 나머지 적용 → 모든 Deployment `rollout status --timeout 5m`.
- [ ] **Step 5: 실행·확인**
  - `./02-apply.ps1` → 종료 코드 0
  - Gateway Service `status.loadBalancer.ingress[0].ip` = `10.50.1.10`. 10분 뒤 어노테이션이 남아 있는지 다시 확인(추가 기능이 덮어쓰는지 확인). 사라지면 멈추고 보고.
  - `web`·`app` Pod 컨테이너 2개(사이드카), `nomesh/probe` 1개
  - 업무 Pod가 `userz*` 노드에만 있음(`kubectl get pod -A -o wide`)
- [ ] **Step 6: 컨텍스트 미변경 확인** — 실행 전후 `kubectl config current-context` 결과 동일.
- [ ] **Step 7: 커밋** — `git commit -m "feat(aksnetval): 매니페스트와 적용 스크립트"`

---

### Task 4: 네트워크 검증 T1\~T11 (`03-verify.ps1 -Only Network`)

**Files:**
- Create: `aks-network-validation/03-verify.ps1`
- Modify: `aks-network-validation/lib/Common.psm1`

**Interfaces:**
- Consumes: `Get-HttpVerdict`, `Get-TlsVerdict`, `Get-AksKubeconfig`
- Produces (`Common.psm1`):
  - `Invoke-VmCurl -Vm <string> -Url <string> [-HostHeader <string>] [-Insecure]` → `[string]` HTTP 코드 (`curl -s -o /dev/null -w '%{http_code}' -m 10`, 실패 시 `000`; run-command 출력에서 코드만 파싱)
  - `Invoke-PodCurl -Namespace <string> -Deploy <string> -Url <string> [-Insecure]` → `[string]` HTTP 코드 (앱 컨테이너에서 `kubectl exec`)
  - `Resolve-PodDns -Namespace <string> -Deploy <string> -Fqdn <string>` → `[string]` IP
  - `Save-Results -Results <object[]>` → `results/<yyyyMMdd-HHmmss>.json`·`.md` 작성 + 콘솔 표
- 결과 객체: `[pscustomobject]@{ Id; From; To; Expect; Observed; Verdict; Note }`

- [ ] **Step 1: 골격** — 매개변수 `-Only <'All','Network','Hpa','Zone'> = 'All'`, kubeconfig 준비, 마지막에 `Save-Results`.
- [ ] **Step 2: Network 블록** — 대조군 먼저 실행: T1, T2c, T4, T6, T11 → 차단 테스트 T2, T5, T7, T8, T9, T10에 설계 6장 표의 대조군 결과 전달(여러 대조군이면 모두 PASS여야 함). T3은 T1 재사용 + Note "`nsg-ingress` Deny 존재 상태". T2c는 VM에서 Blob FQDN `-Insecure` 접속 코드 ≠ `000`. T10/T11은 `Resolve-PodDns` 후 `Get-TlsVerdict`. T8 기대 `blocked`.
- [ ] **Step 3: 실행** — `./03-verify.ps1 -Only Network` → 콘솔 12행(T1\~T11, T2c), 결과 파일 2개.
- [ ] **Step 4: 기대 결과** — 모두 `PASS`. `FAIL`/`INCONCLUSIVE`가 있으면 원인을 조사하되(설계 가정이 틀렸을 수 있음, 예: T10에서 Envoy 업스트림이 NetworkPolicy 적용을 받는지) 기대값을 바꾸지 말고 사용자에게 보고.
- [ ] **Step 5: 판정이 실제로 움직이는지 확인** — `kubectl delete networkpolicy deny-web-to-data -n web` 후 재실행 → T10만 `FAIL`. `30-netpol.yaml` 다시 적용 후 재실행 → 다시 전부 `PASS`.
- [ ] **Step 6: 커밋** — `git commit -m "feat(aksnetval): 네트워크 통제 검증 T1-T11"`

---

### Task 5: HPA·영역 균형 검증 T12·T13

**Files:**
- Modify: `aks-network-validation/03-verify.ps1`

**Interfaces:**
- Consumes: `Get-HpaVerdict`, `Get-ZoneBalanceVerdict`, `Save-Results`

- [ ] **Step 1: Hpa 블록** — `hpa-demo` Ready 5 대기 → `kubectl patch hpa hpa-demo -n scale --type merge -p '{"spec":{"minReplicas":2}}'` → 5초 간격으로 `deploy/hpa-demo`의 `.spec.replicas`와 시각 기록, 2 도달 또는 15분 → `Get-HpaVerdict`. Note에 패치\~첫 감소 초(판정 외). 끝나면 `minReplicas` 5로 원복.
- [ ] **Step 2: Zone 블록** — 설정 확인(`balanceSimilarNodeGroups == "true"`, 풀 영역 1·2·3; 아니면 `INCONCLUSIVE`) → `zone-fill` replicas 6 → 15초 간격, 모든 Pod Running 또는 15분 → `kubectl get nodes -l kubernetes.azure.com/mode=user -L topology.kubernetes.io/zone` 집계 → `Get-ZoneBalanceVerdict`. `TriggeredScaleUp` 이벤트 메시지를 Note에 보조 기록. `zone-fill` 0으로 원복.
- [ ] **Step 3: 실행** — `./03-verify.ps1 -Only Hpa` (약 5\~8분) → T12 `PASS`, Sequence `5,4,3,2`.
- [ ] **Step 4: 실행** — `./03-verify.ps1 -Only Zone` (약 10\~15분) → T13 `PASS`, 영역별 2/2/2.
- [ ] **Step 5: 전체 실행** — `./03-verify.ps1` → 14행, 결과 파일 생성.
- [ ] **Step 6: 커밋** — `git commit -m "feat(aksnetval): HPA 축소와 영역 균형 검증"`

---

### Task 6: 정리 스크립트 + README

**Files:**
- Create: `aks-network-validation/04-cleanup.ps1`, `aks-network-validation/README.md`

- [ ] **Step 1: `04-cleanup.ps1`** — 리소스 그룹 이름을 보여 주고 같은 이름을 입력해야 `az group delete --yes --no-wait`; `results/.kubeconfig` 삭제.
- [ ] **Step 2: README** — 사전 준비(Pester 5, kubectl), 실행 순서 01→02→03→04, 소요 시간(인프라 \~25분, 검증 전체 \~30분), 최대 vCPU 22, 결과 해석. 주의: (1) T12는 축소 속도 정책만 검증하며 안정화 창(300초) 근거로 쓰지 말 것, (2) T13은 확장만 판정하고 축소는 관찰, (3) 영역 레이블이 다른 풀을 유사 그룹으로 보는지는 이 샘플 결과로 확인, (4) 시스템 노드 1대는 검증 전용이며 PRD는 전용 `ingress` 풀 권장.
- [ ] **Step 3: 정리 실행** — `./04-cleanup.ps1` 후 몇 분 뒤 `az group exists -n rg-aksnetval-krc` → `false`.
- [ ] **Step 4: 커밋** — `git commit -m "docs(aksnetval): 정리 스크립트와 README"`
