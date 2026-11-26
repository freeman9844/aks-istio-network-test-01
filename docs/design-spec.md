# AKS 네트워크 통제·HPA 축소·영역 균형 기능 검증 샘플 설계

- 작성일: 2026-11-19
- 대상 설계: AIPortal 지식허브 PRD 아키텍처 v0.4의 "PRD 네트워크 — 3-Tier·서브넷·통신 규칙" 장표
- 상태: 승인됨 (2026-11-19)

## 1. 의도와 배경

PRD 장표에는 NSG, Istio AuthorizationPolicy, K8s NetworkPolicy로 Tier 간 통신을 통제한다고 설계되어 있다.
이 설계가 AKS에서 **실제로 의도대로 허용·차단되는지**를 가장 작은 샘플 구성으로 재현해 증명한다. 아울러 다음 두 가지도 함께 검증한다.
- HPA 축소 정책(`scaleDown.policies: Pods 1 / 60s`)이 **5개에서 최소 2개까지 1분에 1개씩** 줄이는지
- 영역별 사용자 노드 풀 3개(영역 1·2·3)와 `balance-similar-node-groups=true` 설정에서 **노드가 영역마다 고르게 늘어나는지** (자원 제약으로 사용자 노드는 `Standard_D2s_v5`로 축소, 사용자 결정)

용도는 아키텍처 설계에 대한 기능 검증이다(사용자 답변). 성능·운영성 검증이 아니다.

## 2. 목표 / 비목표 / 반목표

**목표**
- 아래 장표 규칙을 샘플로 재현하고, 각 규칙의 허용·차단 결과를 스크립트가 PASS/FAIL로 자동 판정한다.
  - `allow-appgw-to-ingress` (NSG) — App Gateway는 테스트 VM으로 대체
  - `allow-ingress-to-web` (Istio AuthorizationPolicy)
  - `allow-web-to-own-bff` (Istio AuthorizationPolicy + STRICT mTLS)
  - `deny-web-to-data` (NetworkPolicy)
  - `allow-app-to-data` (차단 정책이 없을 때 허용됨을 확인)
- 대화에서 도출한 보조 사실도 함께 증명한다.
  - LB IP가 있는 `snet-ingress`에 건 NSG는 Ingress 트래픽을 막지 못한다.
  - 사이드카가 없는 Pod는 STRICT mTLS 때문에 BFF를 호출할 수 없다.
- HPA: 5 → 4 → 3 → 2로 단계마다 1개씩, 약 60초 간격으로 줄어드는지 판정한다.
- 노드: 영역 1·2·3에 하나씩 고정한 사용자 노드 풀 3개에서, CAS 확장 시 영역 간 노드 수 차이가 1 이하로 유지되는지 판정한다.

**비목표**
- Azure Firewall, 실제 App Gateway, ExpressRoute, Databricks, DEV Spoke 피어링(`egress-via-firewall`, `allow-adapter-to-onprem`, `allow-app-to-databricks`, `deny-dev-to-prd`)
- KEDA, Gateway API 방식 Ingress, NAP, 성능 측정
- CAS **축소** 시 영역 균형(공식적으로 보장되지 않는 동작이므로 판정하지 않고 관찰만 기록)

**반목표 (기술적으로 돌아가도 실패로 보는 것)**
- 차단 테스트가 "다른 이유"(DNS 오류, 잘못된 IP, Pod 미기동)로 실패했는데 PASS로 판정되는 것. → 모든 차단 테스트에는 같은 대상에 대한 **허용 대조군**이 있어야 하며, 대조군이 실패하면 차단 테스트는 INCONCLUSIVE로 판정한다.
- 고객 구독·PRD 환경에서 실행하는 것.
- 검증 후 리소스가 남아 과금되는 것.

## 3. 제약

