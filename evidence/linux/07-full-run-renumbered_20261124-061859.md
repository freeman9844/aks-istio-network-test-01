# 검증 결과 20261124-061859

| Id | From | To | Expect | Observed | Verdict | Note |
|---|---|---|---|---|---|---|
| TN1 | VM vm-appgw-sim | 10.50.1.10:80 Host web-a.test | 200 | 200 | PASS |  |
| TN2 | VM vm-other | 10.50.1.10:80 Host web-a.test | blocked(000) | 000 | PASS | curl rc=28 |
| TN3 | VM vm-other | staksnetval<hash>.blob.core.windows.net:443 (TLS) | 연결 성공(≠000) | 400 | PASS |  |
| TN4 | VM vm-appgw-sim | 10.50.1.10:80 (TN1 재사용) | 200 | 200 | PASS | nsg-ingress/deny-appgw-all Deny 규칙과 서브넷 연결 확인, TN1 결과 재사용 |
| TN5 | web/web-a | bff-a:8080 | 200 | 200 | PASS |  |
| TN6 | web/web-a | bff-b:8080 | 403 | 403 | PASS |  |
| TN7 | web/web-b | bff-b:8080 | 200 | 200 | PASS |  |
| TN8 | web/web-b | bff-a:8080 | 403 | 403 | PASS |  |
| TN9 | nomesh/probe | bff-a:8080 | blocked(000) | 000 | PASS | curl rc=56 |
| TN10 | web/web-a | web-b:8080 (Ingress 우회) | 403 | 403 | PASS |  |
| TN11 | web/web-a | staksnetval<hash>.blob.core.windows.net (10.50.2.4) | TLS 연결 실패(000) | 000 | PASS | DNS=10.50.2.4; curl rc=28 |
| TN12 | app/bff-b | staksnetval<hash>.blob.core.windows.net (10.50.2.4) | TLS 연결 성공(≠000) | 400 | PASS | DNS=10.50.2.4 |
| TN13 | kubelet | web-a/web-b/bff-a/bff-b readinessProbe | Ready + 15020 /app-health/ rewrite | Ready 4/4, rewrite 4/4 | PASS | 모든 Pod가 15020 /app-health/app/readyz 로 rewrite 후 Ready |
| TN14 | vm-mgmt + 로컬 | API 서버 <cluster>.<guid>.privatelink.koreacentral.azmk8s.io:443 | 사설 IP·로컬 접근 불가 | private=True, publicFqdn=False, vm=10.50.4.4, readyz=ok, local=NXDOMAIN/실패 | PASS | API 서버는 사설 IP로만 해석·접근되고 로컬에서는 접근 불가 |
| TN15 | app/bff-a | staksnetval<hash>.blob.core.windows.net (10.50.2.4) | TLS 연결 실패(000) | 000 | PASS | DNS=10.50.2.4; curl rc=28 |
| TN16 | app/bff-a | staksnetvalb<hash>.blob.core.windows.net (10.50.2.5) | TLS 연결 성공(≠000) | 400 | PASS | DNS=10.50.2.5 |
| TN17 | app/bff-b | staksnetvalb<hash>.blob.core.windows.net (10.50.2.5) | TLS 연결 실패(000) | 000 | PASS | DNS=10.50.2.5; curl rc=28 |
| TN18 | app/bff-b | CoreDNS (kubernetes.default 해석) | 해석 성공 | 10.0.0.1 | PASS |  |
| TN19 | app/bff-b | istiod (Envoy xDS 동기화, :15021 ready) | 200 | 200 | PASS | Pod 기동 2026-11-24T04:51:37Z |
| TN20 | app/bff-b | mcr.microsoft.com:443 (인터넷) | 연결 성공(≠000) | 200 | PASS | curl rc=0 |
| TN21 | app/bff-b | 자기 노드 10.50.4.6:10250 (kubelet) | 연결 성공(≠000) | 401 | PASS | 자기 노드 aks-userz1-15001910-vmss000000; 대조군 nomesh/probe=401; curl rc=0 |
| TN22 | app/bff-b | 다른 노드 10.50.4.5:10250 (kubelet) | 연결 성공(≠000) | 401 | PASS | 다른 노드 aks-system-11774125-vmss000000; 대조군 nomesh/probe=401; curl rc=0 |
| TS1 | — | scale/hpa-demo (minReplicas 5→2) | 5→4→3→2, 간격 ≥55초 | 5,4,3,2 (간격 61,61초) | PASS | 5→4→3→2 순서로 감소, 간격 61,61초; 패치→첫 감소 10초(판정 외); 표본 27개/5초 |
| TS2 | — | scale/hpa-demo 영역 분포(DoNotSchedule, minDomains 3) | 기록(판정 없음) | 축소 전 1/2/2 → 축소 후 0/1/1 | INFO | 축소 후 영역별 Pod 수 차이 1 (Running 2); Pod 2개·영역 3개라 빈 영역 1개는 필연. 축소 때 지울 Pod 선택은 TS3 에서 판정 |
| TS3 | — | scale/hpa-demo (5·9·12개 → minReplicas 3, 각 2회) | 삭제된 Pod = ReplicaSet 삭제 규칙 예측 | 5개 2/2/1→1/1/1, 5개 2/2/1→1/1/1, 9개 3/3/3→1/1/1, 9개 3/3/3→1/1/1, 12개 4/4/4→1/1/1, 12개 4/4/4→1/1/1 | PASS | 삭제 34건 모두 ReplicaSet 규칙 예측과 일치(로그 구간 경계·완전 동률로 후보 2개 이상 26건 포함); 한 번에 2개 삭제 1회; 사용자 노드 1/1/1 |
| TS4 | — | 사용자 노드 풀 userz1/userz2/userz3 | 확장 후 영역별 노드 수 차이 ≤1 | 영역1=2, 영역2=2, 영역3=2 | PASS | 영역별 노드 수 차이 0 ≤ 1 (영역1=2, 영역2=2, 영역3=2); 확장 전 영역1=1, 영역2=1, 영역3=1; Running 6/6, 124초; TriggeredScaleUp: pod triggered scale-up: [{aks-userz3-38667051-vmss 1->2 (max: 3)} {aks-userz1-15001910-vmss 1->2 (max: 3)} {aks-userz2-24788361-vmss 1->2 (max: 3)}]; 설정: balanceSimilarNodeGroups=true; userz1=1, userz2=2, userz3=3 |
| TS5 | — | scale/zone-spread 6개(DoNotSchedule, minDomains 3) | Pod 2/2/2, Pending 없음 | Pod 2/2/2, 노드 1/1/1→2/2/2 | PASS | 영역별 Pod 수 차이 0 ≤ 1 (Pod 영역1=2, 영역2=2, 영역3=2); Running 6/6, 126초; TriggeredScaleUp: pod triggered scale-up: [{aks-userz3-38667051-vmss 1->2 (max: 3)}] / pod triggered scale-up: [{aks-userz2-24788361-vmss 1->2 (max: 3)}] |
