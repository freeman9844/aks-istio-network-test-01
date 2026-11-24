"""판정 함수(기존 Verdict.psm1 대응). 모두 순수 함수이고 사유 문구는 원본과 같다."""
import ipaddress
import math
import sys
from datetime import datetime, timedelta
from functools import cmp_to_key

from aksnetval import _cli

ZONES = ("1", "2", "3")
PE_NET = ipaddress.IPv4Network("10.50.2.0/24")
AKS_NET = ipaddress.IPv4Network("10.50.4.0/22")


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def _counts(by_zone: dict) -> list[int]:
    return [int(by_zone.get(z, 0)) for z in ZONES]


def http_verdict(code: str, expect: str, control_passed: bool = True) -> str:
    if expect not in ("200", "403", "blocked"):
        raise ValueError(f"expect 값이 올바르지 않습니다: {expect}")
    # 대조군이 실패하면 차단의 원인을 알 수 없으므로 결과와 무관하게 INCONCLUSIVE
    if not control_passed:
        return "INCONCLUSIVE"
    expected = "000" if expect == "blocked" else expect
    return "PASS" if code == expected else "FAIL"


def is_pe_ip(ip: str) -> bool:
    try:
        return ipaddress.IPv4Address(ip) in PE_NET
    except ValueError:
        return False


def tls_verdict(resolved_ip: str, code: str, expect: str, control_passed: bool = True, expected_ip: str = "") -> str:
    if expect not in ("ok", "fail"):
        raise ValueError(f"expect 값이 올바르지 않습니다: {expect}")
    if not control_passed:
        return "INCONCLUSIVE"
    # DNS가 PE IP로 해석되지 않으면 NetworkPolicy 경로를 검증한 것이 아님
    if not is_pe_ip(resolved_ip):
        return "INCONCLUSIVE"
    # /32 허용 정책은 고정 PE IP 기준이므로 다른 IP 로 해석되면 그 정책을 검증한 것이 아님
    if expected_ip and resolved_ip != expected_ip:
        return "INCONCLUSIVE"
    connected = code != "000"
    ok = connected if expect == "ok" else not connected
    return "PASS" if ok else "FAIL"


def node_reach_verdict(code: str, ran: bool, control_code: str, control_ran: bool) -> str:
    # 대조군(정책 없는 Pod)이 노드에 닿아야 비교 대상 Pod 의 000 을 정책 차단으로 볼 수 있다
    if not (control_ran and control_code != "000") or not ran:
        return "INCONCLUSIVE"
    return "PASS" if code != "000" else "FAIL"