- 실행 환경: Windows PowerShell 7, Azure CLI, kubectl. 현재 `az login` 컨텍스트의 구독을 사용하고 구독 ID를 파일에 기록하지 않는다.
- 리전: Korea Central(가용성 영역 1·2·3 사용). 전용 리소스 그룹 하나에 모든 리소스를 만든다(정리는 그룹 삭제로).
- 구독의 `Standard DSv5 Family` vCPU 할당량: 최대 노드 수 기준 약 22 vCPU 필요(시스템 `D4s_v5` 1대 4 vCPU + 사용자 `D2s_v5` 최대 9대 × 2 vCPU). 스크립트 시작 시 할당량과 Korea Central 영역 1·2·3의 `Standard_D2s_v5`·`Standard_D4s_v5` 사용 가능 여부(`az vm list-skus`의 제한 정보)를 확인한다.
- 비밀정보(키, 토큰)를 저장소에 남기지 않는다. VM은 공인 IP 없이 `az vm run-command invoke`로 명령을 실행한다.
- 이미지는 공용 레지스트리 이미지를 사용한다(ACR 없음).

## 4. 접근 방식

| 안 | 내용 | 트레이드오프 |
|---|---|---|
| **1. az CLI 스크립트 + YAML (선택)** | 인프라·적용·검증·정리를 PowerShell 스크립트 4개로 나누고, K8s 리소스는 YAML 파일로 둔다 | 가장 단순하고 설계 항목과 명령이 1:1 대응. 재사용·멱등성은 낮음 |
| 2. Bicep + YAML | 인프라를 Bicep으로 | IaC 이관에 유리하나 작성량 약 2배, 검증 목적에 과함 |
| 3. 수동 가이드 문서 | 명령 나열 | PASS/FAIL 자동 판정 불가 |

사용자가 1안을 선택했다. 범위는 사용자가 A안(AKS 중심) + HPA 축소 검증 + 영역별 사용자 노드 풀 3개·`balance-similar-node-groups=true`로 확정했다. 자원 제약으로 시스템 노드 1대, 사용자 노드 `D2s_v5`로 줄였다(사용자 결정).

## 5. 구성

### 5.1 Azure 리소스 (`01-infra.ps1`)

장표와 1:1로 대응시키기 위해 장표의 예시 CIDR을 그대로 쓴다(격리된 샌드박스이므로 충돌 없음, 제 판단).

| 리소스 | 설정 |
|---|---|
| 리소스 그룹 | `rg-aksnetval-krc` (이름은 매개변수) |
| VNet | `vnet-aksnetval` 10.50.0.0/16 |
| `snet-aks-app` | 10.50.4.0/22 — 모든 AKS 노드. NSG `nsg-aks-app` 연결 |
| `snet-ingress` | 10.50.1.0/27 — Istio 내부 LB IP 전용. NSG `nsg-ingress` 연결 |
| `snet-pe` | 10.50.2.0/24 — Storage Blob Private Endpoint |
| `snet-appgw-sim` | 10.50.3.0/27 — "App Gateway 대체" VM (허용 출발지) |
| `snet-other` | 10.50.3.32/27 — "그 밖의 VNet 출발지" VM (차단 출발지) |
| AKS | Standard SKU, Free 계층, 기본 K8s 버전, Azure CNI Overlay + Cilium 데이터 플레인, Pod CIDR 192.168.0.0/16, Istio 추가 기능 활성화, 클러스터 오토스케일러 프로필 `balance-similar-node-groups=true` |
| AKS ID 권한 | 클러스터 관리 ID에 VNet에 대한 Network Contributor (다른 서브넷에 내부 LB를 만들기 위해 필요) |
| Storage | 공용 네트워크 액세스 비활성 Storage 계정 1개 + Blob PE(`snet-pe`) + `privatelink.blob.core.windows.net` 사설 DNS 영역(VNet 연결) |
| VM 2대 | Ubuntu LTS, `Standard_B2s_v2`(koreacentral에서 B2s 미제공), 공인 IP 없음, `snet-appgw-sim`·`snet-other`에 각 1대 |

### 5.2 노드 풀

| 노드 풀 | 모드 | 영역 | VM 크기 | 노드 수 | 비고 |
|---|---|---|---|---|---|
| `system` | System | 1, 2, 3 | `Standard_D4s_v5` | 고정 1 | taint `CriticalAddonsOnly=true:NoSchedule`, AKS 필수 시스템 풀 |
| `userz1` | User | 1 | `Standard_D2s_v5` | 오토스케일 1\~3 | |
| `userz2` | User | 2 | `Standard_D2s_v5` | 오토스케일 1\~3 | |
| `userz3` | User | 3 | `Standard_D2s_v5` | 오토스케일 1\~3 | |

