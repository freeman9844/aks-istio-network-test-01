# AKS 네트워크 검증 Linux 관리 VM 실행판 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 PowerShell 판(`aks-network-validation/`)과 같은 검증(T1\~T14)에 T15(API 서버 비공개)를 더해, 공인 IP 없는 Linux 관리 VM 에서 bash + az CLI 로 실행하고 Python 으로 판정하는 새 판을 만든다.

**Architecture:** 로컬 bash 의 얇은 실행기(`local/`)가 `az vm run-command` 로 코드를 vm-mgmt 에 올리고 `systemd-run` 으로 단계 스크립트(`vm/`)를 백그라운드 실행한 뒤, 로그를 폴링하고 결과를 base64 청크로 회수한다. 판정·파싱·보고는 표준 라이브러리만 쓰는 Python 패키지 `aksnetval/` 의 순수 함수이고 bash 는 `python3 -m aksnetval.<모듈> <함수> '<JSON>'` 으로 호출한다.

**Tech Stack:** bash 5, az CLI, kubectl(`az aks install-cli`), Python 3.10+ 표준 라이브러리, pytest(로컬 WSL 에서만), Ubuntu 22.04(vm-mgmt).

**Spec:** `docs/superpowers/specs/2026-11-20-aks-network-validation-linux-mgmt-vm-design.md` (T1\~T14 규칙은 `docs/superpowers/specs/2026-11-19-aks-network-control-validation-design.md`)

## Global Constraints

- 새 폴더 `aks-network-validation-linux/`. 기존 `aks-network-validation/` 은 **수정 금지**. 매니페스트는 복사하지 않고 `../aks-network-validation/manifests/` 를 참조하며 업로드 tar 에 함께 넣는다.
- 모든 bash 스크립트: `#!/usr/bin/env bash` + `set -euo pipefail`, az 실패 시 무엇이 실패했는지 한국어 메시지 후 비 0 종료. 줄바꿈 LF, UTF-8(BOM 없음). 사용자 메시지·주석은 한국어.
- Python: 표준 라이브러리만. 판정 값은 `PASS`/`FAIL`/`INCONCLUSIVE`/`INFO`. 사유 문구는 기존 `Verdict.psm1` 한국어 문구와 같게.
- 결과 열: `Id, From, To, Expect, Observed, Verdict, Note`. MD 표 셀의 `|` 는 `\|` 로, 줄바꿈은 공백으로.
- 기본 RG `rg-aksnetval-linux-krc`, 리전 `koreacentral`. 그 외 이름·주소는 기존 `config.ps1` 과 동일 + `snet-mgmt 10.50.3.64/27`, `vm-mgmt`(Ubuntu2204, `Standard_B2s_v2`, 공인 IP 없음, 시스템 할당 MI), `nat-mgmt`, `pip-nat-mgmt`. Storage 이름 `staksnetval` + sha256("<구독ID>/<RG>") 앞 6자.
- AKS 생성 옵션은 기존 01-infra.ps1 과 동일 + `--enable-private-cluster --disable-public-fqdn`.
- vm-mgmt MI 권한: RG 범위 Contributor + RG 범위 Role Based Access Control Administrator(조건: Network Contributor 만 할당 가능). RG 생성·삭제와 사전 점검은 로컬에서만.
- 사전 점검 필요량: DSv5 22, BSv2 6(vm-mgmt 포함 3대), Total Regional vCPUs 28. 이미 쓰는 분은 뺀다.
- run-command 출력은 마지막 4,096바이트만 온다: 결과 회수 청크 ≤ 3,000바이트(base64 문자), 재조립 후 sha256 비교. 로그 폴링 기본 30초(`--poll`).
- T15 판정 우선순위: FAIL(Private 아님 / public FQDN 켜짐 / vm-mgmt 해석 IP 가 10.50.4.0/22 밖 / 로컬이 공인 IP 로 해석) → INCONCLUSIVE(`/readyz` 실패 / 로컬 접속 성공) → PASS.
- 로컬 실행·pytest 는 WSL bash 기준(`<repo>/aks-network-validation-linux`). 로컬에 jq·shellcheck 없음: 로컬 스크립트는 jq 를 쓰지 않는다(az `--query` 또는 python3).
- 커밋마다 trailer:
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` / `Copilot-Session: 0ee9785a-849d-4265-8cb4-623c98fe757b`

## Review Focus

1. run-command 가 일시 실패하거나 메시지에 `[stdout]` 구간이 없을 때 폴링이 바로 실패로 끝나지 않고 재시도해야 한다(연속 3회 실패 시에만 중단) → Task 6 `test_stdout_section_missing`, `test_poll_tolerates_transient`.
2. 회수 파일 크기가 청크 경계와 정확히 맞거나 0바이트여도 재조립 sha256 이 같아야 한다 → Task 6 `test_fetch_roundtrip_sizes`.
3. 같은 단계가 이미 실행 중일 때 `run.sh` 를 다시 실행하면 새로 시작하지 않고 따라가기만 해야 한다 → Task 6 `test_stage_action`.
4. RG 에 공개 AKS 가 남아 있으면 01-infra 가 아무것도 바꾸기 전에 중단해야 한다 → Task 5 `test_aks_private_guard`.
5. 로컬(Git Bash·macOS)에 dig/getent 가 없어도 T15 로컬 확인이 돌아야 한다: 해석·접속은 python3 socket 으로 → Task 4 `test_local_probe_nxdomain`, `test_local_probe_closed_port`.

---

### Task 1: 파서·vCPU 계산 (`aksnetval/parse.py`)

**Files:**
- Create: `aks-network-validation-linux/aksnetval/__init__.py`(빈 파일), `aks-network-validation-linux/aksnetval/parse.py`, `aks-network-validation-linux/tests/test_parse.py`, `aks-network-validation-linux/pytest.ini`(`testpaths = tests`, `pythonpath = .`)

**Interfaces:**
- Produces:
  - `curl_code(output: str | None) -> str` — `Get-CurlCode` 대응
  - `first_ipv4(output: str | None) -> str` — `Get-FirstIPv4`
  - `@dataclass CurlRun(ran: bool, code: str, rc: int | None)`, `curl_run(output: str | None) -> CurlRun` — `ConvertFrom-CurlRun`
  - `zone_key(zone: str | None) -> str`, `zone_counts(zones: list[str]) -> dict[str, int]`
  - `vcpu_need(own_dsv5: int = 0, own_vm_count: int = 0) -> dict` 키 `max_dsv5, max_total, max_bsv2, dsv5, total, bsv2`. 상수: D4s_v5 4·D2s_v5 2·B2s_v2 2, 사용자 풀 3개 × 최대 3대, B2s_v2 VM 3대(vm-appgw-sim, vm-other, vm-mgmt)
  - `main(argv: list[str]) -> int`: `python3 -m aksnetval.parse <함수명> '<JSON kwargs>'` → 결과 JSON 한 줄 출력(dataclass 는 dict 로). 모든 모듈 CLI 형식 동일.

- [ ] **Step 1: 실패 테스트 작성** — `tests/Common.Tests.ps1` 의 `Get-CurlCode`(7), `Get-FirstIPv4`(4), `ConvertFrom-CurlRun`(6 + rc 126/127 2), `ConvertTo-ZoneKey`(5), `Get-ZoneNodeCounts`(3) 사례를 입력·기대값 그대로 `test_parse.py` 로 옮긴다. vCPU 는 값을 바꿔 이식:

```python
def test_vcpu_need_fresh():
    n = vcpu_need(0, 0)
    assert (n["dsv5"], n["bsv2"], n["total"]) == (22, 6, 28)

