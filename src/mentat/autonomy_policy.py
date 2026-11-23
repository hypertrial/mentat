"""Locally pinned minimum gates; repository proposals cannot weaken authority.

This is an ordinary trusted local file, not a signed attestation or identity system.
"""

import json
import os
from pathlib import Path


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


def pin_policy(repo: Path, state_root: Path, config: dict) -> dict:
    from .autonomy import state_path
    from .autonomy_runtime import git
    from .autonomy_worker import profile_identity

    path = state_path(repo, state_root).parent / "policy.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        policy = load_policy_path(path.with_name("state.db"))
        check_policy(config, policy)
        return policy
    local, worker = minimum_gates(repo)
    controller_repo = Path(__file__).resolve().parents[2]
    worker_profile = profile_identity(controller_repo)
    worker_profile["source_revision"] = git(controller_repo, "rev-parse", "HEAD")
    policy = {
        "schema_version": 1, "repo": str(repo.resolve()),
        "mandatory_local_checks": local,
        "mandatory_worker_checks": worker,
        "model": "gpt-6.1-terra", "reasoning_effort": "high",
        "auto_merge": bool(local or worker) and config["auto_merge"],
        "session_seconds": 7200, "concurrency": 3, "max_attempts": 3,
        "trust": "local controller, local records and authenticated GitHub account",
        "worker_profile": worker_profile,
    }
    check_policy(config, policy)
    # An initial policy is created only by the explicit initialization command.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(policy, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return policy


def load_policy_path(store_path: Path) -> dict:
    path = Path(store_path).parent / "policy.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("initialize a trusted local workspace policy before autonomous work")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported local policy")
    _commands(value.get("mandatory_local_checks"))
    _commands(value.get("mandatory_worker_checks"))
    return value


def check_policy(config: dict, policy: dict) -> None:
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
