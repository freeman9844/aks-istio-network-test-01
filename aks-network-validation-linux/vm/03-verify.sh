#!/usr/bin/env bash
# vm-mgmt 에서 실행: 검증(기존 03-verify.ps1 이식) + TN14 VM 관측.
# 사용: vm/03-verify.sh [--only all|network|hpa|floor|zone|spread]
# 결과: $VM_ROOT/results/<stamp>.json·.md, $VM_ROOT/state/latest(stamp), $VM_ROOT/state/tn14-vm.json(TN14 판정은 로컬에서)
set -euo pipefail
shopt -s inherit_errexit

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/common.sh
source "$ROOT/vm/lib/common.sh"

WORK="$(mktemp -d)"
ROWS="$WORK/rows.jsonl"
ERRF="$WORK/err"
: >"$ROWS"
: >"$ERRF"

PENDING_RESTORE=""
on_exit() {
  [[ -n "$PENDING_RESTORE" ]] && "$PENDING_RESTORE"
  rm -rf "$WORK"
}
trap on_exit EXIT
trap 'exit 143' TERM INT

# 테스트 서브셸 안에서 중단 사유를 남겨 INCONCLUSIVE 행의 "실행 오류: …" 로 쓴다
die() {
  printf '%s' "$*" >"$ERRF"
  echo "[$(date '+%H:%M:%S')] 오류: $*" >&2
  exit 1
}

ONLY=all
while (($#)); do
  case "$1" in
    --only) ONLY="${2:-}"; shift 2 ;;
    *) die "알 수 없는 인자: $1" ;;
  esac
done
case "$ONLY" in
  all | network | hpa | floor | zone | spread) ;;
  *) die "--only 값이 올바르지 않습니다: $ONLY" ;;
esac

retry 5 20 az login --identity -o none || die "az login --identity 실패"
# shellcheck source=../config.sh
source "$ROOT/config.sh"
mkdir -p "$VM_ROOT/state" "$VM_ROOT/results"
[[ -s "$VM_ROOT/state/kubeconfig" ]] || die "kubeconfig 없음: infra 단계를 먼저 실행하세요"

CURL_ARGS="-s -o /dev/null -w '%{http_code}' -m 10"
POOLS_TEXT="$(IFS=/; echo "${USER_POOLS[*]}")"

# ---------- 공통 ----------

kube_ok() {
  local out err rc=0 msg
  err="$(mktemp)"
  out="$(kube "$@" 2>"$err")" || rc=$?
  if ((rc != 0)); then
    msg="$(tr '\n' ' ' <"$err")"
    rm -f "$err"
    die "kubectl ${*:1:2} 실패(종료 코드 $rc): $msg"
  fi
  rm -f "$err"
  [[ -n "$out" ]] && printf '%s\n' "$out"
  return 0
}

# add <Id> <From> <To> <Expect> <Observed> <Verdict> [Note]
add() {
  py report row "$@" >>"$ROWS"
  log "[$1] $3: 관측 $5 → $6"
}

has_row() {
  jq -se --arg id "$1" 'any(.[]; .Id == $id)' "$ROWS" >/dev/null 2>&1
}

# _cli 는 문자열 결과를 {"verdict": …} 로 감싸 돌려준다
pystr() { py "$@" | jq -r .verdict; }

join_note() {
  local out="" p
  for p in "$@"; do
    [[ -n "$p" ]] && out+="${out:+; }$p"
  done
  printf '%s' "$out"
}

# control_note Id=Verdict…: PASS 가 아닌 대조군이 있으면 사유, 모두 PASS 면 빈 문자열
control_note() {
  local bad="" p
  for p in "$@"; do
    [[ "${p#*=}" == PASS ]] || bad+="${bad:+, }$p"
  done
  [[ -n "$bad" ]] && printf '대조군 실패: %s' "$bad"
  return 0
}

# run_test <오류 시 Id> <From> <To> <Expect> <함수> [그 함수가 쓰는 나머지 Id…]
# 함수가 중단되면 아직 행이 없는 Id 마다 INCONCLUSIVE 행을 남긴다(누락된 Id 때문에 요약이 전부 PASS 로 보이지 않게)
run_test() {
  local id="$1" from="$2" to="$3" expect="$4" fn="$5" rc=0 msg other added=0
  shift 5
  : >"$ERRF"
  set +e
  (
    set -eE
    trap '[[ -s "$ERRF" ]] || printf "명령 실패(rc=%s): %s" "$?" "$BASH_COMMAND" >"$ERRF"' ERR
    "$fn"
  )
  rc=$?
  set -e
  ((rc == 0)) && return 0
  msg="$(head -c 400 "$ERRF" | tr '\n' ' ')"
  if [[ -n "$id" ]] && ! has_row "$id"; then
    add "$id" "$from" "$to" "$expect" '오류' INCONCLUSIVE "실행 오류: $msg"
    added=1
  fi
  for other in "$@"; do
    if ! has_row "$other"; then
      add "$other" '—' "$fn 중단으로 미실행" '—' '오류' INCONCLUSIVE "실행 오류: $msg"
      added=1
    fi
  done
  ((added)) || log "경고: $fn 중단(rc=$rc): $msg"
}

# wait_cond <제한초> <간격초> <명령…>: 명령이 성공하면 0, 시간 초과면 1
wait_cond() {
  local deadline=$(($(date +%s) + $1)) iv="$2"
  shift 2
  while true; do
    "$@" && return 0
    (($(date +%s) >= deadline)) && return 1
    sleep "$iv"
  done
}

# ---------- curl·DNS ----------

# curl_cmd <url> [k] [Host 헤더]
curl_cmd() {
  local c="curl $CURL_ARGS"
  [[ "${2:-}" == k ]] && c+=" -k"
  [[ -n "${3:-}" ]] && c+=" -H 'Host: $3'"
  printf '%s' "$c '$1'; rc=\$?; echo; echo rc=\$rc"
}

