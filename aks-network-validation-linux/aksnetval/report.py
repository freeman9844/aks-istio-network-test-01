"""결과 행 생성·저장(기존 Save-Results 대응)과 TN14 병합."""
import json
import sys
from datetime import datetime
from pathlib import Path

from aksnetval import localprobe
from aksnetval.verdict import api_private_verdict

COLUMNS = ("Id", "From", "To", "Expect", "Observed", "Verdict", "Note")
ORDER = [*[f"TN{i}" for i in range(1, 23)], *[f"TS{i}" for i in range(1, 6)]]


def row(id, frm, to, expect, observed, verdict, note="") -> dict:  # noqa: A002
    return dict(zip(COLUMNS, (id, frm, to, expect, observed, verdict, note)))


def _sort_key(r: dict):
    rid = r.get("Id", "")
    return (ORDER.index(rid), "") if rid in ORDER else (len(ORDER), rid)


def _esc(v) -> str:
    return str("" if v is None else v).replace("|", "\\|").replace("\r\n", " ").replace("\n", " ")


def _write(rows: list[dict], json_path: Path, md_path: Path, stamp: str) -> None:
    rows = sorted(({c: r.get(c, "") for c in COLUMNS} for r in rows), key=_sort_key)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# 검증 결과 {stamp}", "", "| " + " | ".join(COLUMNS) + " |", "|" + "|".join("---" for _ in COLUMNS) + "|"]
    lines += ["| " + " | ".join(_esc(r[c]) for c in COLUMNS) + " |" for r in rows]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save(rows: list[dict], results_dir: str, stamp: str | None = None) -> dict:
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    d = Path(results_dir)
    d.mkdir(parents=True, exist_ok=True)
    j, m = d / f"{stamp}.json", d / f"{stamp}.md"
    _write(rows, j, m, stamp)
    return {"json": str(j), "md": str(m)}


def merge_tn14(json_path: str, vm_obs_path: str, local: dict) -> dict:
    obs = json.loads(Path(vm_obs_path).read_text(encoding="utf-8"))
    if obs.get("error"):
        v = {"observed": "관측 실패", "verdict": "INCONCLUSIVE", "reason": f"vm-mgmt 관측 실패: {obs['error']}"}
    else:
        v = api_private_verdict(
            private_enabled=bool(obs.get("private_enabled")),
            public_fqdn_enabled=bool(obs.get("public_fqdn_enabled")),
            vm_ips=list(obs.get("vm_ips") or []),
            readyz_ok=bool(obs.get("readyz_ok")),
            local_ip=local.get("ip", ""),
            local_connected=bool(local.get("connected")),
        )
    tn14 = row("TN14", "vm-mgmt + 로컬", f"API 서버 {obs.get('fqdn', '')}:443", "사설 IP·로컬 접근 불가", v["observed"], v["verdict"], v["reason"])
    j = Path(json_path)
    rows = [r for r in json.loads(j.read_text(encoding="utf-8")) if r.get("Id") != "TN14"] + [tn14]
    _write(rows, j, j.with_suffix(".md"), j.stem)
    return tn14


def main(argv: list[str]) -> int:
    if not argv:
        print("사용법: row <id> <from> <to> <expect> <observed> <verdict> [note] | save <dir> | merge-tn14 <json> <vm_obs> <host>", file=sys.stderr)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "row" and len(args) in (6, 7):
        print(json.dumps(row(*args), ensure_ascii=False))
    elif cmd == "save" and len(args) == 1:
        rows = [json.loads(line) for line in sys.stdin if line.strip()]
        print(json.dumps(save(rows, args[0]), ensure_ascii=False))
    elif cmd == "merge-tn14" and len(args) == 3:
        print(json.dumps(merge_tn14(args[0], args[1], localprobe.probe(args[2])), ensure_ascii=False))
    else:
        print(f"인자가 올바르지 않습니다: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
