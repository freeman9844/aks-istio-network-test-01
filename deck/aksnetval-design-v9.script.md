# 발표 스크립트 — AKS 네트워크 통제 검증 — Linux 관리 VM판 설계와 결과

12장 · L300 · 예상 15분 12초

## 1. 검증 샘플 구성 — Private AKS · NSG · Istio · NetworkPolicy
_1분 41초 · 736자_

오늘은 PowerShell판으로 했던 AKS 네트워크 통제 검증을 Linux 관리 VM에서 다시 돌린 결과를 공유드리려고 합니다. 먼저 이 장은 Linux판 구성을 한 장에 그린 그림이에요. 목적은 성능이 아니라, PRD에 적힌 NSG, Istio 인가, NetworkPolicy 규칙이 정말 의도대로 허용하고 차단하는지 보는 겁니다.
바깥 초록 점선이 VNet 10.50.0.0/16이고, 리소스 그룹은 PowerShell판과 섞이지 않게 rg-aksnetval-linux-krc로 따로 뒀습니다. 왼쪽에는 VM이 세 대 있는데요, vm-appgw-sim은 App Gateway를 대신하는 허용 출발지, vm-other는 막혀야 하는 출발지입니다. 가운데 새로 들어간 vm-mgmt는 snet-mgmt 10.50.3.64/27에 있어요. 모든 생성과 검증을 이 VM 안에서 run-command와 systemd-run으로 실행합니다. 세 VM 모두 공인 IP는 없고, NAT Gateway는 snet-mgmt에만 붙였어요.
또 하나 바뀐 점은 AKS가 Private Cluster라는 겁니다. API 서버는 노드 서브넷 안의 사설 IP 10.50.4.4로만 열리고 공용 FQDN도 껐는데, 이게 T15입니다. 그 밖의 Ingress, web과 bff 사이 인가, 교차 403은 기존과 같습니다.
오른쪽 snet-pe에는 PE가 두 개 생겼습니다. Blob PE 10.50.2.4는 bff-b만, Blob2 PE 10.50.2.5는 bff-a만 나갈 수 있어요. 이를 위해 app 네임스페이스에 오른쪽 위 NetworkPolicy 세 개를 걸었거든요. 그래서 T11과 T17은 허용, 서로 바뀐 T16과 T18은 차단이 기대값입니다. 발표는 이 구성 위에서 네트워크 검증을 먼저 끝내고, 그다음 스케일링 검증으로 넘어가겠습니다.

> 출처: 설계 문서: docs/superpowers/specs/2026-11-20-aks-network-validation-linux-mgmt-vm-design.md
README: aks-network-validation-linux/README.md · 매니페스트: aks-network-validation-linux/manifests/31-app-pe-egress.yaml
https://learn.microsoft.com/azure/aks/private-clusters
https://learn.microsoft.com/azure/virtual-machines/linux/run-command
https://learn.microsoft.com/azure/aks/istio-deploy-ingress


## 2. 네트워크 검증
_17초 · 119자_

첫 번째 파트는 네트워크 검증입니다. NSG, Istio 인가와 STRICT mTLS, PE egress 분리, 그리고 Private Cluster까지 허용과 차단이 의도대로 동작하는지 보겠습니다. 모두 22건이고, 차단 테스트에는 허용 대조군이 함께 붙어 있어요.

## 3. 네트워크 검증 매트릭스 — 22건, 차단 테스트마다 허용 대조군
_1분 33초 · 642자_