curl_run() { py parse curl_run "$(jq -cn --arg o "$1" '{output: $o}')"; }

pod_exec() { kube exec -n "$1" "deploy/$2" -c app -- sh -c "$3" 2>/dev/null || true; }

# pod_curl <ns> <deploy> <url> [k] → {"ran","code","rc"}
pod_curl() {
  local out r
  out="$(pod_exec "$1" "$2" "$(curl_cmd "$3" "${4:-}")")"
  r="$(curl_run "$out")"
  [[ "$(jq -r .ran <<<"$r")" == true ]] || log "경고: $1/$2 에서 curl 이 실행되지 않았습니다(kubectl exec 실패: Pod·컨테이너 없음 또는 API 오류)"
  printf '%s' "$r"
}

# vm_curl <vm> <url> [k] [Host 헤더] → {"ran","code","rc"}: vm-mgmt 관리 ID 로 run-command
vm_curl() {
  local msg="" r
  msg="$("${AZ:-az}" vm run-command invoke -g "$RG" -n "$1" --command-id RunShellScript \
    --scripts "$(curl_cmd "$2" "${3:-}" "${4:-}")" --query 'value[0].message' -o tsv 2>"$WORK/vmerr")" ||
    log "경고: run-command 실패($1): $(tr '\n' ' ' <"$WORK/vmerr")"
  r="$(curl_run "$msg")"
  [[ "$(jq -r .ran <<<"$r")" == true ]] || log "경고: VM $1 에서 curl 이 실행되지 않았습니다(run-command 실패)"
  printf '%s' "$r"
}

pod_dns() {
  local out
  out="$(pod_exec "$1" "$2" "dig +short '$3' A 2>/dev/null; getent hosts '$3' 2>/dev/null")"
  pystr parse first_ipv4 "$(jq -cn --arg o "$out" '{output: $o}')"
}

# 차단(000) 기대 행: curl 이 실제로 실행되지 않았으면 000 이 차단 증거가 아니므로 사유를 남긴다
run_note() {
  local ran rc
  ran="$(jq -r .ran <<<"$1")"
  rc="$(jq -r .rc <<<"$1")"
  if [[ "$ran" != true ]]; then
    if [[ "$rc" == 126 || "$rc" == 127 ]]; then
      echo "curl 없음/실행 불가(rc=$rc) → 차단 판정 불가"
    else
      echo 'curl 미실행(exec/run-command 실패) → 차단 판정 불가'
    fi
  else
    echo "curl rc=$rc"
  fi
}

code_of() { jq -r .code <<<"$1"; }
ran_of() { jq -r .ran <<<"$1"; }

# hv <code> <expect> [control_passed]
hv() {
  pystr verdict http_verdict "$(jq -cn --arg c "$1" --arg e "$2" --argjson p "${3:-true}" '{code: $c, expect: $e, control_passed: $p}')"
}

# tv <ip> <code> <expect> [control_passed] [expected_ip]
tv() {
  pystr verdict tls_verdict "$(jq -cn --arg i "$1" --arg c "$2" --arg e "$3" --argjson p "${4:-true}" --arg x "${5:-}" \
    '{resolved_ip: $i, code: $c, expect: $e, control_passed: $p, expected_ip: $x}')"
}

# row_verdict <Id>: 이미 기록한 행의 판정(없으면 "없음")
row_verdict() {
  jq -rs --arg id "$1" 'map(select(.Id == $id))[0].Verdict // "없음"' "$ROWS"
}

# ---------- 영역 집계 ----------

fmt_counts() { pystr kube fmt_counts "$(jq -cn --argjson c "$1" '{counts: $c}')"; }
fmt_short() { pystr kube fmt_short "$(jq -cn --argjson c "$1" '{counts: $c}')"; }

# pod_zones <ns> <selector> → {"counts","running","pending","names"}
pod_zones() {
  kube_ok get nodes -o json >"$WORK/nodes.json"
  kube_ok get pods -n "$1" -l "$2" -o json >"$WORK/pz.json"
  py kube pod_zone_counts "$(jq -cn --arg n "@$WORK/nodes.json" --arg p "@$WORK/pz.json" '{nodes: $n, pods: $p}')"
}

user_zone_counts() {
  kube_ok get nodes -l kubernetes.azure.com/mode=user -o json >"$WORK/unodes.json"
  py kube node_zone_counts "$(jq -cn --arg n "@$WORK/unodes.json" '{nodes: $n}')"
}

user_nodes_at_min() {
  local c
  c="$(user_zone_counts)" || return 1
  [[ "$(py kube nodes_at_min "$(jq -cn --argjson c "$c" --argjson m "$USER_MIN" '{counts: $c, user_min: $m}')")" == true ]]
}

# 이전 확장의 노드가 남아 있으면 새 Pod 가 기존 노드에 들어가 CAS 확장을 검증하지 못하므로 풀마다 최소 대수가 될 때까지 대기
wait_user_nodes_min() {
  user_nodes_at_min && return 0
  log "사용자 노드가 최소(${USER_MIN}대/풀) 상태가 아님: CAS 축소 대기(최대 25분)"
  wait_cond 1500 30 user_nodes_at_min
}

# event_note <Pod 이름 파일> <pending>: 보조 근거(판정 외). 조회 실패는 판정을 바꾸지 않는다
event_note() {
  local names
  if ! kube get events -n scale -o json >"$WORK/ev.json" 2>"$WORK/everr"; then
    echo "이벤트 조회 실패: $(tr '\n' ' ' <"$WORK/everr")"
    return 0
  fi
  names="$(sort -u "$1" | jq -Rsc 'split("\n") | map(select(length > 0))')"
  pystr kube scale_event_note "$(jq -cn --arg e "@$WORK/ev.json" --argjson n "$names" --argjson p "$2" \
    '{events: $e, pod_names: $n, pending: $p}')"
}

# ---------- 네트워크(TN1~TN12) ----------