- 노드 풀은 별도 시스템 풀 1개(1대) + 영역별 사용자 풀 3개로 구성한다(사용자 결정). 시스템 노드가 1대이므로 시스템 풀은 가용성을 보장하지 않으며 검증용으로만 허용한다. 시스템 노드에는 CoreDNS, istiod, Istio Gateway가 함께 올라가므로 `D4s_v5`를 유지한다(제 판단, 2 vCPU면 Pending 위험). AKS는 시스템 풀이 반드시 하나 있어야 하고, 시스템 풀을 영역 풀 중 하나로 겸하면 `kubernetes.azure.com/mode` 레이블이 달라 "유사 노드 그룹"으로 인식되지 않을 수 있으므로 시스템 풀을 별도로 둔다(제 판단).
- 세 사용자 풀은 VM 크기, 레이블, taint를 동일하게 맞춘다. 영역 레이블과 AKS가 붙이는 풀 이름 레이블 외에는 차이가 없어야 CAS가 유사 그룹으로 본다.
- 시스템 풀을 taint로 분리하므로 업무용 워크로드는 사용자 풀에만 배치된다. Istio Gateway는 추가 기능 기본 동작대로 시스템 노드를 선호하며, 이 동작은 그대로 둔다(사용자 결정: 기능 검증용이라 트래픽이 적음).
- PRD 참고(검증 범위 밖): 시스템 풀의 핵심 Pod와 리소스 경쟁을 피하려면 영역 1·2·3에 걸친 전용 `ingress` 사용자 풀에 `azureservicemesh/istio.replica.preferred=true` 레이블을 붙여 Gateway를 분리한다(가중치 100 > 시스템 노드 50, 선호 조건이라 보장되지는 않음). 출처: https://learn.microsoft.com/azure/aks/istio-deploy-ingress

### 5.3 NSG 규칙

| NSG | 우선순위 | 규칙 | 목적 |
|---|---|---|---|
| `nsg-aks-app` | 100 | Allow: 출발 `snet-appgw-sim` → 대상 10.50.1.10, TCP 80 | `allow-appgw-to-ingress` 구현 위치(노드 서브넷) |
| `nsg-aks-app` | 110 | Deny: 출발 `snet-other` → 대상 10.50.1.10, TCP 80 | 그 밖의 출발지 차단 증명 |
| `nsg-ingress` | 100 | Deny: 출발 `snet-appgw-sim` → 모든 대상, 모든 포트 | "LB IP 서브넷 NSG는 효과 없음" 증명용 |

기본 규칙(AzureLoadBalancer 허용, VNet 내부 허용)은 그대로 둔다.

### 5.4 Istio (`02-apply.ps1`)

- `az aks mesh enable-ingress-gateway --ingress-gateway-type internal`로 내부 Gateway를 켠다.
- Service `aks-istio-ingressgateway-internal`에 어노테이션을 붙인다.
  - `service.beta.kubernetes.io/azure-load-balancer-internal-subnet: snet-ingress`
  - `service.beta.kubernetes.io/azure-load-balancer-ipv4: 10.50.1.10`
- 네임스페이스 `web`, `app`에 `istio.io/rev=<설치된 리비전>` 레이블(리비전은 스크립트가 `az aks show`로 조회).
- `PeerAuthentication` STRICT: `web`, `app` 네임스페이스.
- Istio `Gateway`(selector `istio: aks-istio-ingressgateway-internal`, HTTP 80) + `VirtualService`: Host `web-a.test` → `web-a`, `web-b.test` → `web-b`.
- `AuthorizationPolicy`
  - `web-allow-ingress` (ns web): `web-a`, `web-b`에 Ingress Gateway SA principal만 허용. SA 이름은 스크립트가 Gateway Pod에서 조회해 주입한다.
  - `bff-a-allow` (ns app): `cluster.local/ns/web/sa/web-a`만 허용.
  - `bff-b-allow` (ns app): `cluster.local/ns/web/sa/web-b`만 허용.

### 5.5 워크로드

