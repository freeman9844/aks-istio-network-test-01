"""vm/03-verify.sh --only network 를 가짜 az·kubectl 로 실행해 이식한 판정 흐름을 확인한다(Azure 없이)."""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JQ = shutil.which("jq") or str(Path.home() / ".local/bin/jq")
pytestmark = pytest.mark.skipif(not Path(JQ).exists(), reason="jq 없음")

FAKE_AZ = r'''#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
j = " ".join(a)
if a[:2] == ["account", "show"]:
    print("00000000-0000-0000-0000-000000000000")
elif a[:1] == ["login"]:
    pass
elif a[:3] == ["vm", "run-command", "invoke"]:
    vm = a[a.index("-n") + 1]
    script = a[a.index("--scripts") + 1]
    if vm == "vm-appgw-sim":
        out = "200\nrc=0"
    elif "blob.core.windows.net" in script:
        out = "400\nrc=0"
    else:
        out = "000\nrc=28"
    print(f"Enable succeeded: \n[stdout]\n{out}\n\n[stderr]\n")
elif a[:4] == ["network", "nsg", "rule", "show"]:
    if "access" in a:
        print("Deny")
    else:
        print('{"name": "deny-rule"}')
elif a[:4] == ["network", "vnet", "subnet", "show"]:
    print("/subscriptions/x/resourceGroups/rg/providers/Microsoft.Network/networkSecurityGroups/nsg-ingress")
elif a[:2] == ["aks", "show"]:
    print(json.dumps({"private": True, "publicFqdn": False, "fqdn": "aks-x.privatelink.koreacentral.azmk8s.io"}))
else:
    sys.stderr.write("fake az: 알 수 없는 명령 " + j + "\n")
    sys.exit(3)
'''

FAKE_KUBECTL = r'''#!/usr/bin/env python3
import json, os, re, sys
a = sys.argv[1:]
if a[0] == "--kubeconfig":
    a = a[2:]
CODES = {
    ("web", "web-a", "bff-a"): "200", ("web", "web-b", "bff-b"): "200",
    ("web", "web-a", "bff-b"): "403", ("web", "web-b", "bff-a"): "403",
    ("web", "web-a", "web-b"): "403", ("nomesh", "probe", "bff-a"): "000",
    ("web", "web-a", "blob"): "000",
    ("app", "bff-b", "blob"): "400", ("app", "bff-a", "blob"): os.environ.get("FAKE_BFFA_BLOB", "000"),
    ("app", "bff-a", "blob2"): "400", ("app", "bff-b", "blob2"): "000",
    ("app", "bff-b", "localhost"): "200", ("app", "bff-b", "mcr"): "401",
    ("app", "bff-b", "node"): os.environ.get("FAKE_BFFB_NODE", "401"),
    ("nomesh", "probe", "node"): os.environ.get("FAKE_PROBE_NODE", "401"),
}
DNS = {"blob2": "10.50.2.5", "blob": "10.50.2.4", "kubernetes": "10.0.0.1"}


def target(cmd):
    m = re.search(r"https://(\d+\.\d+\.\d+\.\d+):10250", cmd)
    if m:
        return "node"
    m = re.search(r"(staksnetval\w+)\.blob", cmd)
    if m:
        # STORAGE 는 staksnetval+6자, STORAGE2 는 staksnetvalb+6자(해시가 b 로 시작할 수 있어 길이로 구분)
        return "blob2" if len(m.group(1)) == len("staksnetvalb") + 6 else "blob"
    if "kubernetes.default" in cmd:
        return "kubernetes"
    if "mcr.microsoft.com" in cmd:
        return "mcr"
    return cmd.split("://")[1].split(".")[0].split(":")[0]


if a[0] == "exec":
    ns, dep, cmd = a[a.index("-n") + 1], a[a.index("-n") + 2].split("/")[1], a[-1]
    if "dig +short" in cmd:
        print(DNS[target(cmd)])
        sys.exit(0)
    code = CODES[(ns, dep, target(cmd))]
    print(code)
    print()
    print("rc=" + ("0" if code != "000" else "28"))
elif a[:2] == ["get", "networkpolicy"]:
    if os.environ.get("FAKE_NO_NETPOL"):
        sys.stderr.write("NotFound\n")
        sys.exit(1)
    print("2026-01-01T00:00:00Z")
elif a[:2] == ["get", "pods"]:
    if os.environ.get("FAKE_FAIL_GETPODS"):
        sys.stderr.write("connection refused\n")
        sys.exit(1)
    ns = a[a.index("-n") + 1]
    app = a[a.index("-l") + 1].split("=")[1]
    start = "2025-12-31T23:00:00Z" if os.environ.get("FAKE_OLD_POD") else "2026-01-01T00:10:00Z"
    pod = {"metadata": {"name": app + "-0"},
           "spec": {"nodeName": "aks-user-1", "containers": [{"name": "app", "readinessProbe": {"httpGet": {"port": 15020, "path": "/app-health/app/readyz"}}}]},
           "status": {"phase": "Running", "startTime": start, "conditions": [{"type": "Ready", "status": "True"}]}}
    print(json.dumps({"items": [pod]}))
elif a[:2] == ["get", "nodes"]:
    def node(n, ip):
        return {"metadata": {"name": n}, "status": {"addresses": [{"type": "InternalIP", "address": ip}]}}
    print(json.dumps({"items": [node("aks-system-0", "10.50.4.4"), node("aks-user-1", "10.50.4.6")]}))
elif a[:2] == ["get", "--raw"]:
    print("ok")
else:
    sys.stderr.write("fake kubectl: 알 수 없는 명령 " + " ".join(a) + "\n")
    sys.exit(3)
'''