def test_vcpu_need_rerun_subtracts_own():
    n = vcpu_need(own_dsv5=8, own_vm_count=3)
    assert (n["dsv5"], n["bsv2"], n["total"]) == (14, 0, 14)

def test_cli_prints_json(capsys):
    assert main(["curl_run", '{"output": "403\\nrc=0"}']) == 0
    assert json.loads(capsys.readouterr().out) == {"ran": True, "code": "403", "rc": 0}
```

- [ ] **Step 2: 실패 확인** — `bash -c "cd <repo>/aks-network-validation-linux && python3 -m pytest tests/test_parse.py -q"` → ImportError 로 FAIL
- [ ] **Step 3: 구현** — 정규식은 `Common.psm1:181-245` 와 동일(`(?s)\[stdout\](.*?)(\[stderr\]|$)`, `(?m)^\s*(\d{3})\s*$`, `(?m)^\s*rc=(\d+)\s*$`, `^(?:.*-)?(\d+)$`). IPv4 판정은 `ipaddress.IPv4Address`.
- [ ] **Step 4: 통과 확인** — 같은 명령 → 전부 PASS
- [x] **Step 5: 커밋** — `feat(linux): 파서·vCPU 계산 Python 이식`

### Task 2: 판정 함수 (`aksnetval/verdict.py`, T1\~T14)

**Files:**
- Create: `aks-network-validation-linux/aksnetval/verdict.py`, `aks-network-validation-linux/tests/test_verdict.py`

**Interfaces:**
- Consumes: Task 1 의 CLI 형식(`main`)
- Produces (반환 dict 키는 소문자, 사유 문구는 `Verdict.psm1` 과 동일):
  - `http_verdict(code: str, expect: str, control_passed: bool = True) -> str` (`expect` ∈ `200|403|blocked`, 그 외 `ValueError`)
  - `is_pe_ip(ip: str) -> bool` (10.50.2.0/24)
  - `tls_verdict(resolved_ip: str, code: str, expect: str, control_passed: bool = True) -> str` (`expect` ∈ `ok|fail`)
  - `hpa_verdict(samples: list[dict]) -> dict` — sample `{"time": ISO8601 str, "replicas": int}`, 반환 `verdict, sequence, intervals_sec, reason`. 기대 순서 `[5,4,3,2]`, 최소 간격 55초
  - `zone_balance_verdict(nodes_by_zone: dict, pending: int, nodes_before: int = -1) -> dict` (`verdict, reason`)
  - `nsg_reuse_verdict(t1_verdict, rule_access, subnet_nsg_id, nsg_name, rule_name) -> dict` (`verdict, note`)
  - `pod_spread_verdict(pods_by_zone: dict, pending: int, expected: int) -> dict`
  - `zone_floor_verdict(rounds: list[dict], expected: int) -> dict`
  - `probe_rewrite_verdict(pods: list[dict]) -> dict` — pod `{"name","ready","port","path"}`, 반환 `verdict, observed, reason`
  - `require(value: str | None, name: str) -> None` — 비면 `ValueError(f"{name} 값이 비어 있습니다")`
  - `main(argv)` — Task 1 과 같은 CLI. 문자열 반환 함수는 `{"verdict": "..."}` 로 출력

- [ ] **Step 1: 실패 테스트 작성** — `tests/Verdict.Tests.ps1` 의 모든 `It`(Http 4, Tls 4, Hpa 4, ZoneBalance 4+3, Assert-NotEmpty 1, NsgReuse 6, PodSpread 4, ProbeRewrite 5, ZoneFloor 4)을 입력·기대값 그대로 옮긴다. Hpa 표본 헬퍼는 `[(초, 레플리카)]` → 기준 시각 + 초 ISO 문자열. CLI 1건 추가: `main(["http_verdict", '{"code":"000","expect":"blocked"}'])` 출력 `{"verdict": "PASS"}`.
- [ ] **Step 2: 실패 확인** — `... python3 -m pytest tests/test_verdict.py -q` → FAIL
- [ ] **Step 3: 구현** — `Verdict.psm1` 와 같은 분기 순서(대조군 → 조건 → 결과, Pending 먼저 등). 영역 키는 `'1','2','3'` 고정, 없으면 0.
- [ ] **Step 4: 통과 확인** — 전부 PASS
- [x] **Step 5: 커밋** — `feat(linux): 판정 함수 Python 이식(T1~T14)`

### Task 3: 매니페스트 정적 검사 (`tests/test_manifests.py`)

**Files:**
- Create: `aks-network-validation-linux/tests/test_manifests.py`

**Interfaces:**
- Consumes: `../aks-network-validation/manifests/*.yaml`(읽기 전용, 경로는 `Path(__file__).parents[2] / "aks-network-validation" / "manifests"`)

- [ ] **Step 1: 테스트 작성** — `tests/Manifests.Tests.ps1` 의 모든 사례(`-ForEach` 는 `pytest.mark.parametrize`)를 같은 정규식·개수로 옮긴다. 주석 줄도 원본처럼 텍스트에 포함해 센다(원본과 같은 결과가 나와야 함).
- [ ] **Step 2: 실행** — `... python3 -m pytest tests/test_manifests.py -q` → 현재 매니페스트로 전부 PASS(검사 코드만 새로 쓰므로 바로 통과가 정상). 하나라도 FAIL 이면 정규식 이식 오류이니 원본과 대조해 고친다.
- [ ] **Step 3: 커밋** — `test(linux): 매니페스트 정적 검사 pytest 이식`

### Task 4: 결과 저장과 T15 (`aksnetval/report.py`, `aksnetval/localprobe.py`, `verdict.api_private_verdict`)

**Files:**
- Create: `aks-network-validation-linux/aksnetval/report.py`, `aks-network-validation-linux/aksnetval/localprobe.py`, `aks-network-validation-linux/tests/test_report.py`
- Modify: `aks-network-validation-linux/aksnetval/verdict.py`, `aks-network-validation-linux/tests/test_verdict.py`

**Interfaces:**
- Produces:
  - `verdict.api_private_verdict(private_enabled: bool, public_fqdn_enabled: bool, vm_ips: list[str], readyz_ok: bool, local_ip: str, local_connected: bool) -> dict` (`verdict, observed, reason`). `local_ip == ""` 는 NXDOMAIN. 우선순위는 Global Constraints 의 T15 규칙.
  - `localprobe.probe(host: str, port: int = 443, timeout: float = 5.0) -> dict` → `{"ip": str ("" = 해석 실패), "connected": bool}` (`socket.getaddrinfo` IPv4 + `socket.create_connection`)
  - `report.row(id, frm, to, expect, observed, verdict, note="") -> dict` (키는 열 이름 그대로)
  - `report.save(rows: list[dict], results_dir: str, stamp: str | None = None) -> dict` `{"json": path, "md": path}`. 파일명 `<yyyyMMdd-HHmmss>.json/.md`, MD 첫 줄 `# 검증 결과 <stamp>`
  - `report.merge_t15(json_path: str, vm_obs_path: str, local: dict) -> dict` — vm 관측 JSON(`private_enabled, public_fqdn_enabled, vm_ips, readyz_ok, fqdn`)과 `local` 로 판정, `Id=T15` 행을 넣거나 교체하고 JSON·MD 를 다시 쓴다. From `vm-mgmt + 로컬`, To `API 서버 <fqdn>:443`, Expect `사설 IP·로컬 접근 불가`
  - CLI: `python3 -m aksnetval.report row ...`(JSON 한 줄 출력, 03-verify 가 `rows.jsonl` 에 누적), `save <dir>`(stdin 의 jsonl), `merge-t15 <json> <vm_obs> <host>`(내부에서 `localprobe.probe`)

- [ ] **Step 1: 실패 테스트 작성**

```python
@pytest.mark.parametrize("kw,expected", [
    (dict(private_enabled=True, public_fqdn_enabled=False, vm_ips=["10.50.4.5"], readyz_ok=True, local_ip="", local_connected=False), "PASS"),
    (dict(private_enabled=False, public_fqdn_enabled=False, vm_ips=["10.50.4.5"], readyz_ok=True, local_ip="", local_connected=False), "FAIL"),
    (dict(private_enabled=True, public_fqdn_enabled=True, vm_ips=["10.50.4.5"], readyz_ok=True, local_ip="", local_connected=False), "FAIL"),
    (dict(private_enabled=True, public_fqdn_enabled=False, vm_ips=["10.50.1.5"], readyz_ok=True, local_ip="", local_connected=False), "FAIL"),
    (dict(private_enabled=True, public_fqdn_enabled=False, vm_ips=["10.50.4.5"], readyz_ok=True, local_ip="20.1.2.3", local_connected=False), "FAIL"),
    (dict(private_enabled=True, public_fqdn_enabled=False, vm_ips=["10.50.4.5"], readyz_ok=False, local_ip="", local_connected=False), "INCONCLUSIVE"),
    (dict(private_enabled=True, public_fqdn_enabled=False, vm_ips=["10.50.4.5"], readyz_ok=True, local_ip="10.50.4.5", local_connected=True), "INCONCLUSIVE"),
    (dict(private_enabled=False, public_fqdn_enabled=False, vm_ips=["10.50.4.5"], readyz_ok=False, local_ip="", local_connected=True), "FAIL"),  # FAIL 이 우선
])
def test_api_private_verdict(kw, expected): ...
```

`test_report.py`: `test_save_escapes_pipe_and_newline`(Observed `a|b\nc` → MD 에 `a\|b c`), `test_save_columns_order`, `test_merge_t15_replaces_existing_row`(T15 행 2번 병합해도 1개), `test_local_probe_nxdomain`(`probe("aksnetval-none.invalid")` → `ip == ""`, `connected is False`), `test_local_probe_closed_port`(빈 포트를 bind 후 close 한 127.0.0.1 → `connected is False`).
- [ ] **Step 2: 실패 확인** — `... python3 -m pytest tests/test_verdict.py tests/test_report.py -q` → FAIL
- [ ] **Step 3: 구현** — `observed` 형식: `private=<bool>, publicFqdn=<bool>, vm=<ip,…>, readyz=<ok|fail>, local=<NXDOMAIN|ip>/<연결|실패>`
- [ ] **Step 4: 통과 확인** — PASS
- [x] **Step 5: 커밋** — `feat(linux): 결과 저장과 T15 판정`

### Task 5: 설정·VM 공통 라이브러리 (`config.sh`, `vm/lib/common.sh`)

**Files:**
- Create: `aks-network-validation-linux/config.sh`, `aks-network-validation-linux/vm/lib/common.sh`, `aks-network-validation-linux/tests/test_shell.py`, `aks-network-validation-linux/.gitignore`(`results/`, `.bundle/`, `__pycache__/`)

**Interfaces:**
- Produces:
  - `config.sh`(source 용): `RG`(환경변수 `AKSNETVAL_RG` 우선, 기본 `rg-aksnetval-linux-krc`), `LOCATION`, `VNET`, `VNET_PREFIX`, `SNET_<AKS_APP|INGRESS|PE|APPGW_SIM|OTHER|MGMT>_{NAME,PREFIX}`, `NSG_AKS_APP`, `NSG_INGRESS`, `NSG_INGRESS_DENY_RULE`, `IDENTITY`, `INGRESS_IP`, `POD_CIDR`, `AKS`, `SYSTEM_VM_SIZE`, `USER_VM_SIZE`, `USER_POOLS=(userz1 userz2 userz3)`, `USER_MIN=1`, `USER_MAX=3`, `VM_SIZE`, `VM_APPGW`, `VM_OTHER`, `VM_MGMT`, `NAT_MGMT`, `PIP_NAT_MGMT`, `STORAGE`(az account show 로 계산, 실패 시 종료), `BLOB_PE`, `BLOB_DNS_ZONE`, `BLOB_DNS_LINK`, `TAGS`, `MANIFESTS_DIR`, `VM_ROOT=/opt/aksnetval`
  - `vm/lib/common.sh`: `die <msg>`, `log <msg>`(시각 접두사), `az_ok <args…>`(실패 시 명령·stderr 와 함께 die), `exists <az show args…>`(0/1), `retry <회수> <초> <cmd…>`, `kube <args…>`(`--kubeconfig $VM_ROOT/state/kubeconfig`), `py <모듈> <함수> <JSON>`(`python3 -m aksnetval.<모듈>`), `aks_private_guard <enablePrivateCluster 값>`(빈 값=AKS 없음 → 0, `true` → 0, 그 외 → 1)

- [ ] **Step 1: 실패 테스트 작성** — `test_shell.py`:
  - `test_bash_syntax`: 폴더 안 모든 `*.sh` 에 `bash -n` → rc 0
  - `test_shellcheck`: `shutil.which("shellcheck")` 없으면 skip, 있으면 `shellcheck -S warning` rc 0
  - `test_config_values`: `AZ_STUB` 로 az 를 가짜 함수로 바꿔 source 후 `echo $RG $SNET_MGMT_PREFIX ${#USER_POOLS[@]}` → `rg-aksnetval-linux-krc 10.50.3.64/27 3`. `AKSNETVAL_RG=x` 이면 `x`
  - `test_aks_private_guard`: 입력 `""`→0, `true`→0, `false`→1, `null`→1
- [ ] **Step 2: 실패 확인** — `... python3 -m pytest tests/test_shell.py -q` → FAIL
- [ ] **Step 3: 구현** — config.sh 는 `az` 를 `${AZ:-az}` 로 호출해 테스트에서 대체 가능하게. 값은 `config.ps1` 과 동일.
- [ ] **Step 4: 통과 확인** — PASS
- [x] **Step 5: 커밋** — `feat(linux): 설정과 VM 공통 bash 라이브러리`

### Task 6: 로컬 실행기 전송 계층 (`local/lib/vmexec.sh`, `local/run.sh`)

**Files:**
- Create: `aks-network-validation-linux/local/lib/vmexec.sh`, `aks-network-validation-linux/local/run.sh`, `aks-network-validation-linux/tests/test_vmexec.py`

**Interfaces:**
- Consumes: `config.sh`(Task 5), `report merge-t15`(Task 4)
- Produces (`vmexec.sh`):
  - `vm_exec <script>` → stdout 구간 문자열. `az vm run-command invoke -g $RG -n $VM_MGMT --command-id RunShellScript --scripts "$script" --query 'value[0].message' -o tsv`. `AKSNETVAL_FAKE_VM=1` 이면 로컬 `bash -c` 로 실행해 `[stdout]…[stderr]…` 형식으로 흉내(테스트용)
  - `stdout_section <message>` — `[stdout]` 와 `[stderr]` 사이, 없으면 빈 문자열
  - `upload_bundle <tgz>` — base64 → 청크(기본 `UPLOAD_CHUNK=32000` 문자, 실측으로 조정) 순서대로 `$VM_ROOT/.upload.b64` 에 추가 → 복원·sha256 비교 → `$VM_ROOT` 에 풀기(이전 코드 디렉터리는 교체, `state/`·`results/` 보존)
  - `fetch_file <원격 경로> <로컬 경로>` — 원격 sha256·base64 길이 조회 후 `FETCH_CHUNK=3000` 문자씩 회수·재조립, sha256 다르면 실패
  - `stage_action <is-active 출력>` — `active|activating` → `follow`, 그 외 → `start`
  - `start_stage <stage> [args…]` — `systemd-run --unit aksnetval-<stage> --working-directory $VM_ROOT /bin/bash -c 'vm/<stage>.sh "$@" > logs/<stage>.log 2>&1; echo $? > state/<stage>.rc'`(시작 전 `systemctl reset-failed`, rc 파일 삭제)
  - `poll_stage <stage>` — `POLL`(기본 30)초마다 `systemctl is-active` + 새 로그 줄(이미 본 줄 수 이후, 3,000바이트 이내) 출력. 끝나면 rc 파일 값으로 종료. run-command 실패는 연속 3회까지 경고 후 재시도
- `run.sh <infra|apply|verify> [--only all|network|hpa|floor|zone|spread] [--poll N]`: 번들(`aks-network-validation-linux` 의 `aksnetval/ vm/ config.sh` + `../aks-network-validation/manifests`) 업로드 → `stage_action` → start/follow → poll → verify 면 최신 결과 `.json/.md` 와 `state/t15-vm.json` 회수 후 `python3 -m aksnetval.report merge-t15` 로 T15 합치기 → 요약(판정별 개수) 출력.

- [ ] **Step 1: 실패 테스트 작성** — `test_vmexec.py`(모두 `AKSNETVAL_FAKE_VM=1`, `VM_ROOT=<tmp_path>` 로 bash 실행):
  - `test_stdout_section_missing`: 메시지에 `[stdout]` 없음 → `""`
  - `test_upload_roundtrip`: 3개 파일이 든 tgz 업로드 → `$VM_ROOT` 내용 동일, `UPLOAD_CHUNK=100` 으로도 동일
  - `test_fetch_roundtrip_sizes`: 크기 0, 2,250(base64 3,000 정확히), 7,001 바이트 → 로컬 파일 sha256 동일
  - `test_fetch_detects_corruption`: 원격 sha256 을 다르게 흉내 → 비 0 종료
  - `test_stage_action`: `active`→follow, `activating`→follow, `inactive`/`failed`/`""`→start
  - `test_poll_tolerates_transient`: `vm_exec` 를 앞 2회 실패하는 가짜로 바꿔도 `poll_stage` 성공, 3회 연속 실패면 비 0
- [ ] **Step 2: 실패 확인** — `... python3 -m pytest tests/test_vmexec.py -q` → FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — PASS. 전체 `python3 -m pytest -q` 도 PASS
- [x] **Step 5: 커밋** — `feat(linux): 로컬 실행기 업로드·폴링·회수`

### Task 7: 부트스트랩·사전 점검·정리 (`local/00-bootstrap.sh`, `aksnetval/preflight.py`, `local/99-cleanup.sh`)

**Files:**
- Create: `aks-network-validation-linux/aksnetval/preflight.py`, `aks-network-validation-linux/tests/test_preflight.py`, `aks-network-validation-linux/local/00-bootstrap.sh`, `aks-network-validation-linux/local/99-cleanup.sh`

**Interfaces:**
- Consumes: `parse.vcpu_need`, `config.sh`, `vmexec.sh`
- Produces: `preflight.problems(usage: list, skus: list, own_dsv5: int, own_vm_count: int, system_size: str, user_size: str, vm_size: str) -> list[str]` — `az vm list-usage`·`az vm list-skus` JSON 그대로 입력. 규칙은 `Common.psm1:100-160`(DSv5·cores 잔여, D 계열 영역 1·2·3 제한, VM SKU 리전 제한) + BSv2(`standardBSv2Family`) 잔여 ≥ `bsv2`. 정보 줄은 stderr 로.

- [ ] **Step 1: 실패 테스트 작성** — `test_preflight.py`: 충분 → `[]`; DSv5 잔여 21 → 사유에 `Standard DSv5 Family`; BSv2 잔여 4 → 사유에 `BSv2`; cores 항목 없음 → 해당 검사 생략(`[]`); `Standard_D2s_v5` 영역 2 제한 → 사유에 `영역 2`; VM SKU 에 Location 제한 → 사유에 `제한됨`
- [ ] **Step 2: 실패 확인** — `... python3 -m pytest tests/test_preflight.py -q` → FAIL
- [ ] **Step 3: 구현**
  - `preflight.py` + CLI
  - `00-bootstrap.sh [--force]`: 구독 이름 표시·확인 → 사전 점검(문제 있으면 아무것도 만들지 않고 종료) → RG(태그) → VNet + `snet-mgmt` → `pip-nat-mgmt`(Standard, static) + `nat-mgmt` → 서브넷 연결 → `vm-mgmt`(`--image Ubuntu2204 --size Standard_B2s_v2 --public-ip-address "" --nsg "" --assign-identity [system]`) → 역할 2개(RBAC Admin 조건식: Network Contributor 역할 정의 ID `4d97b98b-1d4f-4787-a291-c67834d212e7` 만 할당·삭제 허용, `--condition-version 2.0`) → run-command 로 도구 설치(`az` 공식 설치 스크립트, `python3-venv`, `jq`, `dnsutils`, `az aks install-cli`) + `az login --identity` 확인. 모든 단계 "이미 있으면 건너뜀"
  - `99-cleanup.sh [--force]`: RG 이름 입력 확인 후 `az group delete --no-wait --yes`
- [ ] **Step 4: 통과 확인** — pytest 전체 PASS, `bash -n` PASS
- [x] **Step 5: 커밋** — `feat(linux): 부트스트랩·사전 점검·정리`

### Task 8: 인프라·적용 단계 (`vm/01-infra.sh`, `vm/02-apply.sh`)

**Files:**
- Create: `aks-network-validation-linux/vm/01-infra.sh`, `aks-network-validation-linux/vm/02-apply.sh`

**Interfaces:**
- Consumes: `config.sh`, `vm/lib/common.sh`(Task 5)
- Produces: `$VM_ROOT/state/kubeconfig`(`az aks get-credentials --file`), RG 안 리소스 일체

- [ ] **Step 1: 01-infra.sh** — 시작 시 `az login --identity`, `aks_private_guard "$(az aks show … --query apiServerAccessProfile.enablePrivateCluster -o tsv 2>/dev/null)"` 실패면 즉시 중단. 이후 `01-infra.ps1:44-170` 순서·옵션 그대로 이식(서브넷, NSG 2개와 규칙, `id-aksnetval` + VNet Network Contributor, Storage + Blob PE + 사설 DNS, VM 2대, AKS + `--enable-private-cluster --disable-public-fqdn`, 영역별 사용자 풀). `--query` 에 JMESPath 괄호 사용 가능(cmd.exe 제약 없음).
- [ ] **Step 2: 02-apply.sh** — `02-apply.ps1:73-150` 1)\~7) 이식. `__GATEWAY_PRINCIPAL__` 치환은 `sed`, 결과는 `results/20-istio.rendered.yaml`.
- [ ] **Step 3: 검사** — `... python3 -m pytest tests/test_shell.py -q` PASS
- [ ] **Step 4: 커밋** — `feat(linux): 인프라·매니페스트 적용 단계`

### Task 9: 검증 단계 (`vm/03-verify.sh`)

**Files:**
- Create: `aks-network-validation-linux/vm/03-verify.sh`

**Interfaces:**
- Consumes: `report row/save`, `verdict.*`, `parse.*`(CLI), `vm/lib/common.sh`
- Produces: `$VM_ROOT/results/<stamp>.json/.md`, `$VM_ROOT/state/t15-vm.json`, `$VM_ROOT/state/latest`(최신 stamp 한 줄)

- [ ] **Step 1: 이식** — `03-verify.ps1` 의 `--only` 묶음과 각 테스트를 Id·From·To·Expect 문자열까지 그대로 이식: network(`:55-131` T1\~T11, `:132-157` T14), hpa(`:188-244` T12·T12b), floor(`:245-282` T12c), zone(`:355-416` T13), spread(`:417-464` T13b). Pod curl 명령은 `curl -s -o /dev/null -w '%{http_code}' -m 10 …; rc=$?; echo; echo rc=$rc`. VM curl(T1\~T3)은 `az vm run-command invoke` 를 vm-mgmt 의 관리 ID 로. 원본처럼 테스트마다 오류를 잡아 `INCONCLUSIVE`·`실행 오류: …` 행을 남기고(bash 는 서브셸 + `|| …`), 스케일 테스트는 `trap` 으로 원복(minReplicas·zone-spread 0). 결과 정렬 순서 `T1 T2 T2c T3 T4 … T11 T12 T12b T12c T13 T13b T14`(T15 는 로컬 병합 시 끝에 추가).
- [ ] **Step 2: T15 VM 관측 추가** — `all` 또는 `network` 일 때 `az aks show` 의 `apiServerAccessProfile.enablePrivateCluster`·`enablePrivateClusterPublicFqdn`·`privateFqdn`, `getent ahostsv4 <privateFqdn>` IP 목록, `kube get --raw /readyz` 결과를 `state/t15-vm.json` 로 저장(판정은 로컬 Task 6 이 함)
- [ ] **Step 3: 검사** — `test_shell.py` PASS
- [ ] **Step 4: 커밋** — `feat(linux): 검증 단계(T1~T14, T15 관측)`

### Task 9b: app 네임스페이스 PE egress 분리 (T11 변경, T16\~T21 추가, Linux 판 전용)

사용자 요구: NetworkPolicy 로 bff-b 만 Blob PE egress 를 허용하되, bff-b 의 기본 egress 는 막지 않는다. bff-a 는 다른 PE(두 번째 Storage) 만 허용.

**Files:**
- Create: `aks-network-validation-linux/manifests-linux/31-app-pe-egress.yaml` — `app-egress-default`(podSelector `{}`, 클러스터 내부 + `0.0.0.0/0 except 10.50.2.0/24`), `allow-bff-b-to-blob`(10.50.2.4/32:443), `allow-bff-a-to-blob2`(10.50.2.5/32:443)
- Modify: `config.sh`(`BLOB_PE_IP=10.50.2.4`, `BLOB2_PE=pe-blob2-aksnetval`, `BLOB2_PE_IP=10.50.2.5`, `STORAGE2=staksnetvalb<해시>`), `vm/01-infra.sh`(PE 고정 IP `--ip-config`, 두 번째 Storage·PE, NIC IP 불일치 시 중단), `local/run.sh`(번들에 `manifests-linux/*.yaml` 합침), `vm/02-apply.sh`(31 적용 후 app Deployment 재시작), `vm/03-verify.sh`(T11 출발지 bff-b, `app_pe_tests`), `aksnetval/verdict.py`(`tls_verdict(..., expected_ip="")`, `started_after(pod_start, policy_created) -> bool`), `aksnetval/report.py`(ORDER 에 T16\~T21 을 T11 뒤에)
- 공유 매니페스트(`aks-network-validation/manifests`)와 PowerShell 판은 바꾸지 않는다

| Id | From | To | Expect | 대조군 |
|---|---|---|---|---|
| T11 | app/bff-b | Blob(10.50.2.4) | TLS 연결 성공(≠000) | — (T10·T16 의 대조군) |
| T16 | app/bff-a | Blob(10.50.2.4) | TLS 연결 실패(000) | T11 |
| T17 | app/bff-a | Blob2(10.50.2.5) | TLS 연결 성공(≠000) | — (T18 의 대조군) |
| T18 | app/bff-b | Blob2(10.50.2.5) | TLS 연결 실패(000) | T17 |
| T19 | app/bff-b | CoreDNS(kubernetes.default 해석) | 해석 성공 | — |
| T20 | app/bff-b | istiod(Envoy :15021 ready) | 200, Pod 가 정책 생성 뒤 기동 | 기동 시각 아니면 INCONCLUSIVE |
| T21 | app/bff-b | mcr.microsoft.com:443 | 연결 성공(≠000) | — |

- [x] **Step 1: RED** — `test_manifests`(정책 3개 구조), `test_shell::test_config_second_blob_pe`, `test_report::test_order_places_app_pe_tests_after_t11`, `test_verdict`(expected_ip·started_after), `test_verify`(행 순서, T16 FAIL 변조, T20 INCONCLUSIVE)
- [x] **Step 2: GREEN** — 위 Files 구현, 전체 192 passed
- [x] **Step 3: 커밋** — `feat(linux): app 네임스페이스 PE egress 분리 검증(T16~T21)`

### Task 9c: bff-b → 노드 egress (T22·T23 추가, Linux 판 전용)

Cilium 이 노드 IP 를 host·remote-node 엔터티로 다루므로, `app-egress-default` 의 ipBlock `0.0.0.0/0` 이 노드까지 허용하는지 실측한다.

**Files:**
- Modify: `aksnetval/kube.py`(`node_targets`), `aksnetval/verdict.py`(`node_reach_verdict`), `aksnetval/report.py`(ORDER 에 T22·T23), `vm/03-verify.sh`(`node_egress_tests`), `manifests-linux/31-app-pe-egress.yaml`(CiliumNetworkPolicy `app-egress-nodes`)

| Id | From | To | 기대 | 대조군 |
|---|---|---|---|---|
| T22 | app/bff-b | 자기 노드 InternalIP:10250(kubelet) | 연결 성공(≠000) | nomesh/probe 같은 IP |
| T23 | app/bff-b | 다른 노드 InternalIP:10250(kubelet) | 연결 성공(≠000) | nomesh/probe 같은 IP |

- [x] **Step 1: RED** — `test_kube`(node_targets), `test_verdict`(node_reach_verdict 5가지), `test_report`(순서), `test_verify`(행 순서, FAIL 변조, 대조군 실패 시 INCONCLUSIVE)
- [x] **Step 2: GREEN** — 전체 213 passed
- [x] **Step 3: 실측** — 정책 3개만으로는 T22·T23 **FAIL**(000, 대조군 401). ipBlock `10.50.4.0/24` 추가도 000. CiliumNetworkPolicy `toEntities: [host, remote-node]` 추가 시 401. Cilium 기본값에서 ipBlock 은 Pod·노드 IP 에 매칭되지 않는다(https://docs.cilium.io/en/stable/network/kubernetes/policy/)
- [x] **Step 4: 보완** — `test_manifests::test_app_node_egress_uses_cilium_entities` RED → `app-egress-nodes` 추가 GREEN → 재적용 후 `--only network` **22 PASS**(`20261120-082121`)

### Task 10: 실측과 문서

**Files:**
- Create: `aks-network-validation-linux/README.md`
- Create(실측 산출물, 커밋 안 함): `aks-network-validation-linux/results/`

- [x] **Step 1: 실행(WSL)** — `local/00-bootstrap.sh` → `local/run.sh infra` → `local/run.sh apply` → `local/run.sh verify`. 실패 시 원인을 고치고 해당 단계만 다시 실행(멱등). run-command 업로드 청크 한도 초과가 나오면 `UPLOAD_CHUNK` 를 줄이고 값을 README 에 적는다.
- [x] **Step 2: 결과 확인** — 결과 JSON 판정이 T1\~T21 **24 PASS + T12b INFO**. 다르면 원인 분석 후 수정·재실행(Private Cluster + Istio 내부 Gateway 조합 문제는 설계 9절에 기록).
- [x] **Step 3: README 작성** — 목적, 구조도(설계 5.1·5.3), 사전 요구(WSL/Cloud Shell, az login, 권한), 실행 순서, `--only`/`--poll`, T15 설명, 최신 결과 표(1회분만), 정리 방법. 표 셀 안 `|` 는 `\|`.
- [x] **Step 4: 정리** — `local/99-cleanup.sh --force` 후 `az group exists -n rg-aksnetval-linux-krc` 가 `false` 가 될 때까지 확인
- [x] **Step 5: 커밋** — `docs(linux): 실행 안내와 실측 결과`

### Task 11: 폴더 통합 (PowerShell 판 삭제, 매니페스트 이동)

**Files:**
- Move: `aks-network-validation/manifests/*.yaml`, `aks-network-validation-linux/manifests-linux/31-app-pe-egress.yaml` → `aks-network-validation-linux/manifests/`
- Delete: `aks-network-validation/`(ps1·lib·tests·README). 미추적 `results/` 는 `aks-network-validation-linux/results/powershell/` 로 이동
- Modify: `config.sh`(`MANIFESTS_DIR="$_CFG_DIR/manifests"`), `local/run.sh`(`manifests-linux` 복사 제거), `tests/test_manifests.py`(경로), `tests/test_shell.py`(`test_config_manifests_dir_is_own_folder`), `README.md`

- [x] **Step 1: 실패 테스트** — 매니페스트 경로를 `<폴더>/manifests` 로 바꾸고 `MANIFESTS_DIR` 검사 추가 → 27 failed, 4 errors
- [x] **Step 2: 이동·수정** — `git mv` 로 이동, PowerShell 판 `git rm`, config·run.sh 수정
- [x] **Step 3: 통과 확인** — `python3 -m pytest -q` → 215 passed, 1 skipped