앞 장 그림의 네트워크 경로를 검증 ID 단위로 정리한 표입니다. vm-mgmt 안의 스크립트가 이 순서대로 실행하고, 판정은 Python이 자동으로 PASS, FAIL, INCONCLUSIVE를 매깁니다.
위쪽 T1부터 T3까지는 NSG예요. T1은 vm-appgw-sim에서 Ingress LB로 200, T2는 vm-other에서 시간 초과가 나와야 하죠. 그런데 VM이 고장 나도 시간 초과가 나오거든요. 그래서 같은 VM에서 PE로 TLS가 되는지 보는 T2c를 대조군으로 붙였습니다. T4부터 T9까지는 Istio로, 자기 BFF는 200, 남의 BFF와 Ingress 우회는 403, 사이드카 없는 Pod는 연결 실패입니다.
표 가운데 T10부터 T21까지가 Linux판에서 바뀐 곳인데요. T10은 web에서 PE로 가는 차단이고요. T11과 T17은 bff마다 자기 전용 PE만 TLS 성공이고, T16과 T18은 남의 PE라 실패가 기대값입니다. 이렇게 막으면 다른 통신까지 끊기지 않을까 걱정되잖아요. 그래서 T19부터 T21까지 CoreDNS, istiod, 인터넷 연결이 그대로인지, T22와 T23으로 노드로 가는 연결도 그대로인지 함께 봅니다.
아래쪽 T14는 STRICT mTLS에서 readinessProbe가 살아 있는지, 마지막 T15는 Private Cluster 확인입니다. 스케일링 항목인 T12와 T13은 두 번째 파트에서 따로 다룹니다. 차단 테스트에는 모두 허용 대조군이 있고 대조군이 실패하면 INCONCLUSIVE라는 점, 기억해 주세요. 그럼 실제 결과를 다음 장에서 보겠습니다.

> 출처: 설계 문서: docs/superpowers/specs/2026-11-20-aks-network-validation-linux-mgmt-vm-design.md (5.5 테스트 매트릭스)
README: aks-network-validation-linux/README.md (테스트 목록)
https://istio.io/latest/docs/ops/configuration/traffic-management/protocol-selection/


## 4. 결과 ① 네트워크 통제 — T1\~T11·T14 13건 모두 PASS
_1분 26초 · 606자_

앞 장 매트릭스를 koreacentral의 Private AKS에 올려서, vm-mgmt 안에서 자동 판정한 표인데요. T1부터 T11까지 대조군 T2c를 포함한 12건과 T14까지 13건이 모두 PASS였습니다. 표는 network 항목만 다시 돌린 재실행 결과이고, 전체 실행과 판정이 같습니다.
먼저 NSG를 보면, T1은 vm-appgw-sim에서 내부 LB로 200이 나왔고, T2는 vm-other에서 curl 종료 코드 28, 그러니까 시간 초과로 막혔습니다. 같은 VM에서 Blob PE로 TLS 연결이 되는 T2c가 통과했으니까, 이 차단이 VM 고장이 아니라 NSG 때문이라고 말할 수 있죠. T3는 snet-ingress에 Deny가 있는데도 200이라서, LB IP 서브넷 NSG는 효과가 없다는 게 다시 확인됐습니다.
Istio 쪽은 자기 BFF가 200, 교차 호출과 우회 호출이 403이었고, 사이드카 없는 probe는 종료 코드 56, 연결 리셋으로 끊겼습니다.
T10과 T11은 같이 보셔야 하는데요. 두 Pod 모두 Blob 이름이 PE IP 10.50.2.4로 해석된 걸 먼저 확인했어요. 그다음 web-a에서는 TLS가 실패, bff-b에서는 성공했습니다. Linux판에서는 이 PE가 bff-b 전용이라 출발지가 bff-b로 바뀌었어요. T14는 STRICT mTLS에서도 probe가 15020 포트로 rewrite되어 네 개 모두 Ready였습니다. 그럼 PE를 bff별로 나눈 결과를 다음 장에서 자세히 보겠습니다.

> 출처: 결과 파일: aks-network-validation-linux/results/20261120-052231.md (network 재실행), 20261120-050457.md (전체 실행)
curl 종료 코드 28 = 시간 초과, 56 = 수신 실패(연결 리셋)
https://istio.io/latest/docs/ops/configuration/mesh/app-health-check/
https://learn.microsoft.com/azure/aks/private-clusters