def _run(tmp_path, extra_env=None):
    fake = tmp_path / "bin"
    fake.mkdir()
    for name, body in (("az", FAKE_AZ), ("kubectl", FAKE_KUBECTL)):
        p = fake / name
        p.write_text(textwrap.dedent(body), encoding="utf-8")
        p.chmod(0o755)
    vm_root = tmp_path / "vmroot"
    (vm_root / "state").mkdir(parents=True)
    (vm_root / "state" / "kubeconfig").write_text("fake", encoding="utf-8")
    (vm_root / "aksnetval").symlink_to(ROOT / "aksnetval")
    env = {
        **os.environ,
        "PATH": f"{fake}:{Path(JQ).parent}:{os.environ['PATH']}",
        "AZ": str(fake / "az"),
        "VM_ROOT": str(vm_root),
        **(extra_env or {}),
    }
    r = subprocess.run(["bash", str(ROOT / "vm/03-verify.sh"), "--only", "network"], capture_output=True, text=True, env=env, timeout=300)
    results = sorted((vm_root / "results").glob("*.json"))
    rows = json.loads(results[-1].read_text(encoding="utf-8")) if results else []
    return r, vm_root, {row["Id"]: row for row in rows}


def test_network_all_pass_with_expected_codes(tmp_path):
    r, vm_root, rows = _run(tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert list(rows) == ["TN1", "TN2", "TN3", "TN4", "TN5", "TN6", "TN7", "TN8", "TN9", "TN10", "TN11", "TN12",
                          "TN13", "TN15", "TN16", "TN17", "TN18", "TN19", "TN20", "TN21", "TN22"]
    assert {k: v["Verdict"] for k, v in rows.items() if v["Verdict"] != "PASS"} == {}
    assert rows["TN21"]["From"] == "app/bff-b" and "10.50.4.6:10250" in rows["TN21"]["To"]
    assert "10.50.4.4:10250" in rows["TN22"]["To"] and rows["TN22"]["Observed"] == "401"
    assert rows["TN4"]["Observed"] == "200"
    assert rows["TN12"]["From"] == "app/bff-b" and rows["TN12"]["To"].endswith("(10.50.2.4)")
    assert rows["TN16"]["From"] == "app/bff-a" and rows["TN16"]["To"].endswith("(10.50.2.5)")
    assert rows["TN18"]["Observed"] == "10.0.0.1"
    assert rows["TN2"]["Note"] == "curl rc=28"
    stamp = (vm_root / "state" / "latest").read_text(encoding="utf-8").strip()
    assert (vm_root / "results" / f"{stamp}.md").exists()
    obs = json.loads((vm_root / "state" / "tn14-vm.json").read_text(encoding="utf-8"))
    assert obs["private_enabled"] is True and obs["public_fqdn_enabled"] is False
    assert obs["readyz_ok"] is True and obs["fqdn"] == "aks-x.privatelink.koreacentral.azmk8s.io"


def test_probe_kubectl_failure_becomes_inconclusive_row(tmp_path):
    r, _, rows = _run(tmp_path, {"FAKE_FAIL_GETPODS": "1"})
    assert r.returncode == 0, r.stderr + r.stdout
    assert rows["TN13"]["Verdict"] == "INCONCLUSIVE"
    assert rows["TN13"]["Note"].startswith("실행 오류: kubectl get pods 실패(종료 코드 1): connection refused")
    assert rows["TN1"]["Verdict"] == "PASS"


def test_bffa_reaching_blob_fails_tn15(tmp_path):
    r, _, rows = _run(tmp_path, {"FAKE_BFFA_BLOB": "400"})
    assert r.returncode == 0, r.stderr + r.stdout
    assert rows["TN15"]["Verdict"] == "FAIL"
    assert rows["TN12"]["Verdict"] == "PASS"


def test_pod_started_before_policy_makes_tn19_inconclusive(tmp_path):
    r, _, rows = _run(tmp_path, {"FAKE_OLD_POD": "1"})
    assert r.returncode == 0, r.stderr + r.stdout
    assert rows["TN19"]["Verdict"] == "INCONCLUSIVE"
    assert "정책 적용 전" in rows["TN19"]["Note"]


def test_mid_function_abort_leaves_rows_for_remaining_ids(tmp_path):
    # app_pe_tests 가 TN15~TN18 을 쓴 뒤 중단돼도 TN19·TN20 이 누락되지 않아야 한다(요약이 전부 PASS 로 보이는 것 방지)
    r, _, rows = _run(tmp_path, {"FAKE_NO_NETPOL": "1"})
    assert r.returncode == 0, r.stderr + r.stdout
    assert rows["TN18"]["Verdict"] == "PASS"
    for i in ("TN19", "TN20"):
        assert rows[i]["Verdict"] == "INCONCLUSIVE"
        assert rows[i]["Note"].startswith("실행 오류")
    assert list(rows)[-1] == "TN22"


def test_node_egress_blocked_fails_tn21_tn22(tmp_path):
    # 기본 egress 정책이 노드(host/remote-node)를 막으면 FAIL 로 드러나야 한다
    r, _, rows = _run(tmp_path, {"FAKE_BFFB_NODE": "000"})
    assert r.returncode == 0, r.stderr + r.stdout
    assert rows["TN21"]["Verdict"] == "FAIL" and rows["TN22"]["Verdict"] == "FAIL"


def test_node_control_unreachable_makes_tn21_tn22_inconclusive(tmp_path):
    r, _, rows = _run(tmp_path, {"FAKE_BFFB_NODE": "000", "FAKE_PROBE_NODE": "000"})
    assert r.returncode == 0, r.stderr + r.stdout
    assert rows["TN21"]["Verdict"] == "INCONCLUSIVE" and rows["TN22"]["Verdict"] == "INCONCLUSIVE"
    assert "대조군" in rows["TN21"]["Note"]