| 네임스페이스 | 이름(Deployment·SA·Service 동일) | 사이드카 | 역할 |
|---|---|---|---|
| web | `web-a`, `web-b` | O | HTTP 8080 응답 + 테스트용 curl·nc 실행 가능 |
| app | `bff-a`, `bff-b` | O | HTTP 8080 응답 + curl·nc 실행 가능 |
| nomesh | `probe` | X | STRICT mTLS 차단 증명용 클라이언트 |
| scale | `hpa-demo` | X | HPA 검증 대상, CPU requests 100m, 유휴 상태 |
| scale | `zone-fill` | X | 영역 균형 검증용. 평소 replicas 0 |
| scale | `zone-spread` | X | 영역 강제 분산 + CAS 검증용(T13b). 평소 replicas 0 |

각 Deployment는 `serviceAccountName`을 명시하고 replicas 1(hpa-demo, zone-fill 제외). 업무용 Deployment에는 영역 기준 `topologySpreadConstraints`(`maxSkew: 1`, `ScheduleAnyway`)를 넣는다. 이미지 선택은 빌더 몫이다. 조건: 8080에서 HTTP 200 응답, 같은 Pod에서 `curl`과 `nc`로 테스트 가능.
- **필수:** web·app Service 포트 이름은 `http`로 하거나 `appProtocol: http`를 지정한다. 그래야 Istio가 HTTP로 인식해 AuthorizationPolicy 거부가 403으로 나온다. TCP로 인식되면 연결이 리셋되어 T5·T7·T9 판정이 어긋난다.

### 5.6 NetworkPolicy

- `deny-web-to-data` (ns web): `podSelector: {}`, Egress만. 허용 = `namespaceSelector: {}` + `ipBlock 0.0.0.0/0 except 10.50.2.0/24`.
- `app` 네임스페이스에는 정책을 두지 않는다(`allow-app-to-data`는 "막히지 않음"을 확인).

### 5.7 HPA

- `hpa-demo` HPA: `minReplicas 5`, `maxReplicas 5`, CPU Utilization 70%, `behavior.scaleDown.policies: [{type: Pods, value: 1, periodSeconds: 60}]`, 안정화 시간은 기본값(300초) 유지(PRD와 같게, 제 판단).
- 검증 절차: 5개 Ready 확인 → `minReplicas`를 2로 패치(유휴라 목표 2) → 5초 간격으로 `spec.replicas`와 시각을 기록 → 2 도달 또는 15분 초과 시 종료.
- 판정: 값이 5→4→3→2 순서로 1씩만 감소하고, 감소 간격이 모두 55초 이상이면 PASS. 패치부터 첫 감소까지 걸린 시간은 기록만 하고 판정하지 않는다.
- 범위: 검증 대상은 **축소 속도 정책(Pods 1 / 60s)뿐**이다(사용자 답변: "5개에서 최소값인 2개까지 1분에 1개씩"). 안정화 창(300초)은 검증하지 않는다. 유휴 상태의 계산값은 원래 약 1이고 안정화는 min/max 적용 전 값에 걸리므로, 패치 직후(수십 초 내) 축소가 시작될 수 있다. 이 결과를 PRD의 안정화 동작 근거로 쓰지 않도록 README에 명시한다.

### 5.8 영역 균형 (CAS)

- 설정 확인: `az aks show`의 `autoScalerProfile.balanceSimilarNodeGroups`가 `true`이고, 세 사용자 풀의 영역이 각각 1·2·3인지 확인한다.
- 확장 유도: `zone-fill`(사용자 풀에만 배치, 영역 분산 제약 없음, CPU requests는 `D2s_v5` 노드 하나에 Pod 하나만 들어가는 크기, 구현값 800m. 1200m은 세 풀이 모두 최대 3대까지 차서 판정이 무의미했음)을 6개로 늘려 사용자 노드 목표를 6대(풀당 2대)로 만든다. 목표(6대)를 최대 용량(9대)보다 작게 잡아야 균형 옵션이 없을 때 3/2/1처럼 치우칠 여지가 남아 판정에 의미가 있다(제 판단). 분산 제약을 일부러 넣지 않아 **스케줄러가 아니라 CAS 설정의 효과**를 본다.
- 판정: 모든 Pod가 Running이 된 뒤 영역별 사용자 노드 수의 최대-최소 차이가 1 이하이면 PASS. 15분 내 Pending이 남으면 FAIL(할당량 부족 등 원인 출력). 보조 근거로 Pending Pod의 `TriggeredScaleUp` 이벤트에 여러 풀이 함께 나오는지 기록한다(판정에는 쓰지 않음).
- 원복: `zone-fill`을 0으로 줄인다. 이후 CAS 축소(기본 10분 이상 소요)의 영역 분포는 판정하지 않고 관찰 값만 기록한다.

