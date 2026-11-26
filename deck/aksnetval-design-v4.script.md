# 발표 스크립트 — AKS 네트워크 통제 검증 샘플 설계와 결과

7장 · L300 · 예상 13분 08초

## 1. 검증 샘플 구성 — NSG · Istio · NetworkPolicy
_2분 08초 · 925자_

이번 장표는 설계 문서의 샘플 구성을 한 장에 그린 그림입니다. 목적은 성능이 아니라, PRD 장표에 적힌 NSG, Istio AuthorizationPolicy, NetworkPolicy 규칙이 AKS에서 정말 의도대로 허용하고 차단하는지 확인하는 거예요.
바깥 초록 점선이 VNet 10.50.0.0/16이고, 장표의 예시 CIDR을 그대로 썼습니다. 왼쪽에는 출발지 VM 두 대가 있습니다. snet-appgw-sim의 VM-allow는 App Gateway를 대신하는 허용 출발지이고, snet-other의 VM-deny는 그 밖의 출발지라서 막혀야 합니다. 두 VM 모두 공인 IP 없이 run-command로 curl을 실행합니다.
위쪽 snet-ingress에는 Istio 내부 LB IP 10.50.1.10이 있습니다. 여기 NSG에 일부러 VM-allow를 막는 Deny 규칙을 넣었는데요, LB IP 서브넷의 NSG는 실제로 평가되지 않기 때문에 T1이 그대로 200이면 이 사실까지 증명됩니다. 실제 허용과 차단은 노드 서브넷 snet-aks-app의 NSG에서 100번 Allow, 110번 Deny로 걸립니다.
노드 풀은 시스템 풀 하나와 영역별 사용자 풀 세 개입니다. Istio Ingress Gateway는 기본 동작대로 시스템 노드에 둡니다. Gateway는 Host 이름으로 web-a, web-b에 나눠 보내고, web 네임스페이스의 AuthorizationPolicy가 Gateway의 SA만 받도록 합니다. 그래서 web-a가 web-b를 직접 부르면 403, T9입니다.
web에서 app으로는 자기 BFF만 허용합니다. web-a에서 bff-a는 T4, web-b에서 bff-b는 T6으로 성공하고, 교차 호출은 403이 나와야 합니다. 사이드카가 없는 probe는 STRICT mTLS 때문에 호출 자체가 실패하는 T8입니다.
오른쪽 snet-pe는 Tier 3 데이터를 대표하는 Blob Private Endpoint입니다. web 네임스페이스는 NetworkPolicy로 이 대역을 막아 T10이 실패해야 하고, app 네임스페이스는 정책이 없으니 T11이 성공해야 합니다. 아래쪽 scale 네임스페이스는 HPA와 영역 균형을 보는 곳인데, 다음 장에서 전체 검증 목록부터 보겠습니다.

> 출처: 설계 문서: docs/superpowers/specs/2026-11-19-aks-network-control-validation-design.md
https://learn.microsoft.com/azure/aks/istio-deploy-ingress
https://learn.microsoft.com/azure/aks/internal-lb


## 2. 검증 매트릭스 — 차단 테스트마다 허용 대조군을 둡니다
_1분 53초 · 750자_

앞 장에서 그림으로 본 경로를 검증 ID 단위로 정리한 표입니다. 스크립트가 이 표의 순서대로 실행하고 PASS, FAIL을 자동으로 판정합니다.
위쪽 T1부터 T3까지는 NSG 영역입니다. T1은 VM-allow에서 Ingress LB로 가서 200이 나와야 하고, T2는 VM-deny에서 같은 대상으로 가서 시간 초과가 나와야 하죠. 그런데 VM 자체가 고장 나도 똑같이 시간 초과가 나오거든요. 그래서 같은 VM에서 PE로 TLS 연결이 되는지 보는 T2c를 대조군으로 붙였습니다. T3는 T1 결과를 다시 쓰는데, snet-ingress에 Deny가 있어도 200이면 LB IP 서브넷 NSG가 효과 없다는 게 증명됩니다.
T4부터 T9까지는 Istio입니다. 자기 BFF는 200, 남의 BFF는 403, 사이드카가 없는 Pod는 연결 실패, Ingress를 우회한 web 간 호출은 403입니다. 여기서 403이 나오려면 Service 포트 이름이 http여야 해요. 그렇지 않으면 Istio가 TCP로 보고 연결을 끊어 버려서 판정이 어긋납니다.
T10과 T11은 NetworkPolicy입니다. nc로 TCP만 확인하면 사이드카가 연결을 먼저 받아 줘서 막혀도 성공처럼 보입니다. 그래서 실제 TLS 연결이 맺어지는지로 판정합니다.
T12와 T13은 스케일링 검증이고요, 맨 아래 세 줄은 PRD 권장 YAML을 반영하면서 이번에 추가한 항목입니다. T12b는 2개로 줄인 뒤 분포를 기록만 하고, T12c는 minReplicas 3으로 줄여도 세 영역에 하나씩 남는지 판정합니다. T13b는 영역 강제 분산과 CAS를 함께 보고, T14는 STRICT mTLS에서 readinessProbe가 통과하는지 봅니다. 차단 테스트에는 모두 허용 대조군이 있고 대조군이 실패하면 INCONCLUSIVE라는 점을 기억해 주세요. 그럼 T12, T13을 다음 장에서 그림으로 보겠습니다.

