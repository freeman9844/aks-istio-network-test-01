import json
from datetime import datetime, timedelta

import pytest

from aksnetval.verdict import (
    api_private_verdict,
    deletion_rule_verdict,
    hpa_verdict,
    http_verdict,
    is_pe_ip,
    main,
    nsg_reuse_verdict,
    pod_spread_verdict,
    probe_rewrite_verdict,
    require,
    rs_possible_first,
    rs_victims,
    started_after,
    tls_verdict,
    zonal_floor_verdict,
    node_reach_verdict,
    zone_balance_verdict,
)


# TN21·TN22: app Pod → 노드(kubelet) egress. 대조군은 정책 없는 nomesh/probe
@pytest.mark.parametrize("code,ran,ccode,cran,want", [
    ("401", True, "401", True, "PASS"),
    ("000", True, "401", True, "FAIL"),
    ("000", False, "401", True, "INCONCLUSIVE"),  # curl 미실행이면 000 은 차단 증거가 아님
    ("000", True, "000", True, "INCONCLUSIVE"),  # 대조군도 못 닿으면 정책 탓인지 알 수 없음
    ("401", True, "401", False, "INCONCLUSIVE"),
])
def test_node_reach_verdict(code, ran, ccode, cran, want):
    assert node_reach_verdict(code, ran, ccode, cran) == want


# TN1~TN12 HTTP
def test_http_200_pass():
    assert http_verdict("200", "200") == "PASS"


def test_http_403_got_200_fail():
    assert http_verdict("200", "403") == "FAIL"


def test_http_blocked_000_pass():
    assert http_verdict("000", "blocked") == "PASS"


def test_http_control_failed_inconclusive():
    assert http_verdict("000", "blocked", control_passed=False) == "INCONCLUSIVE"


def test_http_bad_expect_raises():
    with pytest.raises(ValueError):
        http_verdict("200", "ok")


# TLS
def test_tls_pe_000_fail_expected_pass():
    assert tls_verdict("10.50.2.4", "000", "fail") == "PASS"


def test_tls_pe_400_ok_expected_pass():
    assert tls_verdict("10.50.2.4", "400", "ok") == "PASS"


def test_tls_public_ip_inconclusive():
    assert tls_verdict("20.1.2.3", "000", "fail") == "INCONCLUSIVE"


def test_is_pe_ip_other_subnet():
    assert is_pe_ip("10.50.20.4") is False


# TN12·TN15~TN17: NetworkPolicy 가 /32 로 허용하므로 해석 IP 가 고정 PE IP 와 달라도 경로 검증이 아님
def test_tls_expected_ip_mismatch_inconclusive():
    assert tls_verdict("10.50.2.6", "400", "ok", expected_ip="10.50.2.5") == "INCONCLUSIVE"


def test_tls_expected_ip_match_pass():
    assert tls_verdict("10.50.2.5", "000", "fail", expected_ip="10.50.2.5") == "PASS"


# TN19: 정책 적용 뒤에 기동한 Pod 여야 istiod 연결이 정책 아래에서 이루어졌다고 볼 수 있다
@pytest.mark.parametrize("pod,policy,expected", [
    ("2026-01-01T00:10:00Z", "2026-01-01T00:00:00Z", True),
    ("2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z", False),
    ("", "2026-01-01T00:00:00Z", False),
    ("2026-01-01T00:10:00Z", "bad", False),
])
def test_started_after(pod, policy, expected):
    assert started_after(pod, policy) is expected


# TS1 HPA
def _samples(pairs):
    t0 = datetime(2026, 1, 1)
    return [{"time": (t0 + timedelta(seconds=s)).isoformat(), "replicas": r} for s, r in pairs]


def test_hpa_pass():
    r = hpa_verdict(_samples([(0, 5), (10, 5), (60, 4), (90, 4), (120, 3), (180, 2)]))
    assert r["verdict"] == "PASS"
    assert r["sequence"] == [5, 4, 3, 2]


def test_hpa_short_interval_fail():
    assert hpa_verdict(_samples([(0, 5), (60, 4), (90, 3), (150, 2)]))["verdict"] == "FAIL"


