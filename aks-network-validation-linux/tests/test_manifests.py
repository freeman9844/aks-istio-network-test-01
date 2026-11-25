"""기존 Manifests.Tests.ps1 이식. 매니페스트는 이 폴더의 manifests/ 하나로 관리한다."""
import re
from pathlib import Path

import pytest

MANIFESTS = Path(__file__).resolve().parents[1] / "manifests"


def text(name: str) -> str:
    path = MANIFESTS / name
    assert path.exists(), f"매니페스트 없음: {name}"
    return path.read_text(encoding="utf-8")


def count(t: str, pattern: str) -> int:
    return len(re.findall(pattern, t, re.MULTILINE))


DEPLOYMENT = r"^kind:\s*Deployment\s*$"


@pytest.mark.parametrize("name", ["10-web.yaml", "11-app.yaml"])
def test_service_port_named_http(name):
    t = text(name)
    svc = count(t, r"^kind:\s*Service\s*$")
    assert svc > 0
    assert count(t, r"^\s*(-\s*)?name:\s*http\s*$") == svc


@pytest.mark.parametrize("name", ["10-web.yaml", "11-app.yaml", "12-nomesh.yaml", "40-scale.yaml"])
def test_service_account_per_deployment(name):
    t = text(name)
    dep = count(t, DEPLOYMENT)
    assert dep > 0
    assert count(t, r"^\s*serviceAccountName:\s*\S+") == dep


@pytest.mark.parametrize("needle", ["minReplicas: 5", "maxReplicas: 5", "type: Pods", "value: 1", "periodSeconds: 60", "averageUtilization: 70", "cpu: 800m"])
def test_scale_contains(needle):
    assert needle in text("40-scale.yaml")


def test_netpol_name_namespace():
    t = text("30-netpol.yaml")
    assert re.search(r"name:\s*deny-web-to-data", t)
    assert re.search(r"namespace:\s*web\b", t)


def test_netpol_except_pe_subnet():
    assert re.search(r"except:\s*\r?\n\s*-\s*10\.50\.2\.0/24", text("30-netpol.yaml"), re.DOTALL)


@pytest.mark.parametrize("needle", ["cluster.local/ns/web/sa/web-a", "cluster.local/ns/web/sa/web-b", "__GATEWAY_PRINCIPAL__", "istio: aks-istio-ingressgateway-internal"])
def test_istio_contains(needle):
    assert needle in text("20-istio.yaml")


def test_istio_strict_twice():
    assert count(text("20-istio.yaml"), r"mode:\s*STRICT") == 2


@pytest.mark.parametrize("name", ["10-web.yaml", "11-app.yaml"])
def test_readiness_probe_per_deployment(name):
    t = text(name)
    assert count(t, r"^\s*readinessProbe:") == count(t, DEPLOYMENT)


def test_hpa_demo_has_no_replicas():
    assert not re.search(r"^\s*replicas:\s*5\s*$", text("40-scale.yaml"), re.MULTILINE)


def test_zone_constraints_twice():
    t = text("40-scale.yaml")
    assert count(t, r"whenUnsatisfiable:\s*DoNotSchedule") == 2
    assert count(t, r"minDomains:\s*3") == 2
    assert count(t, r"nodeTaintsPolicy:\s*Honor") == 2


def test_zone_spread_zero_replicas():
    assert re.search(r"name:\s*zone-spread\s*\r?\n\s*namespace:\s*scale\s*\r?\nspec:\s*\r?\n\s*replicas:\s*0", text("40-scale.yaml"), re.DOTALL)


# ---------- Linux 판 전용: app 네임스페이스 PE egress 분리(TN12, TN15~TN20) ----------

LINUX_MANIFESTS = MANIFESTS


@pytest.fixture(scope="module")
def app_pe_policies():
    yaml = pytest.importorskip("yaml")
    path = LINUX_MANIFESTS / "31-app-pe-egress.yaml"
    assert path.exists(), "매니페스트 없음: manifests/31-app-pe-egress.yaml"
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
    assert all(d["metadata"]["namespace"] == "app" for d in docs)
    return {d["metadata"]["name"]: d["spec"] for d in docs if d["kind"] == "NetworkPolicy"}