> 출처: 설계 문서 6장 검증 매트릭스
https://istio.io/latest/docs/ops/configuration/traffic-management/protocol-selection/


## 3. 스케일링 검증 — HPA 축소 속도와 영역 균형
_1분 51초 · 675자_

앞 장 표의 마지막 두 줄, T12와 T13을 그림으로 풀어 본 장표입니다. 둘 다 시간이 10분에서 15분쯤 걸려서 따로 실행할 수 있게 해 뒀어요.
왼쪽 HPA부터 보겠습니다. hpa-demo를 최소 5, 최대 5로 띄워 두고, minReplicas를 2로 패치합니다. 부하 없이 유휴 상태라 HPA는 2개까지 줄이려 하는데, behavior에 Pods 1개, 60초 정책을 걸어 두었기 때문에 한 번에 1개씩만 줄어야 하거든요. 그래서 막대가 5, 4, 3, 2로 내려가고 각 간격이 55초 이상이면 PASS입니다. 스크립트는 5초 간격으로 replicas와 시각을 기록합니다.
여기서 주의할 점이 두 가지 있어요. 하나는 안정화 창 300초는 이번 검증 범위가 아니라는 겁니다. 패치 직후에 바로 축소가 시작될 수 있어서, 이 결과를 안정화 동작의 근거로 쓰시면 안 됩니다. 다른 하나는 PDB가 HPA 축소에는 적용되지 않는다는 점이죠. PDB는 드레인 같은 eviction에만 동작합니다.
오른쪽은 영역 균형입니다. 영역 1, 2, 3에 사용자 풀을 하나씩 두고 각각 1대에서 3대로 오토스케일합니다. zone-fill Pod는 하나가 노드 한 대를 차지하도록 requests를 잡고, 분산 제약은 일부러 넣지 않습니다. 그래야 스케줄러가 아니라 CAS의 balance-similar-node-groups 효과를 볼 수 있거든요. 노드 6대로 늘어났을 때 영역별 차이가 1 이하면 PASS입니다.
축소할 때의 영역 분포는 공식적으로 보장되는 동작이 아니라서 관찰만 하고, 영역 레이블이 다른 풀을 유사 그룹으로 인식하는지는 PoC로 확인이 필요합니다. 여기까지가 설계였고요, 다음 장부터는 이 구성을 실제 AKS에 올려서 돌려 본 결과를 보겠습니다.

> 출처: https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#configurable-scaling-behavior
https://learn.microsoft.com/azure/aks/cluster-autoscaler


## 4. 결과 ① 네트워크 통제 — T1\~T11·T14 13건 모두 PASS
_1분 45초 · 722자_