def test_hpa_skip_fail():
    assert hpa_verdict(_samples([(0, 5), (60, 3), (120, 2)]))["verdict"] == "FAIL"


def test_hpa_not_reached_2_fail():
    r = hpa_verdict(_samples([(0, 5), (60, 4)]))
    assert r["verdict"] == "FAIL"
    assert "2" in r["reason"]


# TS4 영역 균형
def test_zone_balance_222_pass():
    assert zone_balance_verdict({"1": 2, "2": 2, "3": 2}, 0)["verdict"] == "PASS"


def test_zone_balance_321_fail():
    assert zone_balance_verdict({"1": 3, "2": 2, "3": 1}, 0)["verdict"] == "FAIL"


def test_zone_balance_missing_zone_fail():
    assert zone_balance_verdict({"1": 3, "2": 3}, 0)["verdict"] == "FAIL"


def test_zone_balance_pending_fail():
    r = zone_balance_verdict({"1": 2, "2": 2, "3": 2}, 1)
    assert r["verdict"] == "FAIL"
    assert "Pending" in r["reason"]


def test_zone_balance_no_scaleout_inconclusive():
    r = zone_balance_verdict({"1": 1, "2": 1, "3": 1}, 0, nodes_before=3)
    assert r["verdict"] == "INCONCLUSIVE"
    assert "CAS 확장 없음" in r["reason"]


def test_zone_balance_no_scaleout_pending_fail():
    r = zone_balance_verdict({"1": 1, "2": 1, "3": 1}, 2, nodes_before=3)
    assert r["verdict"] == "FAIL"
    assert "Pending" in r["reason"]


def test_zone_balance_scaled_out_pass():
    assert zone_balance_verdict({"1": 2, "2": 2, "3": 2}, 0, nodes_before=3)["verdict"] == "PASS"


def test_require_empty_raises():
    with pytest.raises(ValueError, match="GatewaySA"):
        require("", "GatewaySA")


# TN4 NSG 재사용
NSG_ID = "/subscriptions/x/resourceGroups/rg/providers/Microsoft.Network/networkSecurityGroups/nsg-ingress"


def _nsg(tn1="PASS", access="Deny", nsg_id=NSG_ID):
    return nsg_reuse_verdict(tn1, access, nsg_id, "nsg-ingress", "deny-appgw-all")


def test_nsg_reuse_pass():
    r = _nsg()
    assert r["verdict"] == "PASS"
    assert "deny-appgw-all" in r["note"]


def test_nsg_reuse_tn1_fail():
    assert _nsg(tn1="FAIL")["verdict"] == "FAIL"


def test_nsg_no_rule_inconclusive():
    r = _nsg(access="")
    assert r["verdict"] == "INCONCLUSIVE"
    assert "deny-appgw-all" in r["note"]


def test_nsg_allow_inconclusive():
    assert _nsg(access="Allow")["verdict"] == "INCONCLUSIVE"


def test_nsg_other_attached_inconclusive():
    r = _nsg(nsg_id=NSG_ID + "-old")
    assert r["verdict"] == "INCONCLUSIVE"
    assert "서브넷" in r["note"]


def test_nsg_not_attached_inconclusive():
    assert _nsg(nsg_id="")["verdict"] == "INCONCLUSIVE"


# TS5 Pod 분산
def test_spread_222_pass():
    assert pod_spread_verdict({"1": 2, "2": 2, "3": 2}, 0, 6)["verdict"] == "PASS"


def test_spread_321_fail():
    assert pod_spread_verdict({"1": 3, "2": 2, "3": 1}, 0, 6)["verdict"] == "FAIL"


def test_spread_pending_fail():
    r = pod_spread_verdict({"1": 1, "2": 1, "3": 1}, 3, 6)
    assert r["verdict"] == "FAIL"
    assert "Pending" in r["reason"]


def test_spread_short_inconclusive():
    assert pod_spread_verdict({"1": 2, "2": 2}, 0, 6)["verdict"] == "INCONCLUSIVE"


