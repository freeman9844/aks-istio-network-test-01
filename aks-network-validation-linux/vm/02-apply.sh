#!/usr/bin/env bash
# vm-mgmt 에서 실행: 내부 Ingress Gateway 와 매니페스트 적용(기존 02-apply.ps1 이식).
# kubeconfig 는 $VM_ROOT/state/kubeconfig 만 쓴다(Private API 는 이 VNet 안에서만 닿는다).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/common.sh
source "$ROOT/vm/lib/common.sh"
retry 5 20 az login --identity -o none || die "az login --identity 실패"
# shellcheck source=../config.sh
source "$ROOT/config.sh"

GATEWAY_NS="aks-istio-ingress"
GATEWAY_SVC="aks-istio-ingressgateway-internal"
GATEWAY_LABEL="istio=aks-istio-ingressgateway-internal"
WORKLOAD_NS=(web app nomesh scale)
mkdir -p "$VM_ROOT/state" "$VM_ROOT/results"

# kube_ok <인자…>: 실패하면 명령·stderr 와 함께 중단
kube_ok() {
  local out err rc=0
  err="$(mktemp)"
  out="$(kube "$@" 2>"$err")" || rc=$?
  if (( rc != 0 )); then
    local msg
    msg="$(cat "$err")"
    rm -f "$err"
    die "kubectl ${*:1:3} 실패(종료 코드 $rc): $msg"
  fi
  rm -f "$err"
  [[ -n "$out" ]] && printf '%s\n' "$out"
  return 0
}

# wait_until <라벨> <제한초> <명령…>: 명령이 비어 있지 않은 값을 출력할 때까지 10초 간격으로 기다리고 그 값을 출력
wait_until() {
  local label="$1" timeout="$2" deadline value
  shift 2
  deadline=$(( $(date +%s) + timeout ))
  while true; do
    value="$("$@" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
      printf '%s\n' "$value"
      return 0
    fi
    (( $(date +%s) > deadline )) && die "시간 초과(${timeout}초): $label"
    log "대기 중: $label" >&2
    sleep 10
  done
}

apply_manifest() {
  log "적용: $1"
  kube_ok apply -f "$MANIFESTS_DIR/$1"
}

[[ -f "$VM_ROOT/state/kubeconfig" ]] || az_ok aks get-credentials -g "$RG" -n "$AKS" --file "$VM_ROOT/state/kubeconfig" --overwrite-existing -o none

# 1) 내부 Ingress Gateway
internal_on="$(az_ok aks show -g "$RG" -n "$AKS" --query "length(serviceMeshProfile.istio.components.ingressGateways[?mode=='Internal' && enabled] || \`[]\`)" -o tsv)"
if [[ "$internal_on" != 0 ]]; then
  log "이미 있음: 내부 Ingress Gateway"
else
  log "생성: 내부 Ingress Gateway (수 분 걸림)"
  az_ok aks mesh enable-ingress-gateway -g "$RG" -n "$AKS" --ingress-gateway-type internal -o none
fi

# 2) Gateway Service 어노테이션: snet-ingress 의 고정 IP 로 ILB 를 만든다
wait_until "Service $GATEWAY_NS/$GATEWAY_SVC" 600 kube get svc -n "$GATEWAY_NS" "$GATEWAY_SVC" -o name >/dev/null
require_value() { [[ -n "$1" ]] || die "$2 값이 비어 있습니다"; }
require_value "$SNET_INGRESS_NAME" "Ingress 서브넷 이름"
require_value "$INGRESS_IP" "Ingress IP"
kube_ok annotate svc -n "$GATEWAY_NS" "$GATEWAY_SVC" --overwrite \
  "service.beta.kubernetes.io/azure-load-balancer-internal-subnet=$SNET_INGRESS_NAME" \
  "service.beta.kubernetes.io/azure-load-balancer-ipv4=$INGRESS_IP" >/dev/null
log "어노테이션 적용: subnet=$SNET_INGRESS_NAME, ipv4=$INGRESS_IP"

# 3) 네임스페이스 + 사이드카 주입 레이블
rev="$(az_ok aks show -g "$RG" -n "$AKS" --query "serviceMeshProfile.istio.revisions[0]" -o tsv)"
require_value "$rev" "Istio 리비전"
log "Istio 리비전: $rev"
apply_manifest 00-namespaces.yaml
for ns in web app; do
  kube_ok label namespace "$ns" "istio.io/rev=$rev" --overwrite >/dev/null
done

# 4) Gateway Pod 의 SA 로 principal 을 만든다. 비어 있으면 모두 거부되므로 즉시 중단
sa="$(wait_until "Gateway Pod($GATEWAY_LABEL)" 300 kube get pod -n "$GATEWAY_NS" -l "$GATEWAY_LABEL" -o 'jsonpath={.items[0].spec.serviceAccountName}')"
require_value "$sa" "Gateway ServiceAccount"
principal="cluster.local/ns/$GATEWAY_NS/sa/$sa"
log "Gateway principal: $principal"

# 5) 워크로드 → Istio 정책(치환) → NetworkPolicy → 확장 검증용
apply_manifest 10-web.yaml
apply_manifest 11-app.yaml
apply_manifest 12-nomesh.yaml
rendered="$VM_ROOT/results/20-istio.rendered.yaml"
sed "s#__GATEWAY_PRINCIPAL__#$principal#g" "$MANIFESTS_DIR/20-istio.yaml" > "$rendered"
grep -q '__GATEWAY_PRINCIPAL__' "$rendered" && die "20-istio.yaml 치환 실패"
log "적용: 20-istio.yaml (principal 치환)"
kube_ok apply -f "$rendered"
apply_manifest 30-netpol.yaml
apply_manifest 31-app-pe-egress.yaml
# 타임스탬프가 초 단위이므로 다음 초로 넘긴 뒤 재시작해야 Pod 기동 시각이 정책 생성 시각보다 확실히 늦다
sleep 2
# app Pod 를 정책 적용 뒤에 다시 띄운다. 그래야 Ready(istiod xDS·인증서 수신)가 정책 아래에서 이뤄졌다는 근거가 된다(TN19)
kube_ok rollout restart deployment -n app >/dev/null
apply_manifest 40-scale.yaml

# 6) 롤아웃 확인(Gateway 포함)
for ns in "$GATEWAY_NS" "${WORKLOAD_NS[@]}"; do
  deps="$(kube_ok get deployment -n "$ns" -o name)"
  while read -r d; do
    [[ -n "$d" ]] || continue
    log "롤아웃 확인: $ns/$d"
    kube_ok rollout status -n "$ns" "$d" --timeout 5m >/dev/null
  done <<<"$deps"
done

# 7) ILB IP 확인: 어노테이션 전에 동적 IP 를 받았을 수 있으므로 기대 IP 가 될 때까지 기다린다
ilb_ip() {
  local cur
  cur="$(kube get svc -n "$GATEWAY_NS" "$GATEWAY_SVC" -o 'jsonpath={.status.loadBalancer.ingress[0].ip}')"
  [[ "$cur" == "$INGRESS_IP" ]] && echo "$cur"
  return 0
}
if ! ip="$( (wait_until "ILB IP $INGRESS_IP" 600 ilb_ip) )"; then
  die "Gateway ILB IP 가 '$(kube get svc -n "$GATEWAY_NS" "$GATEWAY_SVC" -o 'jsonpath={.status.loadBalancer.ingress[0].ip}' 2>/dev/null)' 입니다(기대 $INGRESS_IP)"
fi
log "Gateway ILB IP: $ip"
log "적용 완료"
