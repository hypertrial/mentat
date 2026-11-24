"""Locally pinned minimum gates; repository proposals cannot weaken authority.

This is an ordinary trusted local file, not a signed attestation or identity system.
"""

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path

from . import autonomy_private_merge


def workspace_state_path(repo: Path, state_root: Path) -> Path:
    root = state_root.expanduser().resolve()
    if root.is_relative_to(repo.resolve()):
        raise ValueError("autonomy state must be outside the application repository")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return (
        root / "workspaces" / hashlib.sha256(str(repo.resolve()).encode()).hexdigest() / "state.db"
    )


def workspace_config(repo: Path, model: str) -> dict:
    local, worker = minimum_gates(repo)
    return {
        "schema_version": 1,
        "model": model,
        "reasoning_effort": "high",
        "session_seconds": 7200,
        "concurrency": 3,
        "max_attempts": 3,
        "auto_merge": bool(local or worker),
        "base_branch": "main",
        "remote": "origin",
        "local_checks": local,
        "worker_checks": worker,
        "source_checks": minimum_source_checks(repo),
        "worker": "mentat-autonomy",
        "scope": "all first-party application surfaces; trust boundaries and gaps remain explicit",
    }



def _commands(value: object) -> list[list[str]]:
    if not isinstance(value, list) or not all(
        isinstance(argv, list) and argv and all(isinstance(x, str) and x and "\0" not in x for x in argv)
        for argv in value
    ):
        raise ValueError("invalid pinned command plan")
    return value


def minimum_gates(repo: Path) -> tuple[list[list[str]], list[list[str]]]:
    if (repo / "src/mentat/proof_checker.py").is_file() and (repo / "scripts/verify").is_file():
        return [["scripts/verify"], ["scripts/check-autonomy-package"]], [["scripts/verify-worker"]]
    if (repo / "verify.py").is_file() and (repo / "app.py").is_file() and (repo / "titanic.csv").is_file():
        return [[".venv/bin/python", "-B", "verify.py"]], []
    return [], []


def minimum_source_checks(repo: Path) -> list[dict]:
    if not ((repo / "src/mentat/proof_checker.py").is_file() and (repo / "scripts/verify").is_file()):
        return []
    return [{
        "path": "examples/source-proof/clamp.py", "function": "clamp",
        "inputs": {"value": "int", "lower": "int", "upper": "int"},
        "requires": "lower <= upper",
        "ensures": "lower <= result and result <= upper and ((lower <= value and value <= upper) == (result == value)) and (value >= lower or result == lower) and (value <= upper or result == upper)",
        "claim_id": "53944563-954b-5d78-a51b-7401f3eca452",
        "requirement_id": "2bcfa241-162e-5b32-8b37-79deae3acd37",
        "counterexample_cases": [{"path": "examples/source-proof/clamp_faulty.py", "function": "clamp"}],
    }]