### 5.9 PRD 권장 YAML 반영 (v2 추가)

PRD 검토용 Deployment·PDB·HPA YAML 에서 이 샘플로 확인할 가치가 있는 항목을 추가한다.

- `hpa-demo`: `replicas` 생략(HPA 가 소유), 사용자 풀 `nodeSelector`, 영역 분산을 `DoNotSchedule` + `minDomains: 3` + `nodeTaintsPolicy: Honor` + `matchLabelKeys: [pod-template-hash]` 로 강화.
- T12b(관찰): T12 축소 전·후 `hpa-demo` Pod 의 영역 분포를 기록한다. 분산 제약은 배치 때만 적용되므로 판정하지 않는다(`INFO`). Pod 2개·영역 3개라 빈 영역은 필연이다.
- T12c(v3 추가): `minReplicas` 5 → 3 축소를 3회 반복해 매회 세 영역에 Pod 가 1개 이상 남으면 PASS, 빈 영역이 생기면 FAIL, Running 이 3개가 아니면 INCONCLUSIVE.
- T13b: `zone-spread`(위와 같은 강제 분산, 사용자 풀, CPU 800m)를 노드 1/1/1 에서 6개로 늘린다. 15분 안에 Pending 없이 영역별 Pod 수 차이 ≤1 이면 PASS. Pending 이 남으면 FAIL, Running 합계가 6이 아니면 INCONCLUSIVE. 시작 전 노드가 풀당 1대로 돌아올 때까지 최대 25분 기다린다.
- T14: `web-a`/`web-b`/`bff-a`/`bff-b` 에 readinessProbe(httpGet `/` 8080)를 넣고, STRICT mTLS 에서 Istio 가 probe 를 `15020 /app-health/...` 로 rewrite 해 Ready 가 되는지 확인한다.

## 6. 검증 매트릭스 (`03-verify.ps1`)

| ID | 출발 | 대상 | 기대 | 증명하는 규칙 | 대조군 |
|---|---|---|---|---|---|
| T1 | VM `snet-appgw-sim` | 10.50.1.10:80 Host `web-a.test` | 200 | allow-appgw-to-ingress, allow-ingress-to-web | — |
| T2 | VM `snet-other` | 10.50.1.10:80 Host `web-a.test` | 시간 초과 | NSG는 노드 서브넷에서 평가됨 | T1, T2c |
| T2c | VM `snet-other` | Blob PE IP:443 (TLS) | 연결 성공 | 대조군: 같은 VM·run-command가 정상 | — |
| T3 | (T1 결과 재사용) | — | `nsg-ingress` Deny가 있어도 T1이 200 | LB IP 서브넷 NSG 무효 | T1 |
| T4 | `web-a` | `bff-a:8080` | 200 | allow-web-to-own-bff | — |
| T5 | `web-a` | `bff-b:8080` | 403 | allow-web-to-own-bff | T6 |
| T6 | `web-b` | `bff-b:8080` | 200 | allow-web-to-own-bff | — |
| T7 | `web-b` | `bff-a:8080` | 403 | allow-web-to-own-bff | T4 |
| T8 | `nomesh/probe` | `bff-a:8080` | 연결 실패(리셋·200 아님) | STRICT mTLS | T4 |
| T9 | `web-a` | `web-b:8080` (Ingress 우회) | 403 | allow-ingress-to-web | T1 |
| T10 | `web-a` | `https://<계정>.blob.core.windows.net` (PE로 해석) | TLS 연결 실패(시간 초과) | deny-web-to-data | T11 |
| T11 | `bff-a` | 같은 URL | TLS 연결 성공(HTTP 응답 코드 수신, 값 무관) | allow-app-to-data | — |
| T12 | — | `hpa-demo` | 5→4→3→2, 간격 ≥55초 | HPA scaleDown policy | — |
| T13 | — | 사용자 노드 풀 3개 | 확장 후 영역별 노드 수 차이 ≤1 | balance-similar-node-groups | 설정 확인 결과 |
| T12b | — | `hpa-demo` 영역 분포 | 기록(INFO) | 축소 시 분산 제약 미적용 관찰 | — |
| T12c | — | `hpa-demo` minReplicas 5→3, 3회 | 매회 세 영역 모두 ≥1 | minReplicas ≥ 영역 수 | 회차별 Running 3개 |
| T13b | — | `zone-spread` 6개 | Pod 2/2/2, Pending 없음 | DoNotSchedule + minDomains 3 + CAS | 노드 1/1/1 시작 |
| T14 | kubelet | web·bff Pod readinessProbe | rewrite 후 Ready | Istio probe rewrite | — |

