"""Polaris verifier fingerprint, approval, and model cache."""

import json
import platform
import sqlite3
from contextlib import closing
from pathlib import Path

import z3

from mentat.evidence import digest, file_digest, json_bytes, read_json, status

from .formal import Z3_DOMAIN, run_lean, run_z3
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
    for name in ("colima.py", "command.py", "evidence.py"):
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