## 5. PE egress 분리 — bff별 전용 PE만 허용, 기본 egress 유지
_1분 56초 · 811자_

앞 장 마지막에 T11의 출발지가 bff-b로 바뀌었다고 말씀드렸는데요, 이번 장이 그 이유입니다. Linux판에서 새로 넣은 통제가 두 가지인데, 왼쪽이 PE egress 분리, 오른쪽이 API 서버 비공개입니다.
왼쪽 위 정책 세 개부터 볼게요. 표준 NetworkPolicy는 허용 규칙의 합집합이라서, Pod에 정책이 하나라도 걸리면 어느 정책이든 허용한 곳만 나갈 수 있거든요. 그래서 공통 정책 app-egress-default는 클러스터 내부와 0.0.0.0/0을 열되 PE 서브넷 10.50.2.0/24만 뺐어요. 그리고 bff-b에는 10.50.2.4/32, bff-a에는 10.50.2.5/32의 443만 다시 열었습니다.
가운데 격자가 결과입니다. bff-a는 Blob2만, bff-b는 Blob만 TLS가 성공했고, 서로 바뀐 T16과 T18은 종료 코드 28로 막혔습니다. 막는 바람에 다른 통신이 끊기면 안 되잖아요. 그래서 아래 T19부터 T21까지 CoreDNS 해석, istiod 15021 ready, 인터넷 mcr 연결이 모두 정상인 걸 확인했습니다. 다만 T20은 Pod가 정책보다 먼저 떠 있었다면 근거가 안 돼서, 정책 이후 기동한 경우만 인정합니다.
그런데 노드로 가는 T22, T23에서 하나 발견했어요. 0.0.0.0/0을 열었는데도 bff-b에서 노드 kubelet으로는 000이 나왔습니다. 정책이 없는 probe는 401로 붙었으니 정책 때문인 거죠. Cilium은 기본값에서 ipBlock을 Pod나 노드 IP에 매칭하지 않거든요. 노드 대역을 ipBlock으로 더해도 그대로였고, CiliumNetworkPolicy로 host와 remote-node 엔터티를 열자 401로 붙었습니다. 그래서 네 번째 정책으로 넣었어요.
오른쪽 T15는 vm-mgmt에서는 API 서버가 사설 IP 10.50.4.4로 해석되고 readyz가 ok, 로컬에서는 NXDOMAIN이라 PASS입니다. 그럼 네트워크 결과를 PRD 관점으로 정리해 보겠습니다.

> 출처: 매니페스트: aks-network-validation-linux/manifests/31-app-pe-egress.yaml
결과 파일: aks-network-validation-linux/results/20261120-082121.md (CNP 보완 후 22 PASS), 20261120-075659.md (T22·T23 FAIL)
https://docs.cilium.io/en/stable/network/kubernetes/policy/
https://kubernetes.io/docs/concepts/services-networking/network-policies/
https://learn.microsoft.com/azure/aks/use-network-policies
https://learn.microsoft.com/azure/aks/private-clusters


## 6. 네트워크 결과 정리 — 22 PASS, PRD 시사점과 남은 한계
_1분 04초 · 411자_

