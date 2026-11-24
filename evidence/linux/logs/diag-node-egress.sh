K="kubectl --kubeconfig /opt/aksnetval/state/kubeconfig"
jq -c '.[]|select(.Id=="TN21" or .Id=="TN22")|{Id,Observed,Note}' /opt/aksnetval/results/20261120-075659.json
t(){ for ip in 10.50.4.6 10.50.4.5; do printf "%s %s " "$1" $ip; $K exec -n app deploy/bff-b -c app -- sh -c "curl -sk -o /dev/null -w '%{http_code}' -m 6 https://$ip:10250/healthz; echo ' rc='\$?" 2>/dev/null | tail -1; done; }
t 기준
echo "-- istio-proxy 컨테이너:"; $K exec -n app deploy/bff-b -c istio-proxy -- sh -c "curl -sk -o /dev/null -w '%{http_code}' -m 6 https://10.50.4.5:10250/healthz; echo ' rc='\$?" 2>&1 | tail -1
cat <<'Y' | $K apply -f - >/dev/null
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: diag-node-ipblock, namespace: app}
spec:
  podSelector: {matchLabels: {app: bff-b}}
  policyTypes: [Egress]
  egress:
  - to: [{ipBlock: {cidr: 10.50.4.0/24}}]
Y
sleep 8; t ipBlock허용
$K delete networkpolicy -n app diag-node-ipblock >/dev/null
cat <<'Y' | $K apply -f - 2>&1 | tail -1
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata: {name: diag-node-entities, namespace: app}
spec:
  endpointSelector: {matchLabels: {app: bff-b}}
  egress:
  - toEntities: [host, remote-node]
Y
sleep 8; t toEntities허용
$K delete cnp -n app diag-node-entities >/dev/null 2>&1
sleep 5; t 원복
$K -n kube-system get cm cilium-config -o json | jq -c '.data|with_entries(select(.key|test("policy-cidr|enable-policy|policy-mode")))'