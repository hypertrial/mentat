"""Operator records, content fingerprints, and local evidence bundles."""

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

RUN_ID = re.compile(r"[a-f0-9]{32}\Z")
STATUSES = {"pass", "violation", "incomplete"}


def read_json(path: Path):
    with path.open("rb") as stream:
        return decode_json(stream.read(1_000_001))


def decode_json(data: bytes):
    if len(data) > 1_000_000:
        raise ValueError("JSON exceeds 1 MB")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            data,
            object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds parser limit") from exc


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def status(checks: list[dict]) -> str:
    outcomes = {check["status"] for check in checks}
    if outcomes - STATUSES or not outcomes:
        return "incomplete"
    if "violation" in outcomes:
        return "violation"
    return "incomplete" if "incomplete" in outcomes else "pass"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_record(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def state_directory(path: Path) -> Path:
    path = path.expanduser().resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    stat = path.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise ValueError("Mentat state must be an operator-owned directory with mode 0700")
    return path


def report(record: dict) -> bytes:
    lines = [
        "# Mentat verification evidence",
        "",
        f"- Run `{record['run_id']}`",
        f"- Result **{record['status']}**",
        f"- Candidate `{record['revision']}`",
        f"- Approved contract `{record['contract_digest']}`",
        "",
    ]
    if record.get("kind") == "project-verification":
        lines += [f"- Contract base `{record['base']}`", f"- Runner `{record['runner']}`", ""]
        for claim in record["claims"]:
            lines.append(f"- Claim `{claim['id']}` · {claim['statement']}")
        lines.append("")
    for check in record.get("model_checks", []):
        claim = f" · `{check['claim']}`" if "claim" in check else ""
        lines.append(
            f"- {check['kind']} model evidence{claim} · {check['status']} · {check['detail']}"
        )
    if record.get("behavior_skipped"):
        lines.append("- Behavior checks require passing formal checks.")
    for case in record.get("cases", []):
        lines.append(f"- Execution · `{case['case_id']}` · {case['status']}")
        for check in case.get("checks", []):
            detail = check.get("detail", check.get("id", "check"))
            claim = f"`{check['claim']}` · " if "claim" in check else ""
            lines.append(f"  - {claim}{check.get('status', 'incomplete')} · {detail}")
    for regression in record.get("regressions", []):
        lines.append(f"- Native pytest · `{regression['case_id']}` · {regression['status']}")
    for method, counts in record.get("counts", {}).items():
        summary = ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in counts.items())
        lines.append(f"- {method} · {summary}")
    if record.get("kind") == "evaluation":
        lines.append(f"- Formal checks · {record['formal_seconds']} seconds")
        lines.append(
            f"- Native historical and repaired regression · {' / '.join(record['native_regression'])}"
        )
    lines += ["", "## Reproduce", "", f"`{record['reproduce']}`", ""]
    return "\n".join(lines).encode()


def save_run(state: Path, record: dict, artifacts: dict[str, bytes]) -> Path:
    run_id = record["run_id"]
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid run identifier")
    artifacts = {**artifacts, "report.md": report(record)}
    manifest = json_bytes(
        {**record, "artifacts": {name: digest(data) for name, data in artifacts.items()}}
    )
    runs = state / "runs"
    runs.mkdir(mode=0o700, exist_ok=True)
    destination = runs / run_id
    with tempfile.TemporaryDirectory(dir=runs) as staging:
        bundle = Path(staging) / "bundle"
        bundle.mkdir(mode=0o700)
        for name, data in {**artifacts, "manifest.json": manifest}.items():
            if Path(name).name != name or name in {".", ".."}:
                raise ValueError("Invalid evidence filename")
            write_record(bundle / name, data)
        bundle.rename(destination)
    write_record(state / "receipts" / run_id, digest(manifest).encode())
    return destination


def read_run(state: Path, run_id: str) -> tuple[Path, dict]:
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid run identifier")
    bundle = state / "runs" / run_id
    manifest = bundle / "manifest.json"
    if bundle.is_symlink() or manifest.is_symlink():
        raise ValueError("Evidence path must be a regular bundle")
    raw = manifest.read_bytes()
    if digest(raw) != (state / "receipts" / run_id).read_text():
        raise ValueError("Evidence manifest failed its local integrity check")
    record = json.loads(raw)
    if record["run_id"] != run_id or set(path.name for path in bundle.iterdir()) != {
        "manifest.json",
        *record["artifacts"],
    }:
        raise ValueError("Evidence file set changed")
    for name, expected in record["artifacts"].items():
        path = bundle / name
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected:
            raise ValueError(f"Evidence artifact failed its integrity check: {name}")
    return bundle, record