- 대조군이 실패하면 해당 차단 테스트는 INCONCLUSIVE.
- T10·T11은 `nc` 같은 TCP 연결 확인을 쓰지 않는다. 사이드카가 있는 Pod에서는 Envoy가 TCP 연결을 Pod 안에서 먼저 받아 주므로, Cilium이 차단해도 연결 성공으로 보인다. 대신 실제 TLS 연결 수립(`curl`의 HTTP 코드가 000이 아님)으로 판정한다. Envoy의 업스트림 연결도 Pod IP로 나가므로 NetworkPolicy가 그대로 적용된다(제 판단, 실행 결과로 확인). 먼저 DNS가 PE IP(10.50.2.x)로 해석되는지 확인하고, 아니면 INCONCLUSIVE로 판정한다.
- T12, T13은 시간이 걸리므로(각 10\~15분) `-Only Network`, `-Only Hpa`, `-Only Floor`, `-Only Zone`, `-Only Spread` 같은 선택 실행을 지원한다.
- 결과는 콘솔 표 + `aks-network-validation/results/<시각>.json`과 `.md`로 저장.

## 7. 파일 구성

```
aks-network-validation/
  README.md            # 실행 순서, 소요 시간·비용 참고, 정리 방법
  config.ps1           # 이름·리전·CIDR·VM 크기 매개변수
  01-infra.ps1
  02-apply.ps1
  03-verify.ps1
  04-cleanup.ps1       # 리소스 그룹 삭제(확인 프롬프트)
  manifests/           # 네임스페이스, 워크로드, Istio, NetworkPolicy, HPA YAML
  results/             # 실행 결과 (커밋하지 않음)
```

## 8. 결정된 사항과 빌더에게 맡기는 사항

**사용자 결정**
- 목적: 아키텍처 설계의 기능 검증
- 범위: A안(AKS 중심) + HPA 축소(5 → 2, Pods 1 / 60s)
- 노드: 영역 1·2·3에 사용자 노드 풀 3개(`D2s_v5`, 1\~3대), 시스템 풀 `D4s_v5` 1대 별도, `balance-similar-node-groups=true` (자원 제약으로 노드 크기·시스템 노드 수 축소)
- 방식: az CLI 스크립트 + YAML

**제 판단(검토 시 확인 요청)**
- 장표 CIDR 그대로 사용, `snet-ingress`는 /27
- 시스템 풀 1대(`D4s_v5`)를 별도로 두어 총 4개 풀(시스템 1 + 영역별 사용자 3)
- 영역 균형은 CAS 확장(Pod 6개 → 노드 6대, 최대 9대)으로 판정, `TriggeredScaleUp` 이벤트는 보조 기록, 축소는 관찰만
- App Gateway는 VM으로 대체, 포트는 HTTP 80(TLS 미검증)
- HPA 안정화 시간 기본값 300초 유지, 부하 생성 대신 `minReplicas` 패치로 축소 유도(안정화 창은 검증 범위 밖)
- T10·T11은 TCP가 아닌 TLS 연결로 판정, T2에는 같은 VM의 PE 접속 대조군(T2c) 추가
- `allow-app-to-data`는 "정책 없음 = 허용" 확인으로 대체

**빌더 몫**
- 컨테이너 이미지 선택, 스크립트 내부 구조, 대기·재시도 로직, 결과 파일 형식 세부, `zone-fill`의 정확한 CPU requests 값
