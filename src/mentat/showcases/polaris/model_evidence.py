"""Polaris verifier fingerprint and approval."""

from pathlib import Path

from mentat.canonical import digest, json_bytes
from mentat.evidence import file_digest, read_json

from .spec import PACK, Spec


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
    for name in (
        "canonical.py",
        "claims.py",
        "colima.py",
        "command.py",
        "evidence.py",
        "gitio.py",
        "process.py",
        "project.py",
        "project_formal.py",
        "records.py",
    ):
        files[f"mentat/{name}"] = file_digest(root.parents[1] / name)
    return digest(json_bytes(inputs))


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