여기서부터는 실제 결과입니다. 앞에서 본 매트릭스를 koreacentral의 AKS에 그대로 올려서 스크립트로 자동 판정한 표인데요, 권장 YAML을 반영한 재검증에서 T1부터 T11까지 대조군 T2c를 포함해 12건과 새로 넣은 T14까지 13건이 모두 PASS였습니다.
먼저 NSG를 보면, T1은 VM-allow에서 내부 LB로 200이 나왔고, T2는 VM-deny에서 curl 종료 코드 28, 그러니까 시간 초과로 막혔습니다. 같은 VM에서 Blob으로 TLS 연결이 되는 T2c가 통과했기 때문에, 이 차단이 VM 고장이 아니라 NSG 때문이라는 걸 말할 수 있죠. T3는 snet-ingress에 Deny 규칙이 있는데도 200이 나와서, LB IP 서브넷의 NSG는 효과가 없다는 게 확인됐습니다.
Istio 쪽은 자기 BFF 호출이 200, 교차 호출이 403, Ingress를 우회한 web 간 호출도 403이었습니다. 사이드카가 없는 probe는 종료 코드 56, 연결 리셋으로 끊겼고요. STRICT mTLS가 평문 연결을 받지 않는다는 뜻입니다.
NetworkPolicy는 T10과 T11을 같이 봐야 하는데요, 두 Pod 모두 Blob 이름이 PE IP 10.50.2.4로 해석된 걸 먼저 확인했고, web에서는 TLS가 실패, app에서는 성공했습니다. 아래 부정 검증처럼 NetworkPolicy를 지우면 T10만 FAIL로 바뀌었기 때문에, 차단 원인이 정확히 그 정책이라는 것까지 확인한 셈입니다. 마지막 줄 T14는 web과 bff에 readinessProbe를 넣었을 때, STRICT mTLS인데도 Istio가 probe를 15020 포트의 app-health 경로로 바꿔 줘서 네 개 모두 Ready가 됐다는 결과입니다. 그럼 시간이 걸리는 스케일링 결과를 다음 장에서 보겠습니다.

> 출처: 결과 파일: aks-network-validation/results/20261120-100357.md (v4 재검증, v3 20261119-233942.md 동일), 부정 검증은 v1 실행
curl 종료 코드 28 = 시간 초과, 56 = 수신 실패(연결 리셋)


## 5. 결과 ② 스케일링 — HPA 1분에 1개 축소, 영역 2/2/2 확장
_1분 38초 · 586자_

앞 장의 네트워크 통제에 이어서, 이번에는 스케일링 두 가지 결과입니다.
왼쪽 HPA부터 볼게요. minReplicas를 5에서 2로 패치하자 229초 뒤에 5에서 4로 줄었고, 이어서 59초, 64초 간격으로 3, 2까지 하나씩 내려갔습니다. 순서가 정확히 5, 4, 3, 2이고 간격이 모두 55초 기준을 넘었으니 PASS입니다. 5초 간격으로 표본을 뜨기 때문에 간격이 60초에서 몇 초 벗어나는 건 측정 오차 범위예요. 즉 behavior에 넣은 Pods 1개, 60초 정책이 의도대로 동작한다는 거죠. 다만 첫 감소까지 걸린 시간은 이전 실행의 6초에서 16초와 달리 이번엔 229초였어요. 판정에서 뺀 관찰 값이라서, 안정화 창 300초 동작을 이걸로 설명하시면 안 됩니다.
오른쪽은 영역 균형입니다. zone-fill Pod 6개를 800m로 띄우자 128초 만에 모두 Running이 됐고, 이번에도 세 영역 모두 1대에서 2대로 늘어 2, 2, 2가 됐습니다. 더 중요한 건 TriggeredScaleUp 이벤트 하나에 세 풀이 함께 나왔다는 점입니다. 영역 레이블이 다른 풀도 CAS가 유사 그룹으로 묶어서 같이 늘렸다는 뜻이라, 설계 때 PoC 필요로 남겼던 질문이 다시 한 번 확인됐습니다.
첫 실행에서는 3, 2, 2였는데요, 기존 노드에 Pod가 먼저 몇 개 들어가느냐에 따라 추가 대수가 달라지기 때문입니다. 개수는 달라도 차이가 1 이하로 유지된다는 게 핵심이죠. 그럼 PRD 권장 YAML을 반영해서 새로 본 결과를 다음 장에서 보겠습니다.

> 출처: 결과 파일: aks-network-validation/results/20261120-100357.md (v4), v3: 20261119-233942.md, v1: 20261119-192119.md
https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#configurable-scaling-behavior
https://learn.microsoft.com/azure/aks/cluster-autoscaler


## 6. 결과 ③ PRD 권장 YAML — 강제 분산 2/2/2, minReplicas 3 축소 1/1/1
_2분 11초 · 855자_

