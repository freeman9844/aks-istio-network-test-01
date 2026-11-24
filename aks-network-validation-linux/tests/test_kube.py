"""kubectl·az JSON 집계 함수(03-verify 보조). 원본 03-verify.ps1 의 Get-PodZoneCounts 등 대응."""
import json

import pytest

from aksnetval import _cli
from aksnetval.kube import (
    aks_zone_setting,
    fmt_counts,
    fmt_short,
    node_targets,
    node_zone_counts,
    nodes_at_min,
    pod_zone_counts,
    probe_items,
    rs_delete_times,
    rs_pod_snapshot,
    scale_event_note,
)


def _node(name, zone):
    return {"metadata": {"name": name, "labels": {"topology.kubernetes.io/zone": zone}}}


NODES = {"items": [_node("n1", "koreacentral-1"), _node("n2", "koreacentral-2"), _node("n3", "koreacentral-3"), _node("n4", "koreacentral-1")]}


def _pod(name, node=None, phase="Running", deleting=False, ready=True, probe=("15020", "/app-health/app/readyz")):
    p = {
        "metadata": {"name": name},
        "spec": {"containers": [{"name": "istio-proxy"}, {"name": "app"}]},
        "status": {"phase": phase, "conditions": [{"type": "Ready", "status": "True" if ready else "False"}]},
    }
    if node:
        p["spec"]["nodeName"] = node
    if deleting:
        p["metadata"]["deletionTimestamp"] = "2026-11-20T00:00:00Z"
    if probe:
        p["spec"]["containers"][1]["readinessProbe"] = {"httpGet": {"port": int(probe[0]) if probe[0].isdigit() else probe[0], "path": probe[1]}}
    return p


def test_node_zone_counts():
    assert node_zone_counts(NODES) == {"1": 2, "2": 1, "3": 1}


def _ipnode(name, ip):
    return {"metadata": {"name": name}, "status": {"addresses": [{"type": "Hostname", "address": name}, {"type": "InternalIP", "address": ip}]}}


def test_node_targets_own_and_other_node():
    nodes = {"items": [_ipnode("sys", "10.50.4.4"), _ipnode("u1", "10.50.4.5"), _ipnode("u2", "10.50.4.6")]}
    pods = {"items": [_pod("old", "sys", deleting=True), _pod("bff-b-0", "u2")]}
    assert node_targets(nodes, pods) == {"own_node": "u2", "own_ip": "10.50.4.6", "other_node": "sys", "other_ip": "10.50.4.4"}


def test_node_targets_needs_two_nodes():
    with pytest.raises(ValueError):
        node_targets({"items": [_ipnode("u1", "10.50.4.5")]}, {"items": [_pod("bff-b-0", "u1")]})


def test_pod_zone_counts_excludes_deleting_and_counts_pending():
    pods = {"items": [_pod("a", "n1"), _pod("b", "n4"), _pod("c", "n2"), _pod("d", phase="Pending"), _pod("e", "n3", deleting=True), _pod("f", "n3", phase="Succeeded")]}
    r = pod_zone_counts(NODES, pods)
    assert r == {"counts": {"1": 2, "2": 1}, "running": 3, "pending": 1, "names": ["a", "b", "c", "d", "f"]}


def test_probe_items():
    pods = {"items": [_pod("w1"), _pod("w2", ready=False, probe=("8080", "/healthz")), _pod("w3", probe=None), _pod("w4", deleting=True)]}
    assert probe_items(pods, "web") == [
        {"name": "web/w1", "ready": True, "port": "15020", "path": "/app-health/app/readyz"},
        {"name": "web/w2", "ready": False, "port": "8080", "path": "/healthz"},
        {"name": "web/w3", "ready": True, "port": None, "path": None},
    ]


def _ev(pod, reason, msg):
    return {"involvedObject": {"kind": "Pod", "name": pod}, "reason": reason, "message": msg}


def test_scale_event_note_scaleup_only():
    ev = {"items": [_ev("p1", "TriggeredScaleUp", "pod triggered scale-up: [userz1 1->2]"), _ev("p2", "TriggeredScaleUp", "pod triggered scale-up: [userz1 1->2]"), _ev("x", "TriggeredScaleUp", "other")]}
    assert scale_event_note(ev, ["p1", "p2"], 0) == "TriggeredScaleUp: pod triggered scale-up: [userz1 1->2]"


def test_scale_event_note_pending_causes():
    ev = {"items": [_ev("p1", "FailedScheduling", "0/4 nodes"), _ev("p1", "NotTriggerScaleUp", "max size")]}
    assert scale_event_note(ev, ["p1"], 1) == "Pending 사유: FailedScheduling: 0/4 nodes / NotTriggerScaleUp: max size; TriggeredScaleUp 이벤트 없음"


def test_scale_event_note_pending_without_events():
    assert scale_event_note({"items": []}, ["p1"], 2) == "Pending 사유: 이벤트 없음; TriggeredScaleUp 이벤트 없음"


def test_fmt():
    assert fmt_counts({"1": 2, "3": 1}) == "영역1=2, 영역2=0, 영역3=1"
    assert fmt_short({"1": 2, "3": 1}) == "2/0/1"


