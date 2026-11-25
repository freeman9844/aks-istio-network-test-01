import json
import socket
from pathlib import Path

from aksnetval import localprobe, report


def _rows():
    return [
        report.row("TN2", "web-a", "data", "200", "200", "PASS"),
        report.row("TN1", "client", "web", "blocked", "a|b\nc", "PASS", "메모"),
    ]


def test_row_keys_are_column_names():
    assert list(report.row("TN1", "a", "b", "200", "200", "PASS").keys()) == list(report.COLUMNS)


def test_save_escapes_pipe_and_newline(tmp_path):
    out = report.save(_rows(), str(tmp_path), stamp="20261120-000000")
    md = Path(out["md"]).read_text(encoding="utf-8")
    assert "a\\|b c" in md
    assert md.splitlines()[0] == "# 검증 결과 20261120-000000"


def test_save_columns_order(tmp_path):
    out = report.save(_rows(), str(tmp_path), stamp="s")
    md = Path(out["md"]).read_text(encoding="utf-8").splitlines()
    assert md[2] == "| Id | From | To | Expect | Observed | Verdict | Note |"
    data = json.loads(Path(out["json"]).read_text(encoding="utf-8"))
    assert [r["Id"] for r in data] == ["TN1", "TN2"]  # 정해진 순서로 정렬
    assert list(data[0].keys()) == list(report.COLUMNS)


def test_save_default_stamp_format(tmp_path):
    out = report.save(_rows(), str(tmp_path))
    name = Path(out["json"]).stem
    assert len(name) == 15 and name[8] == "-"


def test_order_places_app_pe_tests_after_tn12():
    rows = [report.row(i, "", "", "", "", "PASS") for i in ("TS1", "TN22", "TN20", "TN15", "TN21", "TN12", "TN18")]
    data = json.loads(Path(report.save(rows, "/tmp/aksnetval-order", stamp="o")["json"]).read_text(encoding="utf-8"))
    assert [r["Id"] for r in data] == ["TN12", "TN15", "TN18", "TN20", "TN21", "TN22", "TS1"]


def test_order_is_network_numeric_then_scaling():
    rows = [report.row(i, "", "", "", "", "PASS") for i in ("TS5", "TN14", "TS1", "TN13", "TN2", "TN3", "TN12")]
    data = json.loads(Path(report.save(rows, "/tmp/aksnetval-order", stamp="o2")["json"]).read_text(encoding="utf-8"))
    assert [r["Id"] for r in data] == ["TN2", "TN3", "TN12", "TN13", "TN14", "TS1", "TS5"]


def test_merge_tn14_replaces_existing_row(tmp_path):
    out = report.save(_rows(), str(tmp_path), stamp="s")
    obs = tmp_path / "tn14.json"
    obs.write_text(json.dumps(dict(private_enabled=True, public_fqdn_enabled=False, vm_ips=["10.50.4.5"], readyz_ok=True, fqdn="x.privatelink.koreacentral.azmk8s.io")), encoding="utf-8")
    local = {"ip": "", "connected": False}
    report.merge_tn14(out["json"], str(obs), local)
    report.merge_tn14(out["json"], str(obs), local)
    data = json.loads(Path(out["json"]).read_text(encoding="utf-8"))
    tn14 = [r for r in data if r["Id"] == "TN14"]
    assert len(tn14) == 1
    assert tn14[0]["Verdict"] == "PASS"
    assert tn14[0]["To"] == "API 서버 x.privatelink.koreacentral.azmk8s.io:443"
    assert "TN14" in Path(out["md"]).read_text(encoding="utf-8")


def test_merge_tn14_vm_observation_error_is_inconclusive(tmp_path):
    # vm-mgmt 에서 az aks show 가 실패하면 관측값이 비어 있으므로 FAIL("Private 아님")이 아니라 판정 불가여야 한다
    out = report.save(_rows(), str(tmp_path), stamp="s")
    obs = tmp_path / "tn14.json"
    obs.write_text(json.dumps(dict(error="az aks show 실패", fqdn="")), encoding="utf-8")
    tn14 = report.merge_tn14(out["json"], str(obs), {"ip": "", "connected": False})
    assert tn14["Verdict"] == "INCONCLUSIVE"
    assert tn14["Observed"] == "관측 실패"
    assert tn14["Note"] == "vm-mgmt 관측 실패: az aks show 실패"


def test_local_probe_nxdomain():
    r = localprobe.probe("aksnetval-none.invalid", timeout=2)
    assert r == {"ip": "", "connected": False}


def test_local_probe_closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = localprobe.probe("127.0.0.1", port, timeout=2)
    assert r == {"ip": "127.0.0.1", "connected": False}


def test_order_ts6_after_ts5():
    rows = [report.row(i, "", "", "", "", "PASS") for i in ("TS6", "TN1", "TA1", "TS5")]
    data = json.loads(Path(report.save(rows, "/tmp/aksnetval-order", stamp="o3")["json"]).read_text(encoding="utf-8"))
    assert [r["Id"] for r in data] == ["TN1", "TS5", "TS6", "TA1"]