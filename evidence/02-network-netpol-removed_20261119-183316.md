# 검증 결과 20261119-183316

| Id | From | To | Expect | Observed | Verdict | Note |
|---|---|---|---|---|---|---|
| TN1 | VM vm-appgw-sim | 10.50.1.10:80 Host web-a.test | 200 | 200 | PASS |  |
| TN2 | VM vm-other | 10.50.1.10:80 Host web-a.test | blocked(000) | 000 | PASS |  |
| TN3 | VM vm-other | staksnetval<hash>.blob.core.windows.net:443 (TLS) | 연결 성공(≠000) | 400 | PASS |  |
| TN4 | VM vm-appgw-sim | 10.50.1.10:80 (TN1 재사용) | 200 | 200 | PASS | nsg-ingress Deny 존재 상태에서 TN1 결과 재사용 |
| TN5 | web/web-a | bff-a:8080 | 200 | 200 | PASS |  |
| TN6 | web/web-a | bff-b:8080 | 403 | 403 | PASS |  |
| TN7 | web/web-b | bff-b:8080 | 200 | 200 | PASS |  |
| TN8 | web/web-b | bff-a:8080 | 403 | 403 | PASS |  |
| TN9 | nomesh/probe | bff-a:8080 | blocked(000) | 000 | PASS |  |
| TN10 | web/web-a | web-b:8080 (Ingress 우회) | 403 | 403 | PASS |  |
| TN11 | web/web-a | staksnetval<hash>.blob.core.windows.net (10.50.2.4) | TLS 연결 실패(000) | 400 | FAIL | DNS=10.50.2.4 |
| TN12 | app/bff-a | staksnetval<hash>.blob.core.windows.net (10.50.2.4) | TLS 연결 성공(≠000) | 400 | PASS | DNS=10.50.2.4 |