네트워크 파트를 PRD 설계 관점으로 한 표에 모았습니다. 네트워크는 22건 모두 PASS예요.
위쪽 세 줄은 PowerShell판과 같습니다. 허용과 차단은 노드 서브넷 NSG에서 걸리고, 서비스 계정 기준 인가로 교차와 우회 호출이 403, 사이드카 없는 Pod는 연결이 끊깁니다. 그다음 세 줄이 Linux판에서 새로 확인한 부분인데요. 공통 정책에서 PE 대역을 빼고 Pod별 /32만 다시 열어서 bff마다 자기 PE만 쓰게 만들 수 있었고, CoreDNS, istiod, 인터넷은 끊기지 않았습니다. 다만 노드로 가는 통신은 표준 정책만으로는 막혀서 CiliumNetworkPolicy로 명시해야 했어요. Private Cluster는 운영을 VNet 안 관리 VM에서 하는 형태가 그대로 동작했고, probe는 STRICT mTLS에서도 따로 예외가 필요 없었습니다.
아래 상자도 같이 봐 주세요. 이번 검증은 기능 검증이고, 모든 호출을 HTTP 80으로 했습니다. 그럼 두 번째 파트, 스케일링 검증으로 넘어가겠습니다.

> 출처: README: aks-network-validation-linux/README.md (최신 결과 26 PASS + T12b INFO)
설계 문서: docs/superpowers/specs/2026-11-20-aks-network-validation-linux-mgmt-vm-design.md
https://learn.microsoft.com/azure/aks/azure-cni-powered-by-cilium
https://kubernetes.io/docs/concepts/services-networking/network-policies/


## 7. 스케일링 검증
_18초 · 110자_

두 번째 파트는 스케일링 검증입니다. HPA가 정해진 속도로만 줄어드는지, CAS가 세 영역을 고르게 늘리는지, 그리고 PRD 권장 YAML의 강제 분산이 CAS와 함께 동작하는지 보겠습니다. 모두 다섯 항목이고, 그중 T12b는 기록용 INFO예요.

## 8. 스케일링 검증 매트릭스 — T12\~T13b, 판정 규칙
_1분 16초 · 458자_

방금 스케일링 파트를 열었는데요, 이 장은 그 검증 항목과 판정 규칙입니다. 네트워크와 달리 시간이 오래 걸리고, 결과가 시점에 따라 달라질 수 있어서 판정 규칙을 먼저 짚고 가겠습니다.
T12는 HPA가 1분에 1개씩만 줄어드는지, T12b는 줄어든 뒤 영역 분포를 기록하고, T12c는 축소 때 실제로 지워진 Pod가 ReplicaSet 삭제 규칙으로 예측한 Pod와 같은지 판정해요. T12b는 Pod 2개라 한 영역이 비는 게 당연해서 기록용 INFO입니다. T13은 CAS가 세 영역을 고르게 늘리는지, T13b는 PRD 권장 강제 분산을 넣어도 CAS와 함께 동작하는지입니다.
아래 상자처럼 vm-mgmt에서 5초 간격으로 기록하고, 15분 안에 Pending이 남거나 감소 폭과 간격이 어긋나면 FAIL입니다. 예를 들어 HPA가 한 번에 2개를 줄이거나 50초 만에 다음 축소가 나오면 behavior 정책이 안 먹은 거라 FAIL로 처리하죠. 반대로 공식 문서가 보장하지 않는 축소 순서 같은 동작은 PASS, FAIL을 매기지 않고 기록만 남깁니다. 표의 마지막 열이 그 구분이에요. 그럼 T12와 T13 설계를 다음 장에서 그림으로 보겠습니다.

> 출처: 설계 문서: docs/superpowers/specs/2026-11-20-aks-network-validation-linux-mgmt-vm-design.md (5.5 테스트 매트릭스)
https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraint/


## 9. 스케일링 검증 — HPA 축소 속도와 영역 균형
_1분 31초 · 575자_