def test_app_node_egress_uses_cilium_entities():
    # Cilium 은 ipBlock 을 노드 IP 에 매칭하지 않으므로(TN21·TN22 실측) 노드 egress 는 엔터티로 연다
    yaml = pytest.importorskip("yaml")
    docs = [d for d in yaml.safe_load_all((LINUX_MANIFESTS / "31-app-pe-egress.yaml").read_text(encoding="utf-8")) if d]
    cnp = [d for d in docs if d["kind"] == "CiliumNetworkPolicy"]
    assert len(cnp) == 1 and cnp[0]["apiVersion"] == "cilium.io/v2"
    assert cnp[0]["metadata"]["name"] == "app-egress-nodes"
    assert cnp[0]["spec"] == {"endpointSelector": {}, "egress": [{"toEntities": ["host", "remote-node"]}]}


def test_app_default_egress_keeps_cluster_and_excludes_pe_subnet(app_pe_policies):
    spec = app_pe_policies["app-egress-default"]
    assert spec["podSelector"] == {} and spec["policyTypes"] == ["Egress"]
    assert {"to": [{"namespaceSelector": {}}]} in spec["egress"]
    assert {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.50.2.0/24"]}}]} in spec["egress"]


@pytest.mark.parametrize("name,app,ip", [
    ("allow-bff-b-to-blob", "bff-b", "10.50.2.4/32"),
    ("allow-bff-a-to-blob2", "bff-a", "10.50.2.5/32"),
])
def test_app_pe_allow_only_own_endpoint(app_pe_policies, name, app, ip):
    spec = app_pe_policies[name]
    assert spec["podSelector"] == {"matchLabels": {"app": app}}
    assert spec["policyTypes"] == ["Egress"]
    assert spec["egress"] == [{"to": [{"ipBlock": {"cidr": ip}}], "ports": [{"protocol": "TCP", "port": 443}]}]


def test_app_pe_policy_count(app_pe_policies):
    assert set(app_pe_policies) == {"app-egress-default", "allow-bff-b-to-blob", "allow-bff-a-to-blob2"}


# ---------- TS6: 영역별 노드 풀에 고정한 영역별 Deployment·HPA ----------

@pytest.fixture(scope="module")
def scale_docs():
    yaml = pytest.importorskip("yaml")
    return [d for d in yaml.safe_load_all(text("40-scale.yaml")) if d]


@pytest.mark.parametrize("n", ["1", "2", "3"])
def test_zonal_deployments(scale_docs, n):
    dep = next(d for d in scale_docs if d["kind"] == "Deployment" and d["metadata"]["name"] == f"hpa-z{n}")
    assert dep["metadata"]["namespace"] == "scale"
    assert "replicas" not in dep["spec"]
    tpl = dep["spec"]["template"]
    assert tpl["metadata"]["labels"] == {"group": "hpa-zonal", "zone": n}
    assert tpl["spec"]["nodeSelector"] == {"kubernetes.azure.com/agentpool": f"userz{n}"}
    assert tpl["spec"]["serviceAccountName"] == "hpa-zonal"
    c = tpl["spec"]["containers"][0]
    assert c["image"] == "registry.k8s.io/pause:3.9" and c["resources"]["requests"]["cpu"] == "100m"


def test_zonal_hpas(scale_docs):
    hpas = {d["metadata"]["name"]: d["spec"] for d in scale_docs if d["kind"] == "HorizontalPodAutoscaler" and d["metadata"]["name"].startswith("hpa-z")}
    assert set(hpas) == {"hpa-z1", "hpa-z2", "hpa-z3"}
    for name, spec in hpas.items():
        assert spec["scaleTargetRef"]["name"] == name
        assert spec["minReplicas"] == 1 and spec["maxReplicas"] == 1
        assert spec["metrics"][0]["resource"]["target"]["averageUtilization"] == 70
        assert spec["behavior"]["scaleDown"]["policies"] == [{"type": "Pods", "value": 1, "periodSeconds": 60}]
    assert any(d["kind"] == "ServiceAccount" and d["metadata"]["name"] == "hpa-zonal" for d in scale_docs)