def test_nodes_at_min():
    assert nodes_at_min({"1": 1, "2": 1, "3": 1}, 1)
    assert not nodes_at_min({"1": 2, "2": 1, "3": 1}, 1)
    assert not nodes_at_min({"1": 1, "2": 1}, 1)


def _aks(balance="true", zones=(["1"], ["2"], ["3"])):
    return {
        "autoScalerProfile": {"balanceSimilarNodeGroups": balance},
        "agentPoolProfiles": [{"name": "system", "availabilityZones": ["1", "2", "3"]}] + [{"name": f"userz{i + 1}", "availabilityZones": z} for i, z in enumerate(zones)],
    }


def test_aks_zone_setting_ok():
    r = aks_zone_setting(_aks(), ["userz1", "userz2", "userz3"])
    assert r == {"ok": True, "setting": "balanceSimilarNodeGroups=true; userz1=1, userz2=2, userz3=3"}


def test_aks_zone_setting_mismatch():
    assert not aks_zone_setting(_aks(balance="false"), ["userz1", "userz2", "userz3"])["ok"]
    r = aks_zone_setting(_aks(zones=(["1"], None, ["3"])), ["userz1", "userz2", "userz3"])
    assert not r["ok"] and "userz2=없음" in r["setting"]


def test_cli_reads_json_file_arguments(tmp_path, capsys):
    f = tmp_path / "nodes.json"
    f.write_text(json.dumps(NODES), encoding="utf-8")
    from aksnetval import kube

    assert _cli.run(vars(kube), ["node_zone_counts", json.dumps({"nodes": f"@{f}"})]) == 0
    assert json.loads(capsys.readouterr().out) == {"1": 2, "2": 1, "3": 1}


# TS3: ReplicaSet 삭제 규칙 예측용 Pod 스냅숏과 watch 행에서 뽑은 삭제 요청 시각
def _rspod(name, uid, node, created, ready_at=None, restarts=(0,), deleting=False, cost=None):
    p = {
        "metadata": {"name": name, "uid": uid, "creationTimestamp": created},
        "spec": {"nodeName": node},
        "status": {"phase": "Running", "conditions": [], "containerStatuses": [{"restartCount": r} for r in restarts]},
    }
    if ready_at:
        p["status"]["conditions"].append({"type": "Ready", "status": "True", "lastTransitionTime": ready_at})
    if deleting:
        p["metadata"]["deletionTimestamp"] = "2026-11-20T00:00:00Z"
    if cost is not None:
        p["metadata"]["annotations"] = {"controller.kubernetes.io/pod-deletion-cost": str(cost)}
    return p


def test_rs_pod_snapshot_fields():
    pods = {"items": [
        _rspod("a", "u-a", "n2", "2026-11-20T01:00:00Z", "2026-11-20T01:00:05Z", restarts=(1, 3), cost=-5),
        _rspod("b", "u-b", "n1", "2026-11-20T01:01:00Z"),
        _rspod("c", "u-c", "n3", "2026-11-20T01:02:00Z", "2026-11-20T01:02:04Z", deleting=True),
    ]}
    assert rs_pod_snapshot(NODES, pods) == [
        {"name": "a", "uid": "u-a", "node": "n2", "zone": "2", "phase": "Running", "ready": True,
         "ready_time": "2026-11-20T01:00:05Z", "created": "2026-11-20T01:00:00Z", "restarts": 3, "cost": -5},
        {"name": "b", "uid": "u-b", "node": "n1", "zone": "1", "phase": "Running", "ready": False,
         "ready_time": None, "created": "2026-11-20T01:01:00Z", "restarts": 0, "cost": 0},
    ]


@pytest.mark.parametrize("bad", ["abc", "1.5", "", "2147483648"])
def test_rs_pod_snapshot_bad_cost_is_zero(bad):
    # 업스트림은 int32 로 파싱하지 못한 pod-deletion-cost 를 0 으로 본다
    pods = {"items": [_rspod("a", "u-a", "n2", "2026-11-20T01:00:00Z", cost=bad)]}
    assert rs_pod_snapshot(NODES, pods)[0]["cost"] == 0


def test_rs_delete_times_from_watch_rows():
    # watch 행: [이름, deletionTimestamp, deletionGracePeriodSeconds]. 삭제 요청 시각 = 앞 값 - 유예
    rows = [
        ["p2", "", ""],
        ["p2", "2026-11-20T01:02:30Z", "30"],
        ["p1", "2026-11-20T01:01:30Z", "30"],
        ["p2", "2026-11-20T01:02:30Z", "30"],
        ["old", "2026-11-20T00:01:30Z", "30"],
        ["p3", "2026-11-20T01:03:00Z", "0"],
        ["bad"],
    ]
    assert rs_delete_times(rows, ["p1", "p2", "p3"]) == [
        {"name": "p1", "time": "2026-11-20T01:01:00Z"},
        {"name": "p2", "time": "2026-11-20T01:02:00Z"},
        {"name": "p3", "time": "2026-11-20T01:03:00Z"},
    ]