앞 장 표의 T12와 T13을 그림으로 풀어 본 장표입니다. 판정 규칙은 PowerShell판과 똑같고, 실행 위치만 vm-mgmt로 바뀌었어요.
왼쪽 HPA부터 보겠습니다. hpa-demo를 최소 5, 최대 5로 띄워 두고 minReplicas를 2로 패치합니다. 부하가 없으니 HPA는 2개까지 줄이려 하는데, behavior에 Pods 1개, 60초 정책을 걸어 뒀기 때문에 한 번에 1개씩만 줄어야 하거든요. 그래서 막대가 5, 4, 3, 2로 내려가고 간격이 모두 55초 이상이면 PASS입니다. 스크립트는 5초 간격으로 replicas와 시각을 기록합니다.
여기서 주의할 점이 두 가지 있어요. 안정화 창 300초는 이번 범위가 아니라서, 패치 직후에 바로 축소가 시작될 수 있습니다. 그리고 PDB는 HPA 축소에 적용되지 않고 드레인 같은 eviction에만 동작하죠.
오른쪽은 영역 균형입니다. 영역 1, 2, 3에 사용자 풀을 하나씩 두고 각각 1대에서 3대로 오토스케일합니다. zone-fill Pod는 하나가 노드 한 대를 차지하도록 800m를 잡고, 분산 제약은 일부러 뺐습니다. 그래야 스케줄러가 아니라 CAS의 balance-similar-node-groups 효과만 볼 수 있으니까요. 영역별 노드 차이가 1 이하면 PASS이고, 축소할 때 분포는 공식 보장이 아니라서 관찰만 합니다. 여기까지가 설계였고요, 다음 장에서 Private AKS에서 실제로 돌린 결과를 보겠습니다.

> 출처: 설계 문서: docs/superpowers/specs/2026-11-20-aks-network-validation-linux-mgmt-vm-design.md (5.5 T1\~T14 판정 규칙 유지)
https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#configurable-scaling-behavior
https://learn.microsoft.com/azure/aks/cluster-autoscaler


## 10. 결과 ② 스케일링 — HPA 1분에 1개 축소, 영역 2/2/2 확장
_1분 26초 · 552자_

앞 장 설계대로 Private AKS에서 돌린 스케일링 두 가지 결과입니다.
왼쪽 HPA부터 볼게요. minReplicas를 5에서 2로 패치하자 10초 뒤에 5에서 4로 줄었고, 이어서 61초, 62초 간격으로 3, 2까지 하나씩 내려갔습니다. 순서가 정확히 5, 4, 3, 2이고 간격이 모두 55초 기준을 넘었으니 PASS입니다. 5초 간격으로 표본 27개를 떴기 때문에 60초에서 1, 2초 벗어나는 건 측정 오차 범위예요. 즉 behavior에 넣은 Pods 1개, 60초 정책이 의도대로 동작한다는 거죠. 다만 패치에서 첫 감소까지 10초는 판정에서 뺀 관찰 값이라, 안정화 창 300초 동작을 이걸로 설명하시면 안 됩니다.
오른쪽은 영역 균형입니다. zone-fill Pod 6개를 800m로 띄우자 108초 만에 모두 Running이 됐고, 세 영역 모두 1대에서 2대로 늘어 2, 2, 2가 됐습니다. 더 중요한 건 TriggeredScaleUp 이벤트 하나에 세 풀이 함께 나왔다는 점이에요. 영역 레이블이 다른 풀도 CAS가 유사 그룹으로 묶어 같이 늘렸다는 뜻이고, balanceSimilarNodeGroups가 true인 것도 설정에서 확인했습니다.
Private Cluster에 관리 VM에서 돌렸는데도 PowerShell판과 같은 결론이 나왔다는 게 핵심이죠. 그럼 PRD 권장 YAML을 반영해서 본 결과를 다음 장에서 보겠습니다.

> 출처: 결과 파일: aks-network-validation-linux/results/20261120-050457.md (T12·T13)
https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#configurable-scaling-behavior
https://learn.microsoft.com/azure/aks/cluster-autoscaler


## 11. 결과 ③ PRD 권장 YAML — 강제 분산 2/2/2, minReplicas 3 축소 규칙 일치
_1분 54초 · 726자_

