"""Operator records, content fingerprints, and local evidence bundles."""

import hashlib
import json
import os
import platform
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import z3

from .formal import Z3_DOMAIN, run_lean, run_z3
from .spec import PACK, Spec, read_json

RUN_ID = re.compile(r"[a-f0-9]{32}\Z")
STATUSES = {"pass", "violation", "incomplete"}


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pack_digest(spec: Spec) -> str:
    inputs = {"contract": spec.as_dict(), "files": {}}
    files = inputs["files"]
    root = PACK.parent
    for path in sorted([*root.glob("*.py"), *PACK.rglob("*")]):
        relative = path.relative_to(root)
        if (
            path.is_file()
            and path != PACK / "contract.json"
            and not any(part.startswith(".") or part == "__pycache__" for part in relative.parts)
        ):
            files[relative.as_posix()] = file_digest(path)
    return digest(json_bytes(inputs))


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


def approval(state: Path, contract: str) -> dict:
    try:
        record = read_json(state / "approval.json")
    except FileNotFoundError as exc:
        raise ValueError("Approve the current specification before verifying a candidate") from exc
    if not isinstance(record, dict) or not isinstance(record.get("contract_digest"), str):
        raise ValueError("Approval record is invalid. Check and approve the current contract")
    if record["contract_digest"] != contract:
        raise ValueError("Contract or verifier inputs changed. Check and approve the new digest")
    return record


def model_checks(state: Path, spec: Spec, contract: str, *, fresh: bool) -> list[dict]:
    version = f"{z3.get_full_version()}:{platform.system()}:{platform.machine()}"
    z3_module = Path(z3.__file__).parent / "lib"
    binaries = sorted(file for file in z3_module.glob("libz3.*") if file.is_file())
    if not binaries:
        return [*run_z3(spec), *run_lean(PACK / "lean")]
    key = digest(json_bytes([contract, version, [file_digest(file) for file in binaries]]))
    solver = None
    try:
        with (
            closing(sqlite3.connect(state / "models.sqlite3", timeout=1)) as connection,
            connection,
        ):
            connection.execute(
                "CREATE TABLE IF NOT EXISTS model (key TEXT PRIMARY KEY, result TEXT)"
            )
            if not fresh:
                row = connection.execute(
                    "SELECT result FROM model WHERE key = ?", (key,)
                ).fetchone()
                if row:
                    candidate = json.loads(row[0])
                    if _valid_solver_cache(candidate):
                        solver = [{**check, "cached": True} for check in candidate]
            if solver is None:
                solver = run_z3(spec)
                if status(solver) == "pass":
                    connection.execute(
                        "INSERT OR REPLACE INTO model VALUES (?, ?)",
                        (key, json_bytes(solver).decode()),
                    )
    except (sqlite3.Error, ValueError, TypeError):
        if solver is None:
            solver = run_z3(spec)
    return [*solver, *run_lean(PACK / "lean")]


def _valid_solver_cache(checks: object) -> bool:
    if (
        not isinstance(checks, list)
        or len(checks) != 2
        or not all(
            isinstance(check, dict)
            and check.get("id") == "namespace.identity"
            and check.get("kind") == "z3"
            and check.get("status") == "pass"
            and isinstance(check.get("detail"), str)
            for check in checks
        )
    ):
        return False
    legacy, encoded = checks
    witness = legacy.get("witness")
    if (
        not isinstance(witness, list)
        or len(witness) != 2
        or not all(
            isinstance(path, list)
            and 1 <= len(path) <= 2
            and all(part in Z3_DOMAIN for part in path)
            for path in witness
        )
    ):
        return False
    return (
        legacy.get("check") == "legacy-collision"
        and legacy.get("result") == "sat"
        and witness[0] != witness[1]
        and ".".join(witness[0]) == ".".join(witness[1])
        and encoded.get("check") == "list-identity"
        and encoded.get("result") == "unsat"
        and encoded.get("nonvacuity") == "sat"
    )


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
    for check in record.get("model_checks", []):
        lines.append(f"- {check['kind']} model evidence · {check['status']} · {check['detail']}")
    for case in record.get("cases", []):
        lines.append(f"- Execution · `{case['case_id']}` · {case['status']}")
        for check in case.get("checks", []):
            detail = check.get("detail", check.get("id", "check"))
            lines.append(f"  - {check.get('status', 'incomplete')} · {detail}")
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