앞 장까지가 기존 검증의 재실행 결과였고요, 이번 장은 PRD 검토용 YAML에서 가져와 새로 넣은 세 가지 결과입니다.
왼쪽 T13b부터 볼게요. zone-spread라는 Deployment에 PRD YAML과 같은 영역 강제 분산, 그러니까 DoNotSchedule과 minDomains 3, nodeTaintsPolicy Honor를 넣고, 노드가 영역마다 한 대인 상태에서 6개로 늘렸습니다. 처음 세 개는 기존 노드에 하나씩 들어가고, 나머지 세 개는 Pending이 됐는데요, CAS가 이걸 보고 세 영역의 풀을 한 대씩 늘려서 132초 만에 Pod가 2, 2, 2로 모두 Running이 됐습니다. 강제 분산과 CAS가 충돌 없이 같이 동작한다는 근거죠. 이벤트는 실행마다 일부만 남기도 해서 판정은 Pod와 노드 수로만 했습니다.
가운데는 축소 뒤 영역 분포입니다. 위쪽 T12b는 hpa-demo를 2개로 줄인 결과로 1, 0, 1이 나왔는데요, Pod가 2개인데 영역은 3개니까 한 영역이 비는 건 당연하고, 차이 1이라 maxSkew 1도 만족합니다. 그래서 이건 결함이 아니라 기록용이고요, 진짜 질문은 영역 수만큼 남기면 영역마다 하나씩 남느냐였죠. 그걸 아래 T12c로 따로 봤습니다. minReplicas를 3으로 두고 5개에서 3개로 줄이는 걸 세 번 반복했더니, 축소 전 2, 1, 2나 1, 2, 2에서 세 번 모두 1, 1, 1로 줄었습니다. 분산 제약이 축소 때 적용되는 건 아니라서, 확장 때 추가된 Pod부터 지워진 결과로 보고 있고요, 보장된 동작은 아닙니다. 노드 장애 뒤처럼 분포가 틀어진 상황의 재분산은 Descheduler를 PoC로 확인하셔야 합니다.
오른쪽 T14는 STRICT mTLS에서 kubelet의 readinessProbe가 통과하는지입니다. kubelet은 평문으로 부르기 때문에 그대로면 막히는데, Istio가 probe를 15020 포트의 app-health 경로로 바꿔서 pilot-agent가 앱을 대신 호출합니다. 네 Pod 모두 rewrite되고 Ready였고, 별도 설정 없이 추가 기능 기본값으로 동작했습니다. 그럼 지금까지 결과를 PRD 관점으로 정리하겠습니다.

> 출처: 결과 파일: aks-network-validation/results/20261120-100357.md, T12c 20261120-100846.md
https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraint/
https://istio.io/latest/docs/ops/configuration/mesh/app-health-check/
https://github.com/kubernetes-sigs/descheduler


## 7. 결과 정리 — PRD 설계에 주는 의미와 남은 한계
_1분 42초 · 644자_

앞의 세 장에서 본 결과를 PRD 설계 관점으로 한 표에 모았습니다.
첫 줄 NSG는 허용과 차단이 LB IP 서브넷이 아니라 노드 서브넷 NSG에서 걸린다는 게 확인됐으니, PRD에서도 규칙을 노드 서브넷 NSG에 두시면 됩니다. AuthorizationPolicy는 서비스 계정 기준으로 교차 호출과 우회 호출이 모두 403이 나와서, web과 bff를 1대1로 묶는 정책을 그대로 써도 되겠습니다. STRICT mTLS에서는 사이드카가 없는 Pod가 아예 연결이 끊기니까, 메시 밖 워크로드가 있다면 예외를 따로 설계해야 하고요. NetworkPolicy는 사이드카를 거쳐 나가는 연결도 PE 대역에서 막혀서 Tier 3 데이터 분리에 쓸 수 있습니다.
스케일링 쪽은 HPA behavior로 1분에 1개 축소가 됐고, 영역별 풀 세 개와 균형 옵션 조합도 두 번 모두 차이 1 이하로 늘어났습니다. 이번에 추가한 세 줄도 보시면, PRD의 강제 분산 설정은 CAS와 함께 2, 2, 2를 만들었고, readinessProbe는 STRICT mTLS에서 따로 예외를 두지 않아도 됩니다. 축소 쪽은 minReplicas를 영역 수인 3으로 두면 세 번 모두 영역마다 하나씩 남았습니다. 다만 보장된 동작은 아니어서, 장애 뒤 재분산은 Descheduler를 PoC로 확인하셔야 합니다.
아래 한계도 꼭 같이 봐 주세요. 이건 기능 검증이지 성능 검증이 아니고, HTTP 80만 써서 TLS 종단은 다루지 않았습니다. 시스템 노드 한 대에 Gateway를 둔 건 검증용 구성이라, PRD에서는 Gateway 전용 ingress 풀을 권장드립니다. 이상으로 검증 결과 공유를 마치겠습니다.

> 출처: README: aks-network-validation/README.md (알려진 한계)