앞 장이 기본 스케일링이었다면, 이번 장은 PRD 검토용 YAML에서 가져온 두 가지 결과입니다.
왼쪽 T13b부터 볼게요. zone-spread에 PRD YAML과 같은 영역 강제 분산, 그러니까 DoNotSchedule과 minDomains 3, nodeTaintsPolicy Honor를 넣었어요. 그리고 노드가 영역마다 한 대인 상태에서 6개로 늘렸습니다. CAS가 세 영역 풀을 한 대씩 늘려 노드가 2, 2, 2가 됐고, 110초 만에 Pod 2, 2, 2로 6개 모두 Running, Pending은 없었습니다. 이벤트는 이번에도 userz2와 userz1 두 건만 남아서, 판정은 Pod와 노드 수로만 했어요.
오른쪽은 축소 뒤 영역 분포입니다. T12b는 hpa-demo를 2개로 줄인 결과로 1, 2, 2에서 0, 1, 1이 됐는데요. Pod 2개에 영역 3개니까 한 영역이 비는 건 당연하고 차이 1이라 maxSkew 1도 만족합니다. 그래서 기록용 INFO고요, 진짜 질문은 영역 수만큼 남기면 영역마다 하나씩 남느냐였죠. T12c에서는 minReplicas 3으로 5개, 9개, 12개에서 3개로 줄이는 걸 각각 두 번씩 했고, 여섯 번 모두 1, 1, 1이 됐습니다. 지워진 Pod 34개를 Pod 감시로 기록해서 ReplicaSet 삭제 규칙 예측과 비교했더니 모두 일치했어요. 11월 22일 재테스트 결과인데, 같은 시각·같은 구간이라 후보가 여러 개인 경우가 20건, 한 번에 2개가 지워진 경우가 1회 있었고 모두 규칙으로 설명됩니다. 영역마다 노드가 한 대라서, 같은 노드에 Pod가 많은 쪽부터 지우는 규칙이 적용돼 1, 1, 1로 끝난 겁니다. 분산 제약은 새 Pod 배치에만 쓰이므로, 영역에 노드가 여러 대이거나 한 번에 여러 개를 줄이면 축소 뒤 영역 균등 배치는 보장되지 않습니다.
그럼 스케일링 결과를 PRD 관점으로 정리하겠습니다.

> 출처: 결과 파일: aks-network-validation-linux/results/20261120-050457.md (T12b·T13b), results/20261122-114525.md (T12c 재테스트)
https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraint/
https://github.com/kubernetes-sigs/descheduler


## 12. 스케일링 결과 정리 — 4 PASS + T12b INFO, 전체 26 PASS
_50초 · 320자_

스케일링 파트를 PRD 관점으로 정리하면 네 줄입니다. HPA는 behavior 정책대로 61초, 62초 간격으로 1개씩 줄었고, 세 영역 풀이 함께 2, 2, 2로 늘었으며, PRD의 강제 분산도 CAS와 충돌 없이 2, 2, 2를 만들었습니다. minReplicas를 영역 수로 두면 축소 뒤에도 영역마다 하나씩 남았어요.
아래 상자가 전체 결론입니다. 네트워크 22건과 스케일링 4건, 합쳐서 26건 PASS에 T12b 하나가 기록용 INFO입니다. 가운데 숫자 카드로 보시면 FAIL과 INCONCLUSIVE는 하나도 없습니다. 결국 PRD에 적힌 HPA behavior, 영역별 풀, 강제 분산은 그대로 써도 된다는 결론입니다. 이상으로 Linux 관리 VM판 검증 결과 공유를 마치겠습니다. 질문 있으시면 편하게 주세요.

> 출처: README: aks-network-validation-linux/README.md (최신 결과 26 PASS + T12b INFO)
설계 문서: docs/superpowers/specs/2026-11-20-aks-network-validation-linux-mgmt-vm-design.md
https://learn.microsoft.com/azure/aks/azure-cni-powered-by-cilium
https://kubernetes.io/docs/concepts/services-networking/network-policies/