network_tests() {
  local ingress_url="http://$INGRESS_IP/" ingress_host=web-a.test
  local blob_fqdn="$STORAGE.blob.core.windows.net"
  local blob_url="https://$blob_fqdn/"
  local bff_a=http://bff-a.app.svc.cluster.local:8080/
  local bff_b=http://bff-b.app.svc.cluster.local:8080/
  local web_b=http://web-b.web.svc.cluster.local:8080/
  local r code v ip cn tn1 tn3 tn5 tn7 tn12 tn4 tn4v tn4n extra passed

  log '== 대조군 =='
  r="$(vm_curl "$VM_APPGW" "$ingress_url" '' "$ingress_host")"; code="$(code_of "$r")"
  tn1="$(hv "$code" 200)"
  add TN1 "VM $VM_APPGW" "$INGRESS_IP:80 Host $ingress_host" 200 "$code" "$tn1"

  # 같은 VM·run-command 경로가 정상인지: 스토리지의 HTTP 오류 코드도 연결 성공으로 본다
  r="$(vm_curl "$VM_OTHER" "$blob_url" k)"; code="$(code_of "$r")"
  if [[ "$code" != 000 ]]; then v=PASS; else v=FAIL; fi
  tn3="$v"
  add TN3 "VM $VM_OTHER" "${blob_fqdn}:443 (TLS)" '연결 성공(≠000)' "$code" "$v"

  r="$(pod_curl web web-a "$bff_a")"; code="$(code_of "$r")"
  tn5="$(hv "$code" 200)"
  add TN5 web/web-a bff-a:8080 200 "$code" "$tn5"

  r="$(pod_curl web web-b "$bff_b")"; code="$(code_of "$r")"
  tn7="$(hv "$code" 200)"
  add TN7 web/web-b bff-b:8080 200 "$code" "$tn7"

  # bff-b 는 31-app-pe-egress.yaml 로 Blob PE(고정 IP)만 허용된다. TN11 과 TN15 의 대조군
  ip="$(pod_dns app bff-b "$blob_fqdn")"
  r="$(pod_curl app bff-b "$blob_url" k)"; code="$(code_of "$r")"
  tn12="$(tv "$ip" "$code" ok true "$BLOB_PE_IP")"
  add TN12 app/bff-b "$blob_fqdn ($ip)" 'TLS 연결 성공(≠000)' "$code" "$tn12" "DNS=$ip"

  log '== 차단 테스트 =='
  r="$(vm_curl "$VM_OTHER" "$ingress_url" '' "$ingress_host")"; code="$(code_of "$r")"
  cn="$(control_note "TN1=$tn1" "TN3=$tn3")"
  passed="$([[ -z "$cn" && "$(ran_of "$r")" == true ]] && echo true || echo false)"
  add TN2 "VM $VM_OTHER" "$INGRESS_IP:80 Host $ingress_host" 'blocked(000)' "$code" \
    "$(hv "$code" blocked "$passed")" "$(join_note "$cn" "$(run_note "$r")")"

  # TN4: TN1 재사용 전에 nsg-ingress Deny 규칙과 snet-ingress 연결을 읽기 전용으로 확인한다
  : >"$ERRF"
  if tn4="$(tn4_check "$tn1")"; then
    tn4v="$(jq -r .verdict <<<"$tn4")"; tn4n="$(jq -r .note <<<"$tn4")"
  else
    tn4v=INCONCLUSIVE; tn4n="NSG 확인 실패: $(tr '\n' ' ' <"$ERRF")"
    : >"$ERRF"
  fi
  add TN4 "VM $VM_APPGW" "$INGRESS_IP:80 (TN1 재사용)" 200 "$(jq -r 'select(.Id == "TN1") | .Observed' "$ROWS")" "$tn4v" "$tn4n"

  r="$(pod_curl web web-a "$bff_b")"; code="$(code_of "$r")"
  cn="$(control_note "TN7=$tn7")"
  add TN6 web/web-a bff-b:8080 403 "$code" "$(hv "$code" 403 "$([[ -z "$cn" ]] && echo true || echo false)")" "$cn"

  r="$(pod_curl web web-b "$bff_a")"; code="$(code_of "$r")"
  cn="$(control_note "TN5=$tn5")"
  add TN8 web/web-b bff-a:8080 403 "$code" "$(hv "$code" 403 "$([[ -z "$cn" ]] && echo true || echo false)")" "$cn"

  # probe 쪽 exec 경로는 대조군(TN5, web-a 출발)으로 검증되지 않으므로 curl 실제 실행 여부도 함께 게이트한다
  r="$(pod_curl nomesh probe "$bff_a")"; code="$(code_of "$r")"
  cn="$(control_note "TN5=$tn5")"
  passed="$([[ -z "$cn" && "$(ran_of "$r")" == true ]] && echo true || echo false)"
  extra=""
  [[ "$code" != 000 && "$code" != 200 ]] && extra="연결은 되었으나 코드 $code (200 아님)"
  add TN9 nomesh/probe bff-a:8080 'blocked(000)' "$code" \
    "$(hv "$code" blocked "$passed")" "$(join_note "$cn" "$(run_note "$r")" "$extra")"

  r="$(pod_curl web web-a "$web_b")"; code="$(code_of "$r")"
  cn="$(control_note "TN1=$tn1")"
  add TN10 web/web-a 'web-b:8080 (Ingress 우회)' 403 "$code" "$(hv "$code" 403 "$([[ -z "$cn" ]] && echo true || echo false)")" "$cn"

  ip="$(pod_dns web web-a "$blob_fqdn")"
  r="$(pod_curl web web-a "$blob_url" k)"; code="$(code_of "$r")"
  cn="$(control_note "TN12=$tn12")"
  passed="$([[ -z "$cn" && "$(ran_of "$r")" == true ]] && echo true || echo false)"
  add TN11 web/web-a "$blob_fqdn ($ip)" 'TLS 연결 실패(000)' "$code" \
    "$(tv "$ip" "$code" fail "$passed")" "$(join_note "DNS=$ip" "$cn" "$(run_note "$r")")"
}