# TN13 probe rewrite
def _p(name, ready, port, path):
    return {"name": name, "ready": ready, "port": port, "path": path}


def test_probe_pass():
    r = probe_rewrite_verdict([_p("web/a", True, 15020, "/app-health/app/readyz"), _p("app/b", True, 15020, "/app-health/app/readyz")])
    assert r["verdict"] == "PASS"
    assert r["observed"] == "Ready 2/2, rewrite 2/2"


def test_probe_not_rewritten_fail():
    assert probe_rewrite_verdict([_p("web/a", True, 8080, "/")])["verdict"] == "FAIL"


def test_probe_missing_fail():
    assert probe_rewrite_verdict([_p("web/a", True, None, None)])["verdict"] == "FAIL"


def test_probe_not_ready_fail():
    assert probe_rewrite_verdict([_p("web/a", False, 15020, "/app-health/app/readyz")])["verdict"] == "FAIL"


def test_probe_no_pods_inconclusive():
    assert probe_rewrite_verdict([])["verdict"] == "INCONCLUSIVE"


# TS3 ReplicaSet 삭제 규칙 예측
T0 = datetime(2026, 11, 20, 1, 0, 0)


def _iso(sec_before, base=T0):
    return (base - timedelta(seconds=sec_before)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rp(name, uid, node, zone, ready_age, created_age=None, ready=True, restarts=0, cost=0, base=T0):
    return {"name": name, "uid": uid, "node": node, "zone": zone, "phase": "Running", "ready": ready,
            "ready_time": _iso(ready_age, base) if ready else None,
            "created": _iso(created_age if created_age is not None else ready_age + 5, base),
            "restarts": restarts, "cost": cost}


NOW_ISO = _iso(0)


def test_rs_victim_prefers_pod_on_doubled_node():
    pods = [_rp("a", "u1", "n1", "1", 10), _rp("b", "u2", "n2", "2", 1000), _rp("c", "u3", "n2", "2", 2000)]
    r = rs_victims(pods, NOW_ISO)
    assert r["victims"] == ["b"]
    assert r["rule"] == "6"   # b·c 는 같은 노드(5번 동률), Ready 로그 구간이 다름


def test_rs_victim_same_log_bucket_uses_uid_not_newest():
    # 100초·120초는 log2(ns) 구간이 같아 UID 가 앞선 y(더 오래됨)가 삭제 대상
    pods = [_rp("x", "b", "n1", "1", 100), _rp("y", "a", "n2", "2", 120)]
    r = rs_victims(pods, NOW_ISO)
    assert r["victims"] == ["y"]
    assert r["rule"] == "6-UID"


def test_rs_victim_newer_bucket_wins_over_uid():
    pods = [_rp("x", "z", "n1", "1", 10), _rp("y", "a", "n2", "2", 1000)]
    assert rs_victims(pods, NOW_ISO)["victims"] == ["x"]


def test_rs_victim_rank_beats_ready_time():
    pods = [_rp("lone", "a", "n1", "1", 5), _rp("d1", "b", "n2", "2", 5000), _rp("d2", "c", "n2", "2", 6000)]
    r = rs_victims(pods, NOW_ISO)
    assert r["victims"] == ["d1"] or r["victims"] == ["d2"]
    assert "lone" not in r["victims"]


def test_rs_victim_not_ready_first():
    pods = [_rp("r", "a", "n1", "1", 10), _rp("nr", "b", "n2", "2", 0, created_age=5000, ready=False)]
    r = rs_victims(pods, NOW_ISO)
    assert r["victims"] == ["nr"] and r["rule"] == "3"


def test_rs_victim_lower_deletion_cost_first():
    pods = [_rp("a", "a", "n1", "1", 10, cost=10), _rp("b", "b", "n2", "2", 5000, cost=-1)]
    assert rs_victims(pods, NOW_ISO) == {"victims": ["b"], "rule": "4"}


def test_rs_victim_equal_ready_time_uses_restarts():
    pods = [_rp("a", "a", "n1", "1", 300, restarts=0), _rp("b", "b", "n2", "2", 300, restarts=2)]
    assert rs_victims(pods, NOW_ISO) == {"victims": ["b"], "rule": "7"}


def test_rs_victim_equal_ready_uses_creation_log_bucket():
    pods = [_rp("a", "a", "n1", "1", 300, created_age=5000), _rp("b", "b", "n2", "2", 300, created_age=400)]
    assert rs_victims(pods, NOW_ISO) == {"victims": ["b"], "rule": "8"}


def _round_5_to_3(first="p4", second="p5", final=("p1", "p2", "p3")):
    # 노드 1/1/1, 1/2/2 에서 시작. p4·p5 는 확장 때 추가(400초 전 Ready), 나머지는 1시간 전
    pods = [_rp("p1", "u1", "n1", "1", 3600), _rp("p2", "u2", "n2", "2", 3600), _rp("p3", "u3", "n3", "3", 3600),
            _rp("p4", "u4", "n2", "2", 400), _rp("p5", "u5", "n3", "3", 400)]
    deletes = [{"name": first, "time": _iso(0)}, {"name": second, "time": _iso(-61)}]
    return {"start": 5, "pods": pods, "deletes": deletes, "final": list(final)}


def test_deletion_rule_pass_and_distribution():
    r = deletion_rule_verdict([_round_5_to_3()] * 2)
    assert r["verdict"] == "PASS"
    assert r["observed"] == "5개 1/2/2→1/1/1, 5개 1/2/2→1/1/1"
    assert "4건" in r["reason"] and "일치" in r["reason"]


def test_deletion_rule_mismatch_fail():
    r = deletion_rule_verdict([_round_5_to_3(first="p2", second="p5", final=("p1", "p4", "p3"))])
    assert r["verdict"] == "FAIL"
    assert "p2" in r["reason"] and "p4" in r["reason"]


def test_deletion_rule_log_boundary_counts_as_match():
    # 2^37 ns ≈ 137.44초. x 는 이벤트 시각에 136.5~138초 → 판정 창 안에서 구간 36/37 이 갈림
    pods = [_rp("x", "b", "n1", "1", 137), _rp("y", "a", "n1", "1", 200), _rp("z", "c", "n2", "2", 5000),
            _rp("w", "d", "n3", "3", 5000)]
    rnd = {"start": 4, "pods": pods, "deletes": [{"name": "y", "time": _iso(0)}], "final": ["x", "z", "w"]}
    r = deletion_rule_verdict([rnd])
    assert r["verdict"] == "PASS"
    assert "경계" in r["reason"]


@pytest.mark.parametrize("change", [
    {"deletes": [{"name": "p4", "time": _iso(0)}]},                        # 삭제 이벤트 부족
    {"deletes": [{"name": "zz", "time": _iso(0)}, {"name": "p5", "time": _iso(-61)}]},  # 스냅숏에 없는 Pod
    {"final": ["p1", "p2", "p9"]},                                          # 최종 Pod 가 예상과 다름
    {"deletes": [{"name": "p5", "time": _iso(-61)}],                        # 앞 삭제 누락(남은 p4 가 예측을 흔듦)
     "pods": [_rp("p1", "u1", "n1", "1", 3600), _rp("p2", "u2", "n2", "2", 3600), _rp("p3", "u3", "n3", "3", 3600),
              _rp("p4", "u4", "n2", "2", 400), _rp("p5", "u5", "n3", "3", 401)]},
])
def test_deletion_rule_incomplete_inconclusive(change):
    assert deletion_rule_verdict([{**_round_5_to_3(), **change}])["verdict"] == "INCONCLUSIVE"


def test_deletion_rule_no_rounds_inconclusive():
    assert deletion_rule_verdict([])["verdict"] == "INCONCLUSIVE"


def _round_batch(gap):
    # 노드 1/1/1, Pod 1/2/2. 영역 2 의 p2·p4 가 가장 최근 Ready
    pods = [_rp("p1", "u1", "n1", "1", 3600), _rp("p2", "u2", "n2", "2", 10), _rp("p4", "u4", "n2", "2", 12),
            _rp("p3", "u3", "n3", "3", 3600), _rp("p5", "u5", "n3", "3", 3600)]
    deletes = [{"name": "p2", "time": _iso(0)}, {"name": "p4", "time": _iso(-gap)}]
    return {"start": 5, "pods": pods, "deletes": deletes, "final": ["p1", "p3", "p5"]}


def test_deletion_rule_batch_sorts_once_and_can_empty_zone():
    # 같은 동기화에서 2개를 지우면 한 번 정렬한 상위 2개(노드 몰림 재계산 없음) → 영역 2 가 비는 1/0/2
    r = deletion_rule_verdict([_round_batch(gap=1)])
    assert r["verdict"] == "PASS"
    assert r["observed"] == "5개 1/2/2→1/0/2"
    assert "한 번에 2개" in r["reason"]


def test_deletion_rule_separate_steps_recount_node_rank():
    # 60초 간격의 1개씩 축소라면 첫 삭제 뒤 n2 몰림이 풀려 두 번째는 영역 3 에서 나와야 함
    assert deletion_rule_verdict([_round_batch(gap=61)])["verdict"] == "FAIL"


def test_deletion_rule_nontransitive_tie_accepts_tied_pod():
    # a·c 는 Ready·생성 시각이 같아 완전 동률, b 는 Ready 가 1초 늦어 같은 로그 구간 → UID 비교(a < b < c).
    # a = c 인데 a < b < c 라 비교가 비이행적이고, Go 정렬 결과는 입력 순서에 따라 c 가 맨 앞이 될 수 있다
    pods = [_rp("a", "u2", "n1", "1", 500, created_age=501), _rp("b", "u5", "n2", "2", 499, created_age=501),
            _rp("c", "ue", "n3", "3", 500, created_age=501), _rp("old", "u0", "n4", "1", 5000)]
    rnd = {"start": 4, "pods": pods, "deletes": [{"name": "c", "time": _iso(0)}], "final": ["a", "b", "old"]}
    r = deletion_rule_verdict([rnd])
    assert r["verdict"] == "PASS"
    assert "비이행" in r["reason"]


def test_deletion_rule_batch_nontransitive_tie_accepted():
    # 한 동기화에서 2개 삭제. a = c, a < b < c 라 입력 순서가 [c, a, b, …] 이면 Go 삽입 정렬은 {c, a} 를 고른다
    pods = [_rp("a", "u2", "n1", "1", 500, created_age=501), _rp("b", "u5", "n2", "2", 499, created_age=501),
            _rp("c", "ue", "n3", "3", 500, created_age=501), _rp("o1", "u0", "n4", "1", 5000),
            _rp("o2", "u1", "n5", "2", 5000)]
    deletes = [{"name": "c", "time": _iso(0)}, {"name": "a", "time": _iso(-1)}]
    rnd = {"start": 5, "pods": pods, "deletes": deletes, "final": ["b", "o1", "o2"]}
    r = deletion_rule_verdict([rnd])
    assert r["verdict"] == "PASS"
    assert "비이행" in r["reason"]


def test_deletion_rule_close_deletes_may_be_separate_syncs():
    # 1초 간격이어도 서로 다른 동기화일 수 있다: p2 삭제 뒤 노드 몰림을 다시 세면 n3 의 p3 가 다음
    rnd = _round_batch(gap=1)
    rnd["deletes"][1]["name"] = "p3"
    rnd["final"] = ["p1", "p4", "p5"]
    assert deletion_rule_verdict([rnd])["verdict"] == "PASS"


def test_rs_possible_first_includes_tie_blocked_pod():
    # 실측 12개 1회차 축약: b 가 c 보다 앞서지만, c 와 완전 동률인 a 가 b 를 막아 c 가 맨 앞이 될 수 있음
    pods = [_rp("b", "u5", "n2", "2", 499, created_age=501), _rp("c", "ue", "n3", "3", 500, created_age=501),
            _rp("a", "u2", "n1", "1", 500, created_age=501), _rp("old", "u0", "n4", "1", 5000)]
    assert rs_possible_first(pods, NOW_ISO) == {"a", "b", "c"}


def test_rs_possible_first_chain_blocked_pod():
    # 실측 12개 2회차 축약: k 는 모두에 앞서지만 r 과 완전 동률, r 은 f 를 넘지 못하고 f 는 b 와 완전 동률.
    # 입력 순서 [b, f, r, k] 를 삽입 정렬하면 b 가 맨 앞에 남는다(막는 Pod 가 한 단계가 아니라 사슬)
    pods = [_rp("b", "ud", "n1", "1", 344, created_age=346), _rp("f", "u1", "n2", "2", 344, created_age=346),
            _rp("r", "u5", "n3", "3", 343, created_age=345), _rp("k", "u0", "n4", "1", 343, created_age=345)]
    assert rs_possible_first(pods, NOW_ISO) == {"b", "f", "r", "k"}


def test_cli_string_result(capsys):
    assert main(["http_verdict", '{"code":"000","expect":"blocked"}']) == 0
    assert json.loads(capsys.readouterr().out) == {"verdict": "PASS"}


_BASE = dict(private_enabled=True, public_fqdn_enabled=False, vm_ips=["10.50.4.5"], readyz_ok=True, local_ip="", local_connected=False)


@pytest.mark.parametrize("over,expected", [
    ({}, "PASS"),
    (dict(private_enabled=False), "FAIL"),
    (dict(public_fqdn_enabled=True), "FAIL"),
    (dict(vm_ips=["10.50.1.5"]), "FAIL"),
    (dict(vm_ips=[]), "FAIL"),
    (dict(local_ip="20.1.2.3"), "FAIL"),
    (dict(readyz_ok=False), "INCONCLUSIVE"),
    (dict(local_ip="10.50.4.5", local_connected=True), "INCONCLUSIVE"),
    (dict(private_enabled=False, readyz_ok=False, local_connected=True), "FAIL"),
])
def test_api_private_verdict(over, expected):
    r = api_private_verdict(**{**_BASE, **over})
    assert r["verdict"] == expected
    assert r["reason"]


def test_api_private_verdict_observed_format():
    r = api_private_verdict(**_BASE)
    assert r["observed"] == "private=True, publicFqdn=False, vm=10.50.4.5, readyz=ok, local=NXDOMAIN/실패"

# TS6: 영역별 Deployment 축소 중 영역당 Running ≥1
def _zc(a, b, c, mismatch=0):
    return {"counts": {"1": a, "2": b, "3": c}, "mismatch": mismatch, "pending": 0}


def _zround(start, samples, final=None):
    return {"start": start, "before": _zc(start, start, start)["counts"], "samples": samples, "final": final or _zc(1, 1, 1)}


def test_zonal_floor_pass():
    rounds = [_zround(n, [_zc(n, n, n), _zc(2, 1, 2), _zc(1, 1, 1)]) for n in (3, 3, 4, 4)]
    r = zonal_floor_verdict(rounds)
    assert r["verdict"] == "PASS"
    assert r["observed"] == "3/3/3→1/1/1, 3/3/3→1/1/1, 4/4/4→1/1/1, 4/4/4→1/1/1"
    assert "표본 12개" in r["reason"] and "영역 불일치 0" in r["reason"]


def test_zonal_floor_fail_on_zero_sample():
    rounds = [_zround(3, [_zc(3, 3, 3), _zc(0, 2, 2), _zc(1, 1, 1)])]
    r = zonal_floor_verdict(rounds)
    assert r["verdict"] == "FAIL"
    assert "영역1" in r["reason"]


def test_zonal_floor_fail_on_mismatch():
    rounds = [_zround(3, [_zc(3, 3, 3, mismatch=1), _zc(1, 1, 1)])]
    assert zonal_floor_verdict(rounds)["verdict"] == "FAIL"


def test_zonal_floor_inconclusive_not_reached():
    rounds = [_zround(3, [_zc(3, 3, 3), _zc(2, 1, 1)], final=_zc(2, 1, 1))]
    assert zonal_floor_verdict(rounds)["verdict"] == "INCONCLUSIVE"


def test_zonal_floor_inconclusive_empty():
    assert zonal_floor_verdict([])["verdict"] == "INCONCLUSIVE"