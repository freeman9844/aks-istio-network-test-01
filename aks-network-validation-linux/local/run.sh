#!/usr/bin/env bash
# 로컬 얇은 실행기: run.sh <infra|apply|verify> [--only all|network|hpa|floor|zone|spread|zonal] [--poll N]
# 코드 번들을 vm-mgmt 에 올리고 단계 스크립트를 systemd-run 으로 백그라운드 실행한 뒤 로그를 따라간다.
# 같은 단계가 이미 실행 중이면 새로 시작하지 않고 따라가기만 한다(로컬 세션이 끊겨도 다시 실행하면 이어 봄).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=../config.sh
source "$ROOT/config.sh"
# shellcheck source=lib/vmexec.sh
source "$HERE/lib/vmexec.sh"

usage() {
  echo "사용법: $0 <infra|apply|verify> [--only all|network|hpa|floor|zone|spread|zonal] [--poll 초]" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
case "$1" in
  infra) STAGE="01-infra" ;;
  apply) STAGE="02-apply" ;;
  verify) STAGE="03-verify" ;;
  *) usage ;;
esac
shift
ONLY="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --only) ONLY="${2:-}"; shift 2 ;;
    --poll) POLL="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
case "$ONLY" in all | network | hpa | floor | zone | spread | zonal) ;; *) usage ;; esac
[[ "${POLL:-30}" =~ ^[0-9]+$ ]] || usage
export POLL="${POLL:-30}"

build_bundle() {
  local out="$1" stage_dir
  stage_dir="$(mktemp -d)"
  cp -r "$ROOT/aksnetval" "$ROOT/vm" "$ROOT/config.sh" "$stage_dir/"
  cp -r "$MANIFESTS_DIR" "$stage_dir/manifests"
  find "$stage_dir" -name '__pycache__' -type d -prune -exec rm -rf {} +
  tar -czf "$out" -C "$stage_dir" .
  rm -rf "$stage_dir"
}

state="$(stage_state "$STAGE" || true)"
action="$(stage_action "$state")"
if [[ "$action" == follow ]]; then
  echo "$STAGE 가 이미 실행 중입니다($state). 새로 시작하지 않고 로그를 따라갑니다."
else
  mkdir -p "$ROOT/.bundle"
  bundle="$ROOT/.bundle/bundle.tgz"
  build_bundle "$bundle"
  echo "코드 번들 업로드: $(du -h "$bundle" | cut -f1)"
  upload_bundle "$bundle"
  args=()
  [[ "$STAGE" == 03-verify ]] && args=(--only "$ONLY")
  echo "$STAGE 시작(vm-mgmt, systemd 유닛 aksnetval-$STAGE)"
  start_stage "$STAGE" "${args[@]}" >/dev/null || { echo "오류: $STAGE 시작 실패" >&2; exit 1; }
fi

rc=0
poll_stage "$STAGE" || rc=$?
if (( rc != 0 )); then
  echo "오류: $STAGE 실패(rc=$rc). vm-mgmt 의 $VM_ROOT/logs/$STAGE.log 를 확인하세요." >&2
  exit "$rc"
fi
echo "$STAGE 완료"

if [[ "$STAGE" == 03-verify ]]; then
  latest="$(vm_exec "ls -1t $(printf '%q' "$VM_ROOT")/results/*.json 2>/dev/null | head -n 1")"
  [[ -n "$latest" ]] || { echo "오류: vm-mgmt 에 결과 JSON 이 없습니다" >&2; exit 1; }
  results="$ROOT/results"
  name="$(basename "$latest" .json)"
  fetch_file "$latest" "$results/$name.json"
  fetch_file "${latest%.json}.md" "$results/$name.md"
  if [[ "$ONLY" == all || "$ONLY" == network ]]; then
    fetch_file "$VM_ROOT/state/tn14-vm.json" "$results/$name.tn14-vm.json"
    fqdn="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["fqdn"])' "$results/$name.tn14-vm.json")"
    (cd "$ROOT" && python3 -m aksnetval.report merge-tn14 "$results/$name.json" "$results/$name.tn14-vm.json" "$fqdn") >/dev/null
  fi
  if [[ "$ONLY" == all || "$ONLY" == zonal ]]; then
    # 03-verify 가 TS6 시작 때 지우고 4회 모두 끝나면 쓰므로, 있으면 이번 실행 것이다
    if ! fetch_file "$VM_ROOT/state/ts6-rounds.json" "$results/$name.ts6-rounds.json"; then
      echo "경고: TS6 원시 표본(state/ts6-rounds.json)이 없거나 가져오지 못했습니다" >&2
    fi
  fi
  (cd "$ROOT" && python3 - "$results/$name.json" <<'PY'
import collections, json, sys
rows = json.load(open(sys.argv[1], encoding="utf-8"))
for r in rows:
    print(f"{r['Id']:<5} {r['Verdict']:<12} {r['Observed']}")
c = collections.Counter(r["Verdict"] for r in rows)
print("요약: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
PY
  )
  echo "결과: $results/$name.md"
fi