# tn4_check <TN1 판정> → nsg_reuse_verdict JSON. 조건문 안에서 불리므로 실패를 직접 return 한다
tn4_check() {
  local access="" snsg
  if exists network nsg rule show -g "$RG" --nsg-name "$NSG_INGRESS" -n "$NSG_INGRESS_DENY_RULE"; then
    access="$(az_ok network nsg rule show -g "$RG" --nsg-name "$NSG_INGRESS" -n "$NSG_INGRESS_DENY_RULE" --query access -o tsv)" || return 1
  fi
  snsg="$(az_ok network vnet subnet show -g "$RG" --vnet-name "$VNET" -n "$SNET_INGRESS_NAME" --query networkSecurityGroup.id -o tsv)" || return 1
  py verdict nsg_reuse_verdict "$(jq -cn --arg t "$1" --arg a "$access" --arg s "$snsg" --arg n "$NSG_INGRESS" --arg r "$NSG_INGRESS_DENY_RULE" \
    '{tn1_verdict: $t, rule_access: $a, subnet_nsg_id: $s, nsg_name: $n, rule_name: $r}')"
}

# ---------- app 네임스페이스 PE egress 분리(TN15~TN20, 31-app-pe-egress.yaml) ----------

APP_PE_TO='Blob PE 분리·bff-b 기본 egress'
app_pe_tests() {
  local blob_fqdn="$STORAGE.blob.core.windows.net" blob2_fqdn="$STORAGE2.blob.core.windows.net"
  local r code ip cn passed tn16 policy_ts pod_ts after v

  log '== app 네임스페이스 PE egress 분리 =='
  # TN15: bff-a 는 Blob(bff-b 전용) PE 로 나갈 수 없다. 같은 PE 에 bff-b 가 닿는 TN12 가 대조군
  ip="$(pod_dns app bff-a "$blob_fqdn")"
  r="$(pod_curl app bff-a "https://$blob_fqdn/" k)"; code="$(code_of "$r")"
  cn="$(control_note "TN12=$(row_verdict TN12)")"
  passed="$([[ -z "$cn" && "$(ran_of "$r")" == true ]] && echo true || echo false)"
  add TN15 app/bff-a "$blob_fqdn ($ip)" 'TLS 연결 실패(000)' "$code" \
    "$(tv "$ip" "$code" fail "$passed" "$BLOB_PE_IP")" "$(join_note "DNS=$ip" "$cn" "$(run_note "$r")")"

  # TN16: bff-a 는 자기 PE(Blob2)로 나갈 수 있다. TN17 의 대조군
  ip="$(pod_dns app bff-a "$blob2_fqdn")"
  r="$(pod_curl app bff-a "https://$blob2_fqdn/" k)"; code="$(code_of "$r")"
  tn16="$(tv "$ip" "$code" ok true "$BLOB2_PE_IP")"
  add TN16 app/bff-a "$blob2_fqdn ($ip)" 'TLS 연결 성공(≠000)' "$code" "$tn16" "DNS=$ip"

  # TN17: bff-b 는 bff-a 전용 PE(Blob2)로 나갈 수 없다
  ip="$(pod_dns app bff-b "$blob2_fqdn")"
  r="$(pod_curl app bff-b "https://$blob2_fqdn/" k)"; code="$(code_of "$r")"
  cn="$(control_note "TN16=$tn16")"
  passed="$([[ -z "$cn" && "$(ran_of "$r")" == true ]] && echo true || echo false)"
  add TN17 app/bff-b "$blob2_fqdn ($ip)" 'TLS 연결 실패(000)' "$code" \
    "$(tv "$ip" "$code" fail "$passed" "$BLOB2_PE_IP")" "$(join_note "DNS=$ip" "$cn" "$(run_note "$r")")"

  # TN18~TN20: PE 만 좁혔을 뿐 bff-b 의 기본 egress 는 그대로인지
  ip="$(pod_dns app bff-b kubernetes.default.svc.cluster.local)"
  if [[ -n "$ip" ]]; then v=PASS; else v=FAIL; fi
  add TN18 app/bff-b 'CoreDNS (kubernetes.default 해석)' '해석 성공' "${ip:-해석 실패}" "$v"

  # Envoy 는 istiod 에서 xDS 설정·인증서를 받아야 15021 이 200 이 된다. 정책 적용 뒤 기동한 Pod 여야 근거가 된다
  policy_ts="$(kube_ok get networkpolicy -n app app-egress-default -o 'jsonpath={.metadata.creationTimestamp}')"
  pod_ts="$(kube_ok get pods -n app -l app=bff-b -o json | jq -r '[.items[].status.startTime // empty] | min // ""')"
  after="$(py verdict started_after "$(jq -cn --arg p "$pod_ts" --arg c "$policy_ts" '{pod_start: $p, policy_created: $c}')")"
  r="$(pod_curl app bff-b http://localhost:15021/healthz/ready)"; code="$(code_of "$r")"
  cn=""
  [[ "$after" == true ]] || cn="Pod 가 정책 적용 전에 기동(Pod $pod_ts, 정책 $policy_ts): 02-apply 재실행 필요"
  add TN19 app/bff-b 'istiod (Envoy xDS 동기화, :15021 ready)' 200 "$code" \
    "$(hv "$code" 200 "$([[ -z "$cn" ]] && echo true || echo false)")" "$(join_note "$cn" "Pod 기동 $pod_ts")"

  r="$(pod_curl app bff-b https://mcr.microsoft.com/v2/)"; code="$(code_of "$r")"
  if [[ "$code" != 000 ]]; then v=PASS; else v=FAIL; fi
  add TN20 app/bff-b 'mcr.microsoft.com:443 (인터넷)' '연결 성공(≠000)' "$code" "$v" "$(run_note "$r")"
}