def pin_policy(repo: Path, state_root: Path, config: dict, *, merge_policy: str = "github-protected") -> dict:
    from .autonomy import state_path, validate_source_checks
    from .autonomy_runtime import _gh, git
    from .autonomy_worker import profile_identity

    if merge_policy not in {"github-protected", "controller-private"}:
        raise ValueError("unsupported merge policy")
    validate_source_checks(config["source_checks"])
    path = state_path(repo, state_root).parent / "policy.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        policy = load_policy_path(path.with_name("state.db"))
        check_policy(config, policy)
        current = merge_authority(policy)
        if (current["mode"] if current else "github-protected") != merge_policy:
            raise ValueError("merge authority changed; use a new --state and session")
        return policy
    local, worker = minimum_gates(repo)
    controller_repo = Path(__file__).resolve().parents[2]
    worker_profile = profile_identity(controller_repo)
    worker_profile["source_revision"] = git(controller_repo, "rev-parse", "HEAD")
    policy = {
        "schema_version": 2, "repo": str(repo.resolve()),
        "mandatory_local_checks": local,
        "mandatory_worker_checks": worker,
        "mandatory_source_checks": copy.deepcopy(config["source_checks"]),
        "model": "gpt-6.1-terra", "reasoning_effort": "high",
        "auto_merge": bool(local or worker) and config["auto_merge"],
        "session_seconds": 7200, "concurrency": 3, "max_attempts": 3,
        "trust": "local controller, local records and authenticated GitHub account",
        "worker_profile": worker_profile,
    }
    if merge_policy == "controller-private":
        name = json.loads(_gh(repo, "repo", "view", "--json", "nameWithOwner"))["nameWithOwner"]
        def api(route: str) -> object:
            return json.loads(_gh(repo, "api", "--hostname", "github.com", route))
        policy["merge_policy"] = autonomy_private_merge.pin_repository(repo, name, git, api)
        policy["schema_version"] = 3
    minimum = minimum_source_checks(repo)
    for item in minimum:
        required_cases = item["counterexample_cases"]
        if not any(
            {k: v for k, v in check.items() if k not in {"counterexample_cases", "timeout_ms"}}
            == {k: v for k, v in item.items() if k not in {"counterexample_cases", "timeout_ms"}}
            and all(case in check.get("counterexample_cases", []) for case in required_cases)
            for check in policy["mandatory_source_checks"]
        ):
            raise ValueError("repository configuration removes a required source check")
    check_policy(config, policy)
    serialized = json.dumps(policy, indent=2, allow_nan=False) + "\n"
    if len(serialized.encode()) > 65536:
        raise ValueError("local policy exceeds 64 KiB")
    # Publish the complete bounded policy atomically and never replace an existing one.
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".policy-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(serialized)
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
    return policy


def load_policy_path(store_path: Path) -> dict:
    path = Path(store_path).parent / "policy.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("initialize a trusted local workspace policy before autonomous work")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema_version") not in (1, 2, 3):
        raise ValueError("unsupported local policy")
    _commands(value.get("mandatory_local_checks"))
    _commands(value.get("mandatory_worker_checks"))
    merge_authority(value)
    if value["schema_version"] in (2, 3):
        from .autonomy import validate_source_checks
        validate_source_checks(value.get("mandatory_source_checks"))
    return copy.deepcopy(value)


def merge_authority(policy: dict) -> dict | None:
    version = policy.get("schema_version")
    if version in (1, 2):
        if "merge_policy" in policy:
            raise ValueError("legacy policy cannot grant private merge authority")
        return None
    if version == 3:
        value = autonomy_private_merge.validate_policy(policy.get("merge_policy"))
        if value is not None:
            return value
    raise ValueError("unsupported local merge authority")


def check_policy(config: dict, policy: dict) -> None:
    merge_authority(policy)
    version = policy.get("schema_version")
    if version == 1 and any(check.get("counterexample_cases") for check in config["source_checks"]):
        raise ValueError("counterexample cases require a new --state, policy and session")
    if version in (2, 3):
        descriptors = [json.dumps(check, sort_keys=True, separators=(",", ":"), allow_nan=False)
                       for check in config["source_checks"]]
        if len(descriptors) != len(set(descriptors)):
            raise ValueError("duplicate source check")
        required = policy["mandatory_source_checks"]
        proposed = iter(config["source_checks"])
        if any(not any(item == candidate for candidate in proposed) for item in required):
            raise ValueError("repository configuration removes or reorders a required source check")
    for field in ("model", "reasoning_effort"):
        if config[field] != policy[field]:
            raise ValueError("repository configuration expands pinned agent authority")
    for field in ("session_seconds", "concurrency", "max_attempts"):
        if config[field] > policy[field]:
            raise ValueError("repository configuration expands pinned resource limits")
    for key, source in (("mandatory_local_checks", "local_checks"), ("mandatory_worker_checks", "worker_checks")):
        required = _commands(policy[key])
        proposed = _commands(config[source])
        if any(argv not in proposed for argv in required):
            raise ValueError("repository configuration removes a required local gate")
    if config["auto_merge"] and not policy["auto_merge"]:
        raise ValueError("automatic merge is not authorized by the local policy")