def started_after(pod_start: str, policy_created: str) -> bool:
    # Python 3.10(Ubuntu 22.04)의 fromisoformat 은 'Z' 를 받지 않는다
    def parse(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    try:
        return parse(pod_start) > parse(policy_created)
    except (AttributeError, TypeError, ValueError):
        return False


def hpa_verdict(samples: list[dict]) -> dict:
    expected_seq = [5, 4, 3, 2]
    min_interval = 55
    ordered = sorted(samples, key=lambda s: datetime.fromisoformat(s["time"]))
    seq: list[int] = []
    change_times: list[datetime] = []
    # 연속 중복 제거: 값이 바뀐 시각만 남긴다
    for s in ordered:
        rep = int(s["replicas"])
        if not seq:
            seq.append(rep)
        elif rep != seq[-1]:
            seq.append(rep)
            change_times.append(datetime.fromisoformat(s["time"]))
    intervals = [(b - a).total_seconds() for a, b in zip(change_times, change_times[1:])]
    seq_text = ",".join(map(str, seq))
    iv_text = ",".join(_num(i) for i in intervals)

    reason = None
    if not seq:
        reason = "표본이 없습니다"
    elif 2 not in seq:
        reason = f"레플리카가 2에 도달하지 않았습니다 (관측 순서: {seq_text})"
    elif seq != expected_seq:
        reason = f"레플리카 순서가 5,4,3,2가 아닙니다 (관측 순서: {seq_text})"
    elif any(i < min_interval for i in intervals):
        reason = f"감소 간격이 {min_interval}초 미만입니다 (간격: {iv_text}초)"
    verdict = "FAIL" if reason else "PASS"
    if not reason:
        reason = f"5→4→3→2 순서로 감소, 간격 {iv_text}초"
    return {"verdict": verdict, "sequence": seq, "intervals_sec": intervals, "reason": reason}


def zone_balance_verdict(nodes_by_zone: dict, pending: int, nodes_before: int = -1) -> dict:
    c = _counts(nodes_by_zone)
    summary = f"영역1={c[0]}, 영역2={c[1]}, 영역3={c[2]}"
    # 확장 없이 Pending 이 남는 경우(할당량 부족 등)는 FAIL 이므로 Pending 을 먼저 본다
    if pending > 0:
        return {"verdict": "FAIL", "reason": f"Pending Pod가 {pending}개 남아 있습니다 ({summary})"}
    total = sum(c)
    if nodes_before >= 0 and total <= nodes_before:
        return {"verdict": "INCONCLUSIVE", "reason": f"CAS 확장 없음: 사용자 노드 {nodes_before} → {total}대 ({summary})"}
    diff = max(c) - min(c)
    if diff <= 1:
        return {"verdict": "PASS", "reason": f"영역별 노드 수 차이 {diff} ≤ 1 ({summary})"}
    return {"verdict": "FAIL", "reason": f"영역별 노드 수 차이 {diff} > 1 ({summary})"}


def nsg_reuse_verdict(tn1_verdict: str, rule_access: str | None, subnet_nsg_id: str | None, nsg_name: str, rule_name: str) -> dict:
    # TN4: nsg-ingress 에 Deny 규칙이 있고 snet-ingress 에 연결된 것이 확인될 때만 TN1 판정을 재사용한다
    if rule_access != "Deny":
        got = f"access={rule_access}" if rule_access else "없음"
        return {"verdict": "INCONCLUSIVE", "note": f"{nsg_name}/{rule_name} Deny 규칙 확인 실패({got}): TN1 재사용 불가"}
    attached = subnet_nsg_id.rstrip("/").split("/")[-1] if subnet_nsg_id else ""
    if attached != nsg_name:
        got = attached or "없음"
        return {"verdict": "INCONCLUSIVE", "note": f"서브넷 NSG 연결이 {nsg_name} 이 아님(연결된 NSG: {got}): TN1 재사용 불가"}
    return {"verdict": tn1_verdict, "note": f"{nsg_name}/{rule_name} Deny 규칙과 서브넷 연결 확인, TN1 결과 재사용"}


def pod_spread_verdict(pods_by_zone: dict, pending: int, expected: int) -> dict:
    c = _counts(pods_by_zone)
    summary = f"Pod 영역1={c[0]}, 영역2={c[1]}, 영역3={c[2]}"
    if pending > 0:
        return {"verdict": "FAIL", "reason": f"Pending Pod가 {pending}개 남아 있습니다 ({summary})"}
    total = sum(c)
    if total != expected:
        return {"verdict": "INCONCLUSIVE", "reason": f"Running Pod {total}/{expected} ({summary})"}
    diff = max(c) - min(c)
    ok = diff <= 1
    return {"verdict": "PASS" if ok else "FAIL", "reason": f"영역별 Pod 수 차이 {diff} {'≤' if ok else '>'} 1 ({summary})"}


_PHASE_ORD = {"Pending": 0, "Unknown": 1, "Running": 2}


def _ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _log_rank_diff(t1: datetime, t2: datetime, now: datetime) -> int:
    # k8s logarithmicRankDiff: 경과 시간(ns)의 log2 정수부 비교
    def r(t):
        d = (now - t).total_seconds() * 1e9
        return int(math.log2(d)) if d > 0 else -1
    return r(t1) - r(t2)


def _rs_cmp(a: dict, b: dict, rank: dict, now: datetime) -> tuple[int, str]:
    """ActivePodsWithRanks.Less 이식. (-1: a 먼저 삭제, 1: b 먼저, 0: 동률, 결정 규칙)."""
    def pick(a_first: bool, rule: str):
        return (-1 if a_first else 1), rule
    if a["node"] != b["node"] and (not a["node"] or not b["node"]):
        return pick(not a["node"], "1")
    pa, pb = _PHASE_ORD.get(a["phase"], 0), _PHASE_ORD.get(b["phase"], 0)
    if pa != pb:
        return pick(pa < pb, "2")
    if a["ready"] != b["ready"]:
        return pick(not a["ready"], "3")
    if a.get("cost", 0) != b.get("cost", 0):
        return pick(a.get("cost", 0) < b.get("cost", 0), "4")
    ra, rb = rank[a["node"]], rank[b["node"]]
    if ra != rb:
        return pick(ra > rb, "5")
    if a["ready"] and b["ready"]:
        r = _time_cmp(a, b, "ready_time", "6", now)
        if r:
            return r
    if a.get("restarts", 0) != b.get("restarts", 0):
        return pick(a.get("restarts", 0) > b.get("restarts", 0), "7")
    return _time_cmp(a, b, "created", "8", now) or (0, "동률")


def _time_cmp(a: dict, b: dict, key: str, rule: str, now: datetime) -> tuple[int, str] | None:
    # 6·8번: 시각이 다르면 로그 구간 비교, 같은 구간이면 UID 사전순. 시각이 같으면 다음 규칙으로
    ta, tb = _ts(a.get(key)), _ts(b.get(key))
    if ta == tb:
        return None
    if ta is None or tb is None:
        return (-1 if ta is None else 1), rule
    diff = _log_rank_diff(ta, tb, now)
    if diff != 0:
        return (-1 if diff < 0 else 1), rule
    if a["uid"] == b["uid"]:
        return 0, rule + "-UID"
    return (-1 if a["uid"] < b["uid"] else 1), rule + "-UID"


def rs_victims(pods: list[dict], now) -> dict:
    """ReplicaSet 이 1개를 줄일 때 지울 Pod(동률이면 여러 개)와, 다음 후보와 갈린 규칙 번호."""
    now = _ts(now) if isinstance(now, str) else now
    rank: dict[str, int] = {}
    for p in pods:
        rank[p["node"]] = rank.get(p["node"], 0) + 1
    order = sorted(pods, key=cmp_to_key(lambda a, b: _rs_cmp(a, b, rank, now)[0]))
    first = order[0]
    victims = sorted(p["name"] for p in order if _rs_cmp(first, p, rank, now)[0] == 0)
    rule = _rs_cmp(first, order[1], rank, now)[1] if len(order) > 1 else ""
    return {"victims": victims, "rule": rule}


def _zone_text(pods: list[dict]) -> str:
    c: dict[str, int] = {}
    for p in pods:
        c[p["zone"]] = c.get(p["zone"], 0) + 1
    return "/".join(str(c.get(z, 0)) for z in ("1", "2", "3"))


def _node_rank(pods: list[dict]) -> dict[str, int]:
    rank: dict[str, int] = {}
    for p in pods:
        rank[p["node"]] = rank.get(p["node"], 0) + 1
    return rank


def _rs_first_info(pods: list[dict], now, rank: dict | None = None) -> tuple[set[str], set[str]]:
    """(맨 앞에 올 수 있는 Pod, 그중 다른 Pod 에 앞섬을 당하는데도 동률 비이행 때문에 가능한 Pod)."""
    now = _ts(now) if isinstance(now, str) else now
    rank = rank or _node_rank(pods)

    def lt(a, b):
        return _rs_cmp(a, b, rank, now)[0] < 0

    possible, beaten = set(), set()
    for x in pods:
        if not any(lt(y, x) for y in pods):
            possible.add(x["name"])
        elif len(pods) <= 12 and _insertion_can_lead(x, pods, lt):
            # Go 정렬은 12개 이하에서만 삽입 정렬이라 이 추론이 성립한다
            possible.add(x["name"])
            beaten.add(x["name"])
    return possible, beaten


def _insertion_can_lead(x: dict, pods: list[dict], lt) -> bool:
    # x 를 맨 앞에 두고, 삽입 정렬로 넣어도 x 를 넘지 않는 Pod 를 더 넣을 수 없을 때까지 넣는다.
    # 삽입은 기존 순서를 유지하고 멈출 자리만 늘리므로, 한 번 넣을 수 있던 Pod 는 계속 넣을 수 있다(탐욕으로 충분)
    order, rest = [x], [p for p in pods if p is not x]
    grew = True
    while rest and grew:
        grew = False
        for e in list(rest):
            j = len(order)
            while j > 0 and lt(e, order[j - 1]):
                j -= 1
            if j > 0:
                order.insert(j, e)
                rest.remove(e)
                grew = True
    return not rest


def rs_possible_first(pods: list[dict], now) -> set[str]:
    """1개를 지울 때 맨 앞에 올 수 있는 Pod.

    Ready·생성 시각이 같으면 6·8번을 건너뛰어 완전 동률이 되는데, 1초 차이 Pod 와는 UID 로 갈리므로
    비교가 비이행적일 수 있다(a = c, a < b < c). Pod 가 12개 이하면 Go 정렬은 삽입 정렬이라
    입력(인포머 캐시) 순서에 따라 결과가 달라진다. x 를 맨 앞에 둔 입력 순서 중 삽입 정렬 뒤에도 x 가 맨 앞에 남는 순서가
    있으면(막는 Pod 가 사슬로 이어져도 됨) x 가 맨 앞이 될 수 있다.
    """
    return _rs_first_info(pods, now)[0]


def _batches(deletes: list[dict], gap: float = 2.0) -> list[list[dict]]:
    # 요청 시각이 gap 초 안에 몰린 삭제는 한 묶음으로 판정한다(한 번 동기화 또는 1개씩 동기화)
    out: list[list[dict]] = []
    for d in sorted(deletes, key=lambda d: _ts(d["time"])):
        if out and (_ts(d["time"]) - _ts(out[-1][0]["time"])).total_seconds() <= gap:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def _batch_explained(pods: list[dict], names: set[str], now) -> int:
    """names 를 지운 것이 규칙으로 설명되는지. 2: 동률 비이행 없이 설명, 1: 비이행 동률로만 설명, 0: 설명 불가.
    한 번의 동기화(정렬 1회, 노드별 개수 고정)와 1개씩 별도 동기화(노드별 개수 재계산) 두 해석을 모두 본다.
    정렬 1회에서 앞에서부터 k개는 '남은 Pod 중 맨 앞에 올 수 있는 Pod'를 차례로 고른 결과와 같다."""
    best = 0
    for fixed in (True, False):
        base_rank = _node_rank(pods) if fixed else None
        memo: dict[frozenset, int] = {}

        def go(left: frozenset) -> int:
            if not left:
                return 2
            if left in memo:
                return memo[left]
            rest = [p for p in pods if p["name"] not in names or p["name"] in left]
            possible, beaten = _rs_first_info(rest, now, base_rank)
            r = 0
            for n in left & possible:
                sub = go(left - {n})
                r = max(r, sub if n not in beaten else min(sub, 1))
                if r == 2:
                    break
            memo[left] = r
            return r

        best = max(best, go(frozenset(names)))
        if best == 2:
            break
    return best


def deletion_rule_verdict(rounds: list[dict], target: int = 3) -> dict:
    """TS3: 축소 때 실제 삭제된 Pod 가 ReplicaSet 삭제 규칙(1~8번)으로 계산한 예측과 같은지.
    삭제 시각은 초 단위라 컨트롤러의 now 를 -3~+1초 범위로 흔들어, 로그 구간 경계에서 갈리면 후보를 모두 인정한다.
    2초 안에 몰린 삭제는 한 번의 동기화(정렬 1회, 노드별 개수 재계산 없음)나 1개씩 별도 동기화 어느 쪽으로든 설명되면 인정한다.
    삭제 기록이 빠진 회차는 남은 Pod 가 이후 예측을 흔들므로 판정하지 않는다(INCONCLUSIVE)."""
    if not rounds:
        return {"verdict": "INCONCLUSIVE", "observed": "회차 없음", "reason": "측정한 회차 없음"}
    observed, problems, mismatches = [], [], []
    steps = boundary = nontrans = 0
    multi: dict[int, int] = {}
    for i, rd in enumerate(rounds, 1):
        pods = {p["name"]: p for p in rd["pods"]}
        label = f"{i}회({rd['start']}개)"
        after = [pods[n] for n in rd["final"] if n in pods]
        observed.append(f"{rd['start']}개 {_zone_text(rd['pods'])}→{_zone_text(after)}")
        deleted = [d["name"] for d in rd["deletes"]]
        unknown = set(deleted) - set(pods)
        if unknown:
            problems.append(f"{label} 스냅숏에 없는 Pod 삭제: {','.join(sorted(unknown))}")
            continue
        if len(deleted) != rd["start"] - target:
            problems.append(f"{label} 삭제 {len(deleted)}건(기대 {rd['start'] - target}건)")
            continue
        if sorted(rd["final"]) != sorted(set(pods) - set(deleted)):
            problems.append(f"{label} 최종 Pod 가 예상과 다름")
            continue
        active = dict(pods)
        for batch in _batches(rd["deletes"]):
            names = {d["name"] for d in batch}
            k = len(batch)
            if k > 1:
                multi[k] = multi.get(k, 0) + 1
            t = _ts(batch[0]["time"])
            res = 0
            union: set[str] = set()
            for j in range(-12, 5):
                now_j = t + timedelta(seconds=j * 0.25)
                possible, beaten = _rs_first_info(list(active.values()), now_j)
                union |= possible - beaten
                if res < 2:
                    res = max(res, _batch_explained(list(active.values()), names, now_j))
            if not res:
                mismatches.append(f"{label} 실제 {','.join(sorted(names))} / 예측 후보 {','.join(sorted(union))}")
            else:
                steps += k
                nontrans += res == 1
                boundary += k == 1 and len(union) > 1
            for n in names:
                del active[n]
    obs = ", ".join(observed)
    if mismatches:
        return {"verdict": "FAIL", "observed": obs, "reason": "예측과 다른 삭제: " + "; ".join(mismatches)}
    if problems:
        return {"verdict": "INCONCLUSIVE", "observed": obs, "reason": "; ".join(problems)}
    edge = "".join(f"({t} {n}건 포함)" for t, n in (("로그 구간 경계·완전 동률로 후보 2개 이상", boundary), ("Ready 동률 비이행 비교", nontrans)) if n)
    batch_text = "; " + ", ".join(f"한 번에 {k}개 삭제 {v}회" for k, v in sorted(multi.items())) if multi else ""
    return {"verdict": "PASS", "observed": obs,
            "reason": f"삭제 {steps}건 모두 ReplicaSet 규칙 예측과 일치{edge}{batch_text}"}


def probe_rewrite_verdict(pods: list[dict]) -> dict:
    # TN13: 각 Pod 의 app 컨테이너 readinessProbe 가 Istio 에 의해 15020 /app-health/ 로 바뀌었고 Ready 인지
    n = len(pods)
    if n == 0:
        return {"verdict": "INCONCLUSIVE", "observed": "Pod 없음", "reason": "대상 Pod 없음"}

    def rewritten(p):
        return str(p.get("port")) == "15020" and str(p.get("path") or "").startswith("/app-health/")

    no_probe = [p for p in pods if not p.get("path")]
    not_rw = [p for p in pods if p.get("path") and not rewritten(p)]
    not_ready = [p for p in pods if not p.get("ready")]
    rw = sum(1 for p in pods if rewritten(p))
    observed = f"Ready {n - len(not_ready)}/{n}, rewrite {rw}/{n}"
    names = lambda lst: ", ".join(p["name"] for p in lst)  # noqa: E731
    if no_probe:
        reason = f"readinessProbe 없음: {names(no_probe)}"
    elif not_rw:
        reason = "probe rewrite 안 됨: " + ", ".join(f"{p['name']}={p.get('port')}{p.get('path')}" for p in not_rw)
    elif not_ready:
        reason = f"Not Ready: {names(not_ready)}"
    else:
        return {"verdict": "PASS", "observed": observed, "reason": f"모든 Pod가 15020 {pods[0]['path']} 로 rewrite 후 Ready"}
    return {"verdict": "FAIL", "observed": observed, "reason": reason}


def _in_net(ip: str, net: ipaddress.IPv4Network) -> bool:
    try:
        return ipaddress.IPv4Address(ip) in net
    except ValueError:
        return False


def _is_public(ip: str) -> bool:
    try:
        return ipaddress.IPv4Address(ip).is_global
    except ValueError:
        return False


def api_private_verdict(private_enabled: bool, public_fqdn_enabled: bool, vm_ips: list[str], readyz_ok: bool, local_ip: str, local_connected: bool) -> dict:
    # TN14: FAIL(구성 위반) > INCONCLUSIVE(관측 불충분) > PASS 순으로 판정한다
    observed = (
        f"private={private_enabled}, publicFqdn={public_fqdn_enabled}, vm={','.join(vm_ips) or '없음'}, "
        f"readyz={'ok' if readyz_ok else 'fail'}, local={local_ip or 'NXDOMAIN'}/{'연결' if local_connected else '실패'}"
    )
    fails = []
    if not private_enabled:
        fails.append("Private Cluster가 아닙니다")
    if public_fqdn_enabled:
        fails.append("public FQDN이 켜져 있습니다")
    if not vm_ips or not all(_in_net(ip, AKS_NET) for ip in vm_ips):
        fails.append(f"vm-mgmt 해석 IP가 {AKS_NET} 밖입니다")
    if _is_public(local_ip):
        fails.append(f"로컬에서 공인 IP({local_ip})로 해석됩니다")
    if fails:
        return {"verdict": "FAIL", "observed": observed, "reason": "; ".join(fails)}
    if not readyz_ok:
        return {"verdict": "INCONCLUSIVE", "observed": observed, "reason": "vm-mgmt 에서 /readyz 확인 실패"}
    if local_connected:
        return {"verdict": "INCONCLUSIVE", "observed": observed, "reason": "로컬에서 API 서버 443 연결이 성공했습니다(VPN 등 사설 경로 확인 필요)"}
    return {"verdict": "PASS", "observed": observed, "reason": "API 서버는 사설 IP로만 해석·접근되고 로컬에서는 접근 불가"}


def require(value: str | None, name: str) -> None:
    if value is None or not str(value).strip():
        raise ValueError(f"{name} 값이 비어 있습니다")


def main(argv: list[str]) -> int:
    return _cli.run(globals(), argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