# ---------- TN21·TN22: bff-b → 노드(kubelet :10250) 기본 egress ----------
# Cilium 은 노드 IP 를 host(자기 노드)·remote-node(다른 노드) 엔터티로 따로 다루므로
# 표준 NetworkPolicy 의 ipBlock 0.0.0.0/0 이 노드까지 허용하는지 실측한다. 정책 없는 nomesh/probe 가 대조군

NODE_TO='노드 kubelet :10250 (host/remote-node)'
node_egress_tests() {
  local t r c code note
  kube_ok get nodes -o json >"$WORK/nn.json" || return 1
  kube_ok get pods -n app -l app=bff-b -o json >"$WORK/nb.json" || return 1
  t="$(py kube node_targets "$(jq -cn --arg n "@$WORK/nn.json" --arg p "@$WORK/nb.json" '{nodes: $n, pods: $p}')")" || return 1

  log '== bff-b → 노드 egress =='
  local id kind ip node
  for id in TN21 TN22; do
    if [[ "$id" == TN21 ]]; then kind=자기; ip="$(jq -r .own_ip <<<"$t")"; node="$(jq -r .own_node <<<"$t")"
    else kind=다른; ip="$(jq -r .other_ip <<<"$t")"; node="$(jq -r .other_node <<<"$t")"; fi
    r="$(pod_curl app bff-b "https://$ip:10250/healthz" k)"; code="$(code_of "$r")"
    c="$(pod_curl nomesh probe "https://$ip:10250/healthz" k)"
    note="$(join_note "$kind 노드 $node" "대조군 nomesh/probe=$(code_of "$c")" "$(run_note "$r")")"
    add "$id" app/bff-b "$kind 노드 $ip:10250 (kubelet)" '연결 성공(≠000)' "$code" \
      "$(pystr verdict node_reach_verdict "$(jq -cn --arg c "$code" --argjson r "$(ran_of "$r")" --arg cc "$(code_of "$c")" --argjson cr "$(ran_of "$c")" \
        '{code: $c, ran: $r, control_code: $cc, control_ran: $cr}')")" "$note"
  done
}

# ---------- TN13 ----------

TN13_TO='web-a/web-b/bff-a/bff-b readinessProbe'
TN13_EXPECT='Ready + 15020 /app-health/ rewrite'

probe_test() {
  # STRICT mTLS 에서도 readinessProbe 가 Istio rewrite(15020 /app-health/)로 통과해 Ready 인지
  local t ns name v
  log '== readinessProbe rewrite (TN13) =='
  : >"$WORK/probe.jsonl"
  for t in web/web-a web/web-b app/bff-a app/bff-b; do
    ns="${t%/*}"; name="${t#*/}"
    kube_ok get pods -n "$ns" -l "app=$name" -o json >"$WORK/pods.json"
    py kube probe_items "$(jq -cn --arg f "@$WORK/pods.json" --arg ns "$ns" '{pods: $f, ns: $ns}')" >>"$WORK/probe.jsonl"
  done
  v="$(py verdict probe_rewrite_verdict "$(jq -cs '{pods: (add // [])}' "$WORK/probe.jsonl")")"
  add TN13 kubelet "$TN13_TO" "$TN13_EXPECT" "$(jq -r .observed <<<"$v")" "$(jq -r .verdict <<<"$v")" "$(jq -r .reason <<<"$v")"
}

# ---------- TN14 관측 ----------

tn14_observe() {
  # 판정은 로컬(run.sh merge-tn14)이 한다. 관측 실패는 error 로 남겨 INCONCLUSIVE 가 되게 한다
  local out="$VM_ROOT/state/tn14-vm.json" a fqdn ips readyz=false err
  log '== API 서버 사설 확인 (TN14 관측) =='
  rm -f "$out"
  if ! a="$("${AZ:-az}" aks show -g "$RG" -n "$AKS" -o json \
    --query '{private: apiServerAccessProfile.enablePrivateCluster, publicFqdn: apiServerAccessProfile.enablePrivateClusterPublicFqdn, fqdn: privateFqdn}' 2>"$WORK/t15err")"; then
    err="az aks show 실패: $(tr '\n' ' ' <"$WORK/t15err" | head -c 300)"
    jq -n --arg e "$err" '{error: $e, fqdn: ""}' >"$out"
    log "경고: TN14 $err"
    return 0
  fi
  fqdn="$(jq -r '.fqdn // ""' <<<"$a")"
  ips=""
  [[ -n "$fqdn" ]] && ips="$(getent ahostsv4 "$fqdn" 2>/dev/null | awk '{print $1}' | sort -u || true)"
  kube get --raw /readyz >/dev/null 2>&1 && readyz=true
  jq -n --argjson a "$a" --arg ips "$ips" --argjson r "$readyz" \
    '{private_enabled: ($a.private == true), public_fqdn_enabled: ($a.publicFqdn == true),
      vm_ips: ($ips | split("\n") | map(select(length > 0))), readyz_ok: $r, fqdn: ($a.fqdn // "")}' >"$out"
  log "TN14 관측: $(jq -c . "$out")"
}

# ---------- HPA(TS1·TS2) ----------

TS1_TO='scale/hpa-demo (minReplicas 5→2)'
TS1_EXPECT='5→4→3→2, 간격 ≥55초'

hpa_min() { kube_ok patch hpa hpa-demo -n scale --type merge -p "{\"spec\":{\"minReplicas\":$1}}" >/dev/null; }

restore_hpa() {
  if kube patch hpa hpa-demo -n scale --type merge -p '{"spec":{"minReplicas":5,"maxReplicas":5}}' >/dev/null 2>&1; then
    log 'hpa-demo minReplicas·maxReplicas 5 원복'
  else
    log '경고: hpa-demo minReplicas 원복 실패'
  fi
  PENDING_RESTORE=""
}

hpa_replicas() { kube_ok get deploy hpa-demo -n scale -o 'jsonpath={.spec.replicas}'; }

hpa_ready5() {
  local d
  d="$(kube_ok get deploy hpa-demo -n scale -o json)" || return 1
  jq -e '.spec.replicas == 5 and (.status.readyReplicas // 0) == 5' <<<"$d" >/dev/null
}

hpa_running_le2() {
  local z
  z="$(pod_zones scale app=hpa-demo)" || return 1
  jq -e '.running <= 2' <<<"$z" >/dev/null
}

hpa_test() {
  local zb za samples="$WORK/samples.jsonl" rep last patch_ts deadline hvj first observed diff now
  log '== HPA 축소 (TS1) =='
  # 이전 실행이 중간에 끊겼을 수 있으므로 시작 상태를 minReplicas 5로 맞춘 뒤 5개 Ready 확인
  hpa_min 5
  if ! wait_cond 300 5 hpa_ready5; then
    add TS1 '—' "$TS1_TO" "$TS1_EXPECT" '5개 Ready 아님' INCONCLUSIVE '시작 조건(hpa-demo 5개 Ready) 미충족, 5분 대기 초과'
    return 0
  fi

  zb="$(pod_zones scale app=hpa-demo)"
  log "축소 전 hpa-demo Pod 영역 분포: $(fmt_short "$(jq -c .counts <<<"$zb")")"
  : >"$samples"
  rep="$(hpa_replicas)"
  jq -cn --arg t "$(date -Iseconds)" --argjson e "$(date +%s)" --argjson r "$rep" '{time: $t, epoch: $e, replicas: $r}' >>"$samples"
  hpa_min 2
  patch_ts="$(date +%s)"
  log "minReplicas 2 패치: $(date '+%H:%M:%S')"
  deadline=$((patch_ts + 900))
  last="$rep"
  while (($(date +%s) < deadline)); do
    sleep 5
    rep="$(hpa_replicas)"
    now="$(date +%s)"
    jq -cn --arg t "$(date -Iseconds)" --argjson e "$now" --argjson r "$rep" '{time: $t, epoch: $e, replicas: $r}' >>"$samples"
    if [[ "$rep" != "$last" ]]; then
      log "  replicas $last→$rep"
      last="$rep"
    fi
    ((rep <= 2)) && break
  done

  hvj="$(py verdict hpa_verdict "$(jq -cs '{samples: .}' "$samples")")"
  first="$(jq -rs --argjson p "$patch_ts" '.[0].replicas as $f | [.[] | select(.replicas != $f)][0]
    | if . == null then "감소 없음" else "패치→첫 감소 \(.epoch - $p)초(판정 외)" end' "$samples")"
  observed="$(jq -r '"\(.sequence | map(tostring) | join(",")) (간격 \(.intervals_sec | map(round | tostring) | join(","))초)"' <<<"$hvj")"
  add TS1 '—' "$TS1_TO" "$TS1_EXPECT" "$observed" "$(jq -r .verdict <<<"$hvj")" \
    "$(join_note "$(jq -r .reason <<<"$hvj")" "$first" "표본 $(wc -l <"$samples" | tr -d ' ')개/5초")"

  # TS2: 분산 제약(DoNotSchedule)은 배치 때만 적용된다. 축소 후 영역 분포를 기록만 한다(판정 없음)
  wait_cond 90 5 hpa_running_le2 || true
  za="$(pod_zones scale app=hpa-demo)"
  diff="$(jq '[.counts["1"] // 0, .counts["2"] // 0, .counts["3"] // 0] | max - min' <<<"$za")"
  add TS2 '—' 'scale/hpa-demo 영역 분포(DoNotSchedule, minDomains 3)' '기록(판정 없음)' \
    "축소 전 $(fmt_short "$(jq -c .counts <<<"$zb")") → 축소 후 $(fmt_short "$(jq -c .counts <<<"$za")")" INFO \
    "축소 후 영역별 Pod 수 차이 $diff (Running $(jq -r .running <<<"$za")); Pod 2개·영역 3개라 빈 영역 1개는 필연. 축소 때 지울 Pod 선택은 TS3 에서 판정"
}

# ---------- 축소 시 삭제 Pod 선택(TS3) ----------

TS1C_STARTS=(5 9 12)
TS1C_ROUNDS=2
TS1C_TO='scale/hpa-demo (5·9·12개 → minReplicas 3, 각 2회)'
TS1C_EXPECT='삭제된 Pod = ReplicaSet 삭제 규칙 예측'

hpa_set() { kube_ok patch hpa hpa-demo -n scale --type merge -p "{\"spec\":{\"minReplicas\":$1,\"maxReplicas\":$2}}" >/dev/null; }

hpa_running_n() {
  local d z
  d="$(kube_ok get deploy hpa-demo -n scale -o json)" || return 1
  jq -e --argjson n "$1" '.spec.replicas == $n and (.status.readyReplicas // 0) == $n' <<<"$d" >/dev/null || return 1
  z="$(pod_zones scale app=hpa-demo)" || return 1
  jq -e --argjson n "$1" '.running == $n and .pending == 0 and (.names | length) == $n' <<<"$z" >/dev/null
}

hpa_at3() {
  local r z
  r="$(hpa_replicas)" || return 1
  [[ "$r" == 3 ]] || return 1
  z="$(pod_zones scale app=hpa-demo)" || return 1
  jq -e '.running == 3 and (.names | length) == 3' <<<"$z" >/dev/null
}

rs_snapshot() {
  kube_ok get nodes -o json >"$WORK/nodes.json"
  kube_ok get pods -n scale -l app=hpa-demo -o json >"$WORK/rs-pods.json"
  py kube rs_pod_snapshot "$(jq -cn --arg n "@$WORK/nodes.json" --arg p "@$WORK/rs-pods.json" '{nodes: $n, pods: $p}')"
}

floor_test() {
  # 분산 제약은 배치 때만 적용되고, 축소 때 지울 Pod 는 ReplicaSet 삭제 규칙이 고른다.
  # 시작 개수를 바꿔 가며 축소 직전 Pod(노드·Ready 시각·UID 등)를 기록하고, Pod watch 로 잡은 삭제 요청 시각과 규칙 예측을 대조한다
  local start n snap final ev names rounds="$WORK/rounds.jsonl" fv watch="$WORK/rs-watch.tsv"
  # run_test 서브셸은 부모 trap 을 물려받지 않으므로, 중단·종료 시 watch 정리를 여기서 건다
  trap rs_watch_stop EXIT
  trap 'exit 143' TERM INT
  log '== 축소 시 삭제 Pod 선택 (TS3) =='
  : >"$rounds"
  for start in "${TS1C_STARTS[@]}"; do
    for ((n = 1; n <= TS1C_ROUNDS; n++)); do
      hpa_set "$start" "$start"
      if ! wait_cond 600 5 hpa_running_n "$start"; then
        add TS3 '—' "$TS1C_TO" "$TS1C_EXPECT" "${start}개 ${n}회 시작 조건 미충족" INCONCLUSIVE "hpa-demo ${start}개 Ready 아님, 10분 대기 초과"
        return 0
      fi
      snap="$(rs_snapshot)"
      log "  ${start}개 ${n}회 축소 전: Pod $(fmt_short "$(jq -c 'map(.zone) | group_by(.) | map({(.[0]): length}) | add' <<<"$snap")"), 노드 $(fmt_short "$(user_zone_counts)")"
      : >"$watch"
      rs_watch_start "$watch"
      sleep 3
      hpa_set 3 "$start"
      wait_cond 1500 5 hpa_at3 || true
      sleep 5
      rs_watch_stop
      final="$(rs_snapshot)"
      names="$(jq -c 'map(.name)' <<<"$snap")"
      jq -Rn '[inputs | gsub("\\s+$"; "") | split("|")]' "$watch" >"$WORK/rs-watch.json"
      ev="$(py kube rs_delete_times "$(jq -cn --arg r "@$WORK/rs-watch.json" --argjson n "$names" '{rows: $r, names: $n}')")"
      log "  ${start}개 ${n}회 삭제 순서: $(jq -r 'map(.name | split("-") | last) | join(" → ")' <<<"$ev")"
      jq -cn --argjson s "$start" --argjson p "$snap" --argjson d "$ev" --argjson f "$(jq -c 'map(.name)' <<<"$final")" \
        '{start: $s, pods: $p, deletes: $d, final: $f}' >>"$rounds"
    done
  done
  jq -cs '.' "$rounds" >"$WORK/rounds.json"
  cp "$WORK/rounds.json" "$VM_ROOT/results/ts3-rounds-$(date +%Y%m%d-%H%M%S).json"
  fv="$(py verdict deletion_rule_verdict "$(jq -cn --arg r "@$WORK/rounds.json" '{rounds: $r}')")"
  add TS3 '—' "$TS1C_TO" "$TS1C_EXPECT" "$(jq -r .observed <<<"$fv")" "$(jq -r .verdict <<<"$fv")" \
    "$(join_note "$(jq -r .reason <<<"$fv")" "사용자 노드 $(fmt_short "$(user_zone_counts)")")"
}

# ---------- 영역 균형(TS4) ----------

TS4_TO="사용자 노드 풀 $POOLS_TEXT"
TS4_EXPECT='확장 후 영역별 노드 수 차이 ≤1'

restore_zone_fill() {
  if kube scale deploy zone-fill -n scale --replicas=0 >/dev/null 2>&1; then
    log 'zone-fill 0으로 원복(CAS 축소는 10분 이상 걸리며 판정하지 않음)'
  else
    log '경고: zone-fill 원복 실패'
  fi
  PENDING_RESTORE=""
}

zone_test() {
  local target=6 zs setting before after now start deadline running pending elapsed zv ev cap note names="$WORK/zf-names"
  log '== 영역 균형 (TS4) =='
  az_ok aks show -g "$RG" -n "$AKS" -o json >"$WORK/aks.json"
  zs="$(py kube aks_zone_setting "$(jq -cn --arg a "@$WORK/aks.json" '{aks: $a, pools: $ARGS.positional}' --args "${USER_POOLS[@]}")")"
  setting="$(jq -r .setting <<<"$zs")"
  log "설정 확인: $setting"
  if [[ "$(jq -r .ok <<<"$zs")" != true ]]; then
    add TS4 '—' "$TS4_TO" "$TS4_EXPECT" '설정 불일치' INCONCLUSIVE "대조군(설정 확인) 실패: $setting"
    return 0
  fi
  if ! wait_user_nodes_min; then
    now="$(user_zone_counts)"
    add TS4 '—' "$TS4_TO" "$TS4_EXPECT" "$(fmt_counts "$now")" INCONCLUSIVE "시작 조건 미충족: 사용자 노드가 풀당 ${USER_MIN}대로 돌아오지 않음(25분 대기)"
    return 0
  fi
  before="$(user_zone_counts)"
  log "확장 전 사용자 노드: $(fmt_counts "$before")"
  start="$(date +%s)"
  kube_ok scale deploy zone-fill -n scale "--replicas=$target" >/dev/null
  : >"$names"
  deadline=$((start + 900))
  while true; do
    kube_ok get pods -n scale -l app=zone-fill -o json >"$WORK/zf.json"
    jq -r '.items[].metadata.name' "$WORK/zf.json" >>"$names"
    running="$(jq '[.items[] | select(.status.phase == "Running" and (.metadata.deletionTimestamp | not))] | length' "$WORK/zf.json")"
    log "  zone-fill Running $running/$target, 사용자 노드 $(fmt_counts "$(user_zone_counts)")"
    ((running >= target || $(date +%s) >= deadline)) && break
    sleep 15
  done
  elapsed=$(($(date +%s) - start))
  pending=$((target - running > 0 ? target - running : 0))

  after="$(user_zone_counts)"
  zv="$(py verdict zone_balance_verdict "$(jq -cn --argjson a "$after" --argjson p "$pending" --argjson b "$(jq 'add // 0' <<<"$before")" \
    '{nodes_by_zone: $a, pending: $p, nodes_before: $b}')")"
  ev="$(event_note "$names" "$pending")"
  cap=""
  if jq -e --argjson m "$USER_MAX" '[.["1"], .["2"], .["3"]] | all(. != null and . >= $m)' <<<"$after" >/dev/null; then
    cap="모든 풀이 최대(${USER_MAX}대)에 도달: 균형이 최대값 제한으로 강제되었을 수 있음"
  fi
  note="$(join_note "$(jq -r .reason <<<"$zv")" "확장 전 $(fmt_counts "$before")" "Running $running/$target, ${elapsed}초" "$cap" "$ev" "설정: $setting")"
  add TS4 '—' "$TS4_TO" "$TS4_EXPECT" "$(fmt_counts "$after")" "$(jq -r .verdict <<<"$zv")" "$note"
}

# ---------- 영역 강제 분산(TS5) ----------

TS4B_TO='scale/zone-spread 6개(DoNotSchedule, minDomains 3)'
TS4B_EXPECT='Pod 2/2/2, Pending 없음'

restore_zone_spread() {
  if kube scale deploy zone-spread -n scale --replicas=0 >/dev/null 2>&1; then
    log 'zone-spread 0으로 원복'
  else
    log '경고: zone-spread 원복 실패'
  fi
  PENDING_RESTORE=""
}

spread_test() {
  # PRD 제안 YAML 의 영역 강제 분산(DoNotSchedule + minDomains 3)이 CAS 확장과 함께 Pod 2/2/2 를 만드는지
  local target=6 now before after start deadline pc elapsed sv ev observed names="$WORK/zs-names"
  log '== 영역 강제 분산 + CAS (TS5) =='
  if ! wait_user_nodes_min; then
    now="$(user_zone_counts)"
    add TS5 '—' "$TS4B_TO" "$TS4B_EXPECT" "노드 $(fmt_short "$now")" INCONCLUSIVE "시작 조건 미충족: 사용자 노드가 풀당 ${USER_MIN}대로 돌아오지 않음(25분 대기)"
    return 0
  fi
  before="$(user_zone_counts)"
  log "확장 전 사용자 노드: $(fmt_counts "$before")"
  start="$(date +%s)"
  kube_ok scale deploy zone-spread -n scale "--replicas=$target" >/dev/null
  : >"$names"
  deadline=$((start + 900))
  while true; do
    sleep 15
    pc="$(pod_zones scale app=zone-spread)"
    jq -r '.names[]' <<<"$pc" >>"$names"
    log "  zone-spread Running $(jq -r .running <<<"$pc")/$target (Pod $(fmt_short "$(jq -c .counts <<<"$pc")"), Pending $(jq -r .pending <<<"$pc")), 사용자 노드 $(fmt_short "$(user_zone_counts)")"
    (($(jq -r .running <<<"$pc") >= target || $(date +%s) >= deadline)) && break
  done
  elapsed=$(($(date +%s) - start))
  after="$(user_zone_counts)"
  sv="$(py verdict pod_spread_verdict "$(jq -cn --argjson c "$(jq -c .counts <<<"$pc")" --argjson p "$(jq -r .pending <<<"$pc")" --argjson e "$target" \
    '{pods_by_zone: $c, pending: $p, expected: $e}')")"
  ev="$(event_note "$names" "$(jq -r .pending <<<"$pc")")"
  observed="Pod $(fmt_short "$(jq -c .counts <<<"$pc")"), 노드 $(fmt_short "$before")→$(fmt_short "$after")"
  add TS5 '—' "$TS4B_TO" "$TS4B_EXPECT" "$observed" "$(jq -r .verdict <<<"$sv")" \
    "$(join_note "$(jq -r .reason <<<"$sv")" "Running $(jq -r .running <<<"$pc")/$target, ${elapsed}초" "$ev")"
}

# ---------- 실행 ----------

if [[ "$ONLY" == all || "$ONLY" == network ]]; then
  run_test TN1 "VM $VM_APPGW" "$INGRESS_IP:80 Host web-a.test" 200 network_tests TN2 TN3 TN4 TN5 TN6 TN7 TN8 TN9 TN10 TN11 TN12
  run_test TN15 app/bff-a "$APP_PE_TO" 'PE 분리 + 기본 egress 유지' app_pe_tests TN16 TN17 TN18 TN19 TN20
  run_test TN21 app/bff-b "$NODE_TO" '연결 성공(≠000)' node_egress_tests TN22
  run_test TN13 kubelet "$TN13_TO" "$TN13_EXPECT" probe_test
  tn14_observe
fi
if [[ "$ONLY" == all || "$ONLY" == hpa ]]; then
  PENDING_RESTORE=restore_hpa
  run_test TS1 '—' "$TS1_TO" "$TS1_EXPECT" hpa_test TS2
  restore_hpa
fi
if [[ "$ONLY" == all || "$ONLY" == floor ]]; then
  PENDING_RESTORE=restore_hpa
  run_test TS3 '—' "$TS1C_TO" "$TS1C_EXPECT" floor_test
  restore_hpa
fi
if [[ "$ONLY" == all || "$ONLY" == zone ]]; then
  PENDING_RESTORE=restore_zone_fill
  run_test TS4 '—' "$TS4_TO" "$TS4_EXPECT" zone_test
  restore_zone_fill
fi
if [[ "$ONLY" == all || "$ONLY" == spread ]]; then
  PENDING_RESTORE=restore_zone_spread
  run_test TS5 '—' "$TS4B_TO" "$TS4B_EXPECT" spread_test
  restore_zone_spread
fi

saved="$(py report save "$VM_ROOT/results" <"$ROWS")"
stamp="$(basename "$(jq -r .json <<<"$saved")" .json)"
echo "$stamp" >"$VM_ROOT/state/latest"
log "결과 저장: $(jq -r .json <<<"$saved")"
log "판정 요약: $(jq -sr 'group_by(.Verdict) | map("\(.[0].Verdict) \(length)") | join(", ")' "$ROWS")"
