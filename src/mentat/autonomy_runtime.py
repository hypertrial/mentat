"""Bounded runtime adapters for autonomous work.

Adapter failures are returned as incomplete evidence, never as successful verification.
"""

import base64
import hashlib
import io
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import (
    autonomy_git_transaction,
    autonomy_private_merge,
    autonomy_spec_scope,
    autonomy_worker,
    gitio,
    process,
    project,
)
from .autonomy_state import record_digest


def _execution_sources() -> tuple[bytes, ...]:
    paths = (
        Path(__file__),
        Path(autonomy_worker.__file__),
        Path(autonomy_private_merge.__file__),
        Path(autonomy_git_transaction.__file__),
        Path(autonomy_spec_scope.__file__),
        *(Path(__file__).with_name(name) for name in (
            "autonomy_state.py", "autonomy_cli.py", "autonomy_projection.py",
            "autonomy_integration.py", "autonomy.py", "autonomy_final_review.py",
            "autonomy_source_corrections.py", "autonomy_review_inputs.py",
        )),
    )
    return tuple(path.read_bytes() for path in paths)


# Refuse to attest code that changed after this process imported its implementation.
_LOADED_EXECUTION_SOURCES = _execution_sources()


def _execution_digest(parts: list[bytes]) -> str:
    digest = hashlib.sha256(b"mentat-execution-identity-v1\0")
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def execution_identity() -> str:
    """Fingerprint the executing worker implementation and its trusted assets."""
    sources = _execution_sources()
    if sources != _LOADED_EXECUTION_SOURCES:
        raise ValueError("trusted execution code changed after import; restart required")
    assets = autonomy_worker.worker_assets()
    return _execution_digest([*sources, *(data for _, _, data in assets)])

_OUTPUT_LIMIT = 1_000_000
_MODEL = "gpt-6.1-terra"
_EMBEDDED_CHECKER_INSTALLER = 'eNq1V2Fv2zYQ/e5fcXHT2h7ASLJkS07qYl2RtQG6FMjSYUCaGRRJxVpkSRUpJ1nS/74jJSt2rGTZisGAJdN3x3fv7p6oFztWKQsrjFNLpEsIqZx3pFBARJlBHucionHS6eRFlkUzFs/wJooTMY1oIkUnjqDfh90XMBgcgJqLtANwdqYXplNw4NUr2HX0LSEsJrUrnJ/D3R3cgmDzDHqfJb0Q+xCnUtEkIWYjwuaCXYoCztYdz3vw5tXwAMR1rACv3+rNbt8fnX74/NPs7bvTo0/Hv+6Tb3pLVZTCALg9+Xx8fHgyOzz+7ejk0/Evh8entclFrOZlSOaZVIJv2H5aRfkYp+X1CjHuBzXqjYRiCYWQolhilCgrNBHAY5lnkob49/tYfbjfpor4ZxZKk04V02SEtzqlLap1Jp0o7uhc+2VKFwKIHGyhq5Gd4uboqQTTu62ILMTXMkaQlcsWkYxKcR98McByIJLrYDwbewMDsUJFCzaPl2KaCJoSb88d7dkk0RH3FC32/pJqzVbO6dQfBpPRxKeeN/LcoTdmk+HQDwNGxch2/LEv7MmICcePxtzzgnAyiphHA84m/nAc4KIJd3CAF6q3fiaYWW38CCgWRHYUUHcytEXgTnwnCB3Xc8XYD5yIBzZ1xSR07KEXhYz7th+I0LWjkScCexzaw+Ae1A+DVROnsszzrDAFRjhQwakLZIDqgpSF2KBeRxGSsnq8ZMmzaX+gp8rUOuZAygHsTMHGIlcDBuum+htIirOH7VF7PWyfZhZWERA3yxYLmmL0JdA8p8UiK2Z4wRaGNxYXSystk2S9qd7m+VttBbWV6XjTUVXHvzsCFI0yb5nQCk+JXqmchQKtxXS3L28kUwliB+zOVCR7DQ5sUlXETM3KNC/iJWZwIXjtP6gnviWmTtPWkB/700EC9BR1d2/vKTz78fxbFzlQ5AI1r8w5VeIpi1qlgNxAWIY43FcFzYGVRQLYZ7yuARFgZbmy1hpznf2K0hMRlTJOL0BlyGWeUCYAGwepk0ovP4xwAEuaxBogxqENFKriLEXykT38K7lZiUotKZhwlYta5Ej74lKJRQ6EDzpKI+8VCyBFhPoM3d3GsNuDw9+PTjsmL0L0EwAvScaqzYgWPkTdm86VylHGCFGJXDp7Q7wrhCpuwIUviKFr/t+3rEpo97DtTEbovhSFufWsQuBVCmnx7CpNMsqtZZWwtbsx5l0TkpCsVHmp1uE+NMSM41RF0HspAV7KL2mvscbx7z7lCneAJsPRWJYL/dDS6gmkgzqCv3SF8SKukTqmNDF6vJ6KRgjHEWEqQ0rW+W1rscUl2gLJTeFbDZabe91ubPZyJXZo+bB12qJhD15hKU8A19W+/nqm2yLj2u0iI1fbHg8XzLlC/0YqsOQS2+ff6JSWlsquGYs4BUsoZq30Yo9boRnC1lVSyQBZiYrpIdiy1a2dPOLRbluJ08y4HADPTFhMa0cP/+4DzHiywPWPW+vrx4qnZKFxWNOcPEtidgNIkAEEeqKKmAu5KcEmtpZhnqWiUeOacomK0FZjVBcLBdqqpNmSAoUgVjcNC9bKX+sxlvMCgQI5/Aq9P/qGkbt1eu6aA6b5OeifgXV+tzvowevXr1cdvYrYbWr/FCFaJvDJs05H7b+Rve6g+GHS07UZaq24fj5+p/AB9NakL6GV9DX0NTfEKuiV5Uz8CY1837G5HbkBD8fjwA65z5wJC+yJPYwcJ5qMJw3t24WwjCjJ9oR6NaYt7ay9uw8pMqeksetxl7ve0Gde4EbO0GXhSPCRG/qOoI5re7YT4AHO55No7DPqe97Q4d6Qjh3fjuyq2R7T4bWNultwHhFhfJ60PZg3jy+EyMs4J1XnEt0nqyVGMVBb6i1Rm8d8ZuQRyEV9XYA99rxtxM8Sn+cz8jwt+y6aVqP0fOT/g3Ds1NLx8zVqRyXjfZGi6jPxiDxowdzwWleaf3Le0hY8trZoCJ7XBaRY7jBTc6hD8lZlMcf0/3iW1Q+8bttptdvyvvk+yUJUef1+JnNdt1VorUtsTlMMDBzZRqFsOYnXr5YtNSvKtDQdUWLKYcZv9FlwU7FxpUyx1QpBKtPmZypUoy1FRtCDg4UfI5QMLPONhyCxBMt86Zir19m/ATmhbho='
_ARRAYS = ("requirements", "claims", "findings", "verification_requests", "defects", "changes")
_ITEM_FIELDS: dict[str, dict[str, dict[str, Any]]] = {
    "requirements": {"id": {"type": "string"}, "summary": {"type": "string"}},
    "claims": {"id": {"type": "string"}, "summary": {"type": "string"}},
    "findings": {"id": {"type": "string"}, "summary": {"type": "string"}, "requirement_ids": {"type": "array", "items": {"type": "string"}}, "severity": {"type": "string"}},
    "verification_requests": {"argv": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}, "claim_ids": {"type": "array", "items": {"type": "string"}}, "method": {"type": "string"}},
    "defects": {"id": {"type": "string"}, "requirement_id": {"type": "string"}, "evidence_id": {"type": "string"}, "summary": {"type": "string"}},
    "changes": {"path": {"type": "string"}, "operation": {"type": "string", "enum": ["write", "delete", "patch_fas"]}, "content": {"type": "string"}},
}
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "decision", "summary", *_ARRAYS],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "decision": {"type": "string", "enum": ["accept", "revise", "defer"]},
        "summary": {"type": "string", "minLength": 1},
        **{key: {"type": "array", "items": {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}} for key, fields in _ITEM_FIELDS.items()},
    },
}


def _run(argv: list[str], *, cwd: Path | None = None, timeout: int = 30, env: dict[str, str] | None = None) -> str:
    code, out, err = process.run(argv, output_limit=_OUTPUT_LIMIT, timeout=timeout, cwd=cwd, env=env)
    if code:
        raise RuntimeError(f"{argv[0]} failed ({code}): {err.decode('utf-8', 'replace')[:2000]}")
    return out.decode("utf-8", "strict")


def git(repo: Path, *args: str) -> str:
    """Execute bounded Git with caller Git environment and repository hooks disabled."""
    env = gitio.git_env()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    env["GIT_ALLOW_PROTOCOL"] = "ssh"
    # A repository-local clean/smudge or process filter executes host code during
    # checkout and may also run while Git inspects the working tree.
    code, out, _ = process.run(["git", "--no-replace-objects", "-C", str(repo), "config", "--local", "--includes", "--name-only", "--get-regexp", r"^filter\."], output_limit=65536, timeout=15, env=env)
    if code == 0 and out.strip():
        raise ValueError("repository-local Git filters are not allowed for autonomous work")
    if code not in (0, 1):
        raise ValueError("cannot verify repository-local Git filters")
    return _run(["git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args], timeout=60, env=env).strip()


def identity(repo: Path) -> dict:
    root = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    return {"repo": str(root), "head": git(root, "rev-parse", "--verify", "HEAD^{commit}"),
            "base": git(root, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"),
            "tree": git(root, "rev-parse", "--verify", "HEAD^{tree}"),
            "dirty": bool(git(root, "status", "--porcelain=v1", "--untracked-files=all"))}


def _gh(repo: Path, *args: str) -> str:
    return _run(["gh", *args], cwd=repo, timeout=60)


def gh(repo: Path, *args: str) -> str:
    """Run a bounded GitHub CLI command for other autonomy modules."""
    return _gh(repo, *args)


def diagnostics(repo: Path) -> dict:
    result: dict[str, Any] = {"codex": {"available": bool(shutil.which("codex")), "model": _MODEL, "reasoning_effort": "high"},
                              "gh": {"available": bool(shutil.which("gh"))},
                              "limactl": {"available": bool(shutil.which("limactl"))}}
    if result["codex"]["available"]:
        try:
            result["codex"]["version"] = _run(["codex", "--version"], timeout=10).strip()
            result["codex"]["model_verified"] = False
        except (OSError, RuntimeError, TimeoutError) as exc:
            result["codex"]["error"] = str(exc)
    if result["gh"]["available"]:
        try:
            _gh(repo, "auth", "status")
            name = json.loads(_gh(repo, "repo", "view", "--json", "nameWithOwner"))["nameWithOwner"]
            protection = json.loads(_gh(repo, "api", f"repos/{name}/branches/main/protection"))
            result["gh"].update({"authenticated": True, "repo": name, "protection_visible": True,
                                  "strict": protection.get("required_status_checks", {}).get("strict") is True,
                                  "enforce_admins": protection.get("enforce_admins", {}).get("enabled") is True})
        except (OSError, RuntimeError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            result["gh"].update({"protection_visible": False, "error": str(exc)})
    return result


def _validate_result(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != set(_SCHEMA["required"]):
        raise ValueError("invalid structured agent response fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid agent schema version")
    if value["decision"] not in ("accept", "revise", "defer") or not isinstance(value["summary"], str) or not value["summary"].strip():
        raise ValueError("invalid agent decision or summary")
    for key in _ARRAYS:
        if not isinstance(value[key], list):
            raise ValueError(f"invalid agent {key}")
        for item in value[key]:
            fields = _ITEM_FIELDS[key]
            if not isinstance(item, dict) or set(item) != set(fields):
                raise ValueError(f"invalid agent {key} fields")
            for field, spec in fields.items():
                part = item[field]
                if spec["type"] == "string" and not isinstance(part, str):
                    raise ValueError(f"invalid agent {key} field {field}")
                if spec["type"] == "array" and (not isinstance(part, list) or not all(isinstance(x, str) for x in part)):
                    raise ValueError(f"invalid agent {key} field {field}")
    for request in value["verification_requests"]:
        if not request["argv"] or any(not arg or "\0" in arg for arg in request["argv"]):
            raise ValueError("invalid agent verification_requests field argv")
    changes = value["changes"]
    if len(changes) > 128 or sum(len(item["content"].encode("utf-8")) for item in changes) > 300_000:
        raise ValueError("agent changes exceed limits")
    if any(item["operation"] not in {"write", "delete", "patch_fas"} or (item["operation"] == "delete" and item["content"]) for item in changes):
        raise ValueError("invalid file change operation")
    return value


def _strict_json(data: str | bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid JSON") from exc


def _fas_key(collection: str, record: Any) -> str:
    if not isinstance(record, dict):
        raise ValueError("invalid FAS record")
    key = record.get("path" if collection == "surfaces" else "id")
    if not isinstance(key, str):
        raise ValueError("invalid FAS record key")
    if collection == "surfaces":
        project.safe_relative_path(key)
    else:
        try:
            uuid.UUID(key)
        except ValueError as exc:
            raise ValueError("invalid FAS record UUID") from exc
    return key


def _patch_fas(raw: bytes, content: str) -> str:
    if len(raw) > 1_000_000:
        raise ValueError("FAS base exceeds size limit")
    base = _strict_json(raw)
    patch = _strict_json(content)
    collections = ("requirements", "claims", "surfaces")
    if (not isinstance(base, dict) or set(base) != {"schema_version", *collections}
            or type(base["schema_version"]) is not int or base["schema_version"] != 1):
        raise ValueError("invalid FAS base")
    indexes: dict[str, dict[str, int]] = {}
    ids: set[str] = set()
    for collection in collections:
        records = base[collection]
        if not isinstance(records, list) or len(records) > 4096:
            raise ValueError("invalid FAS base inventory")
        indexes[collection] = {}
        for i, record in enumerate(records):
            key = _fas_key(collection, record)
            if key in indexes[collection] or (collection != "surfaces" and key in ids):
                raise ValueError("duplicate FAS base record")
            indexes[collection][key] = i
            if collection != "surfaces":
                ids.add(key)
    if (not isinstance(patch, dict) or set(patch) != {"schema_version", "expected_sha256", "edits"}
            or type(patch["schema_version"]) is not int or patch["schema_version"] != 1
            or not isinstance(patch["expected_sha256"], str)
            or patch["expected_sha256"] != hashlib.sha256(raw).hexdigest()
            or not isinstance(patch["edits"], list) or not 1 <= len(patch["edits"]) <= 128):
        raise ValueError("invalid or stale FAS patch")
    seen: set[tuple[str, str]] = set()
    for edit in patch["edits"]:
        if not isinstance(edit, dict) or set(edit) != {"collection", "key", "expected_digest", "record"}:
            raise ValueError("invalid FAS edit")
        collection = edit["collection"]
        key = edit["key"]
        if (not isinstance(collection, str) or collection not in collections
                or not isinstance(key, str) or _fas_key(collection, edit["record"]) != key
                or (collection, key) in seen):
            raise ValueError("invalid or duplicate FAS edit")
        seen.add((collection, key))
        index = indexes[collection].get(key)
        expected = edit["expected_digest"]
        if index is None:
            if expected is not None or (collection != "surfaces" and key in ids):
                raise ValueError("FAS insertion conflicts with existing record")
            base[collection].append(edit["record"])
            if collection != "surfaces":
                ids.add(key)
        else:
            if not isinstance(expected, str) or expected != record_digest(base[collection][index]):
                raise ValueError("stale FAS record")
            base[collection][index] = edit["record"]
    try:
        return json.dumps(base, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid FAS result") from exc


def apply_changes(repo: Path, changes: list[dict], *, kind: str, protected_paths: set[str] | None = None) -> list[str]:
    """Apply bounded model-proposed text files within an already pinned worktree.

    The caller must check the worktree identity, HEAD, and active session before
    invoking this function, and pass its immutable verification paths.
    """
    if not isinstance(changes, list) or len(changes) > 128:
        raise ValueError("invalid file changes")
    if kind not in {"generate_spec", "generate_verification", "investigate_defect"}:
        raise ValueError("job cannot modify files")
    protected = {p.casefold() for p in (protected_paths or set())}
    paths: set[str] = set()
    total = 0
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"path", "operation", "content"} or not all(isinstance(change[k], str) for k in change):
            raise ValueError("invalid file change")
        path = project.safe_relative_path(change["path"])
        lower = path.casefold()
        parts = lower.split("/")
        if any(part in {".git", ".agents", ".codex"} for part in parts):
            raise ValueError("prohibited file path")
        if lower in paths or any(lower.startswith(p + "/") or p.startswith(lower + "/") for p in paths):
            raise ValueError("duplicate or conflicting file paths")
        paths.add(lower)
        if kind == "generate_spec":
            allowed = lower == ".mentat/fas.json" or lower.startswith("openspec/specs/")
        elif kind == "generate_verification":
            allowed = lower.startswith((".mentat/verification/", "verification/"))
        else:
            allowed = not (lower.startswith(("openspec/", ".mentat/", "verification/", "scripts/", ".github/")) or lower in {"agents.md", "contributing.md", "pyproject.toml", "uv.lock", "makefile", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "tox.ini", "pytest.ini", "setup.cfg", "setup.py", "verify.py"} or parts[-1].startswith(("requirements", "conftest")))
        if not allowed or lower in protected or any(lower.startswith(p + "/") for p in protected):
            raise ValueError("file outside permitted job scope")
        if (change["operation"] not in {"write", "delete", "patch_fas"}
                or (change["operation"] == "delete" and change["content"])
                or (change["operation"] == "patch_fas" and (kind != "generate_spec" or path != ".mentat/fas.json"))):
            raise ValueError("invalid file change operation")
        total += len(change["content"].encode("utf-8"))
    if total > 300_000:
        raise ValueError("file changes exceed size limit")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(repo, flags)
    def parent_fd(parts: list[str], *, create: bool) -> int | None:
        fd = os.dup(root_fd)
        try:
            for part in parts:
                try:
                    child = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        os.close(fd)
                        return None
                    os.mkdir(part, 0o755, dir_fd=fd)
                    child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except BaseException:
            os.close(fd)
            raise
    try:
        # Validate all existing targets and materialize patches before modifying anything.
        materialized = [dict(change) for change in changes]
        for change in materialized:
            parts = change["path"].split("/")
            fd = parent_fd(parts[:-1], create=False)
            if fd is None:
                if change["operation"] in {"delete", "patch_fas"}:
                    raise ValueError("cannot modify missing file")
                continue
            try:
                try:
                    info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    if change["operation"] in {"delete", "patch_fas"}:
                        raise ValueError("cannot modify missing file") from None
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("file change target must be regular")
                if change["operation"] == "patch_fas":
                    file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                    with os.fdopen(file_fd, "rb") as source:
                        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                            raise ValueError("file change target must be regular")
                        raw = source.read(1_000_001)
                    change["content"] = _patch_fas(raw, change["content"])
                    change["operation"] = "write"
            finally:
                os.close(fd)
        if sum(len(change["content"].encode("utf-8")) for change in materialized) > 300_000:
            raise ValueError("materialized file changes exceed size limit")
        for change in materialized:
            parts = change["path"].split("/")
            fd = parent_fd(parts[:-1], create=change["operation"] == "write")
            if fd is None:
                raise ValueError("parent directory disappeared")
            try:
                if change["operation"] == "delete":
                    info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("file change target must be regular")
                    os.unlink(parts[-1], dir_fd=fd)
                else:
                    try:
                        existing = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                        if not stat.S_ISREG(existing.st_mode):
                            raise ValueError("file change target must be regular")
                        mode = stat.S_IMODE(existing.st_mode) & 0o755
                    except FileNotFoundError:
                        mode = 0o644
                    temp = ".mentat-edit-" + uuid.uuid4().hex
                    temp_fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
                    try:
                        with os.fdopen(temp_fd, "w", encoding="utf-8") as output:
                            output.write(change["content"])
                        os.replace(temp, parts[-1], src_dir_fd=fd, dst_dir_fd=fd)
                    finally:
                        try:
                            os.unlink(temp, dir_fd=fd)
                        except FileNotFoundError:
                            pass
            finally:
                os.close(fd)
    finally:
        os.close(root_fd)
    return [change["path"] for change in changes]


class _CommandFailed(RuntimeError):
    def __init__(self, message: str, code: int, stdout: str, stderr: str) -> None:
        super().__init__(message)
        self.code = code
        self.stdout = stdout
        self.stderr = stderr


class _Cancellable:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: set[subprocess.Popen] = set()
        self._cancelled = False

    def cancel_all(self) -> None:
        with self._lock:
            self._cancelled = True
            for child in self._children:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def _execute(self, argv: list[str], *, cwd: Path | None = None, timeout: int, stdin: Any = None) -> str:
        with self._lock:
            if self._cancelled:
                raise RuntimeError("operation cancelled")
            child = subprocess.Popen(argv, cwd=cwd, stdin=stdin or subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            self._children.add(child)
        output: dict[Any, bytearray] = {child.stdout: bytearray(), child.stderr: bytearray()}
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                for stream in output:
                    selector.register(stream, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("execution timed out")
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            data = output[key.fileobj]
                            data.extend(chunk)
                            if len(data) > _OUTPUT_LIMIT:
                                raise RuntimeError("execution output exceeded limit")
            code = child.wait(timeout=max(0.01, deadline - time.monotonic()))
            if code:
                stderr = output[child.stderr].decode("utf-8", "replace")[-2000:]
                details = []
                for line in output[child.stdout].decode("utf-8", "replace").splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("type") in {"error", "turn.failed"}:
                        details.append(str(event.get("message") or event.get("error") or "agent error"))
                detail = (stderr + " " + " ".join(details))[-3000:]
                detail = re.sub(r"(?i)(api[_-]?key|authorization|token|password)([\s:=]+)[^\s,}]+", r"\1\2[redacted]", detail)
                raise _CommandFailed(f"{argv[0]} failed ({code}): {detail}", code, output[child.stdout].decode("utf-8", "replace"), output[child.stderr].decode("utf-8", "replace"))
            return output[child.stdout].decode("utf-8", "strict")
        finally:
            with self._lock:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
                self._children.discard(child)
            for stream in output:
                stream.close()


def _retain_rejected_result(output_dir: Path, answer: str) -> None:
    """Privately retain bounded reviewer output rejected for proposing changes."""
    data = answer.encode("utf-8")
    if len(data) > _OUTPUT_LIMIT:
        raise ValueError("agent response exceeds size limit")
    name = f"codex-rejected-result-{hashlib.sha256(data).hexdigest()}.json"
    directory = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("unsafe agent output directory")
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        except FileExistsError:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != len(data)):
                    raise ValueError("unsafe rejected result artifact")
                stored = bytearray()
                while len(stored) <= len(data):
                    chunk = os.read(fd, min(65536, len(data) + 1 - len(stored)))
                    if not chunk:
                        break
                    stored.extend(chunk)
                if stored != data:
                    raise ValueError("rejected result artifact identity conflict")
            finally:
                os.close(fd)
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
    finally:
        os.close(directory)


class CodexAgent(_Cancellable):
    def run(self, repo: Path, job: dict, output_dir: Path, *, timeout_seconds: int) -> dict:
        payload = job.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip() or payload.get("purpose") not in {"generator", "discriminator"}:
            raise ValueError("job requires prompt and generator or discriminator purpose")
        if type(timeout_seconds) is not int or timeout_seconds <= 0 or timeout_seconds > 7200:
            raise ValueError("invalid timeout")
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise ValueError("agent output directory must be a real directory")
        os.chmod(output_dir, 0o700)
        with tempfile.TemporaryDirectory(prefix="agent-", dir=output_dir) as directory:
            schema_path = Path(directory) / "schema.json"
            answer_path = Path(directory) / "answer.json"
            schema_path.write_text(json.dumps(_SCHEMA), encoding="utf-8")
            # Model-proposed edits are data; the controller applies them after validation.
            sandbox = "read-only"
            argv = ["codex", "--ask-for-approval", "never", "exec", "--ignore-user-config", *[flag for feature in ("shell_tool", "unified_exec", "apps", "browser_use", "computer_use", "multi_agent", "code_mode", "code_mode_host") for flag in ("--disable", feature)], "-c", "mcp_servers={}", "-c", 'web_search="disabled"', "--model", _MODEL, "-c", 'model_reasoning_effort="high"', "--sandbox", sandbox, "--json", "--output-schema", str(schema_path), "--output-last-message", str(answer_path), payload["prompt"]]
            try:
                help_text = _run(["codex", "exec", "--help"], timeout=10)
                features_text = _run(["codex", "features", "list"], timeout=10)
                if "--ignore-user-config" not in help_text or "--disable" not in help_text or any(not re.search(r"^" + feature + r"\s", features_text, re.M) for feature in ("shell_tool", "unified_exec", "apps", "browser_use", "computer_use", "multi_agent", "code_mode", "code_mode_host")):
                    raise RuntimeError("Codex execution isolation controls unavailable")
                output = self._execute(argv, cwd=repo, timeout=timeout_seconds)
                if answer_path.stat().st_size > _OUTPUT_LIMIT:
                    raise ValueError("agent response exceeds size limit")
                answer = answer_path.read_text(encoding="utf-8")
                result = _validate_result(json.loads(answer))
                if result["changes"] and (payload["purpose"] != "generator" or result["decision"] == "defer"):
                    if payload["purpose"] == "discriminator":
                        _retain_rejected_result(output_dir, answer)
                    raise ValueError("reviewer or deferred response cannot modify files")
                events = [json.loads(line) for line in output.splitlines() if line.strip()]
                if not all(isinstance(event, dict) for event in events):
                    raise ValueError("invalid agent event")
                if any(event.get("item", {}).get("type") == "command_execution" for event in events if isinstance(event.get("item"), dict)):
                    raise ValueError("agent attempted command execution")
                session_id = next((event.get("thread_id") for event in events if event.get("type") == "thread.started"), None)
                for name, data in (("codex-events.jsonl", output), ("codex-result.json", answer)):
                    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
                    fd = os.open(output_dir / name, flags, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        stream.write(data)
                event_summary = [{key: event[key] for key in ("type", "thread_id", "usage") if key in event} for event in events if event.get("type") in {"thread.started", "turn.started", "turn.completed", "turn.failed", "error"}]
                return {"status": "succeeded", "result": result, "events": event_summary, "session_id": session_id, "model": _MODEL}
            except (OSError, RuntimeError, TimeoutError, ValueError, TypeError) as exc:
                return {"status": "failed", "error": str(exc), "events": [], "session_id": None, "model": _MODEL}


# Both guest services require a completion record from their trusted wrapper.
_SERVICE_COMPLETION_HELPERS = r"""
import json, os, sys
COMPLETION_WRAPPER = "import json,subprocess,sys; r=subprocess.run(sys.argv[3:]); f=open(sys.argv[1], 'x'); json.dump({'unit':sys.argv[2], 'returncode':r.returncode}, f); f.close(); sys.exit(r.returncode if r.returncode >= 0 else 125)"
def completion_status(marker, unit, service_returncode):
    try:
        with open(marker, "r", encoding="ascii") as status:
            payload = status.read(1025)
        assert len(payload) <= 1024
        completion = json.loads(payload)
        assert isinstance(completion, dict) and set(completion) == {"unit", "returncode"}
        assert completion["unit"] == unit and type(completion["returncode"]) is int
        command_returncode = completion["returncode"]
    except (OSError, ValueError, AssertionError):
        print("MENTAT_INCOMPLETE:"+unit+":command did not complete", file=sys.stderr, flush=True)
        return 125
    if command_returncode < 0 or command_returncode > 255 or service_returncode != command_returncode:
        print("MENTAT_INCOMPLETE:"+unit+":service status mismatch", file=sys.stderr, flush=True)
        return 125
    print("MENTAT_FINISHED:"+unit+":"+str(command_returncode), file=sys.stderr, flush=True)
    return command_returncode
"""

# This script is supplied by the trusted controller, never by a candidate checkout.
_GUEST_RUNNER = _SERVICE_COMPLETION_HELPERS + r"""
import base64, json, os, shutil, subprocess, sys, tarfile, tempfile
args = json.loads(base64.b64decode(sys.argv[1])); seconds = int(sys.argv[2]); unit = sys.argv[3]
folder = tempfile.mkdtemp(prefix="mentat-job-", dir="/tmp")
try:
    os.chmod(folder, 0o755)
    source_file = os.path.join(folder, "source.tar")
    bundle_file = os.path.join(folder, "source.bundle")
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as envelope:
        for member in envelope:
            assert member.name in ("source.tar", "source.bundle") and member.isfile()
            target = source_file if member.name == "source.tar" else bundle_file
            assert not os.path.exists(target)
            with open(target, "xb") as out:
                shutil.copyfileobj(envelope.extractfile(member), out)
    work = os.path.join(folder, "work")
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", "clone", "--no-checkout", "--", bundle_file, work], env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    head = subprocess.check_output(["git", "-C", work, "rev-parse", "HEAD"], env=env).decode().strip()
    assert head == sys.argv[4]
    with tarfile.open(source_file, mode="r") as archive:
        for member in archive:
            assert member.name != ".git" and not member.name.startswith(".git/") and member.name != ".venv" and not member.name.startswith(".venv/")
        archive.extractall(work, filter="data")
    version_file = os.path.join(work, ".python-version")
    if os.path.lexists(version_file):
        assert os.path.isfile(version_file) and not os.path.islink(version_file)
        with open(version_file, "rb") as marker:
            declared = marker.read(32)
        assert declared in (b"3.12", b"3.12\n", b"3.12\r\n", b"3.14", b"3.14\n", b"3.14\r\n")
        declared = declared.rstrip(b"\r\n")
    else:
        declared = b"3.12"
    use_py314 = declared == b"3.14"
    venv = "/opt/mentat-tools/.venv-3.14" if use_py314 else "/opt/mentat-tools/.venv"
    if use_py314:
        modules = os.path.join(work, "node_modules")
        assert not os.path.lexists(modules)
        os.mkdir(modules)
    os.mkdir(os.path.join(work, ".venv"))
    cache = os.path.join(folder, "uv-cache")
    assert not os.path.lexists(cache)
    shutil.copytree("/opt/mentat-tools/cache", cache, symlinks=True)
    for tree in (work, cache):
        for root, dirs, files in os.walk(tree, followlinks=False):
            os.chown(root, 65534, 65534, follow_symlinks=False)
            for name in files + dirs:
                os.chown(os.path.join(root, name), 65534, 65534, follow_symlinks=False)
    # The wrapper is outside the sandbox and owns this marker directory; the
    # candidate only sees a separate /tmp. A systemd SIGTERM can otherwise be
    # reported as a clean service exit even though the command never finished.
    marker_dir = os.path.join(folder, "completion")
    os.mkdir(marker_dir, 0o700)
    os.chown(marker_dir, 65534, 65534)
    marker = os.path.join(marker_dir, "status")
    cmd = ["systemd-run", "--unit="+unit, "--quiet", "--wait", "--pipe", "--collect", "--uid=nobody",
           "--property=RuntimeMaxSec="+str(seconds), "--property=MemoryMax=4G",
           "--property=TasksMax=256", "--property=CPUQuota=400%", "--property=LimitFSIZE=256M",
           "--", "/usr/bin/python3", "-I", "-B", "-c", COMPLETION_WRAPPER, marker, unit, "bwrap", "--die-with-parent", "--unshare-all", "--new-session",
           "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
           "--symlink", "usr/lib", "/lib", "--proc", "/proc", "--dev", "/dev",
           "--tmpfs", "/tmp", "--bind", cache, "/tmp/mentat-uv-cache", "--bind", work, "/work", "--ro-bind", "/opt/mentat-tools", "/opt/mentat-tools", "--ro-bind", venv, "/work/.venv", "--chdir", "/work",
           "--clearenv", "--setenv", "HOME", "/tmp", "--setenv", "PATH", "/tmp/mentat-package/bin:/work/.venv/bin:/opt/mentat-tools/bin:/opt/mentat-tools/node/bin:/usr/local/bin:/usr/bin:/bin", "--setenv", "PYTHONPATH", "/work/src:/tmp/mentat-package", "--setenv", "TEST_JOBS", "1", "--setenv", "UV_OFFLINE", "1", "--setenv", "UV_CACHE_DIR", "/tmp/mentat-uv-cache", "--setenv", "PLAYWRIGHT_BROWSERS_PATH", "/opt/mentat-tools/browsers"]
    if use_py314:
        cmd += ["--ro-bind", "/opt/mentat-tools/node-tools/node_modules", "/work/node_modules", "--setenv", "NODE_PATH", "/opt/mentat-tools/node-tools/node_modules"]
    if os.path.isdir("/opt/lean-4.35.0"):
        cmd += ["--ro-bind", "/opt/lean-4.35.0", "/opt/lean-4.35.0"]
    if os.path.isdir("/lib64"):
        cmd += ["--ro-bind", "/lib64", "/lib64"]
    bootstrap = 'if [ -f pyproject.toml ]; then if [ -e /tmp/mentat-package ]; then printf "%s\\n" "$1" >&2; exit 125; fi; uv pip install --target /tmp/mentat-package --no-deps --no-build-isolation --offline /work || { printf "%s\\n" "$1" >&2; exit 125; }; fi; shift; exec "$@"'
    cmd += ["--", "/bin/sh", "-c", bootstrap, "mentat-bootstrap", "MENTAT_BOOTSTRAP_FAILED:"+unit] + args
    result = subprocess.run(cmd, timeout=seconds+20, check=False)
    sys.exit(completion_status(marker, unit, result.returncode))
finally:
    shutil.rmtree(folder, ignore_errors=True)
"""


_CHECKER_GUEST_RUNNER = _SERVICE_COMPLETION_HELPERS + r"""
import os, shutil, subprocess, sys, tarfile, tempfile
unit=sys.argv[1]
folder=tempfile.mkdtemp(prefix='mentat-checker-',dir='/tmp')
try:
    with tarfile.open(fileobj=sys.stdin.buffer,mode='r|*') as archive:
        archive.extractall(folder,filter='data')
    env=['/usr/bin/env','-i','HOME=/tmp','PATH=/opt/mentat-tools/.venv/bin:/opt/mentat-tools/bin:/usr/bin:/bin','PYTHONPATH='+folder+'/src','MENTAT_REQUIRE_PROOF_CHECKER=1','PYTHONDONTWRITEBYTECODE=1']
    marker_dir=os.path.join(folder,'completion')
    os.mkdir(marker_dir,0o700)
    marker=os.path.join(marker_dir,'status')
    cmd=['systemd-run','--unit='+unit,'--quiet','--wait','--pipe','--collect','--property=RuntimeMaxSec='+sys.argv[2], '--property=MemoryMax=4G','--property=TasksMax=256','--property=CPUQuota=400%','--property=LimitFSIZE=256M','--property=WorkingDirectory='+folder,'--','/usr/bin/python3','-I','-B','-c',COMPLETION_WRAPPER,marker,unit]+env+['/opt/mentat-tools/.venv/bin/pytest','-q','-rs','-n','0','tests/test_proof_checker.py']
    result=subprocess.run(cmd,timeout=int(sys.argv[2])+20,check=False)
    sys.exit(completion_status(marker,unit,result.returncode))
finally:
    shutil.rmtree(folder,ignore_errors=True)
"""

_CHECKER_INPUTS = (
    "src/mentat/proof_checker.py", "src/mentat/proof_lean.py", "src/mentat/canonical.py",
    "tests/test_proof_checker.py", "tests/conftest.py",
)


_REPRODUCE = r"""
import ast, base64, hashlib, json, operator, sys
D=json.loads(base64.b64decode(sys.argv[1])); source=base64.b64decode(D['source'])
assert hashlib.sha256(source).hexdigest()==D['sha256']
tree=ast.parse(source.decode()); assert len(list(ast.walk(tree))) <= 1200
matches=[x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name==D['function']]
assert len(matches)==1
fn=matches[0]; a=fn.args; assert not (fn.decorator_list or getattr(fn,'type_params',[]) or a.posonlyargs or a.vararg or a.kwonlyargs or a.kwarg or a.defaults)
types=D['inputs']; witness=D['witness']; names=[x.arg for x in a.args]
assert set(names)==set(types)==set(witness) and len(names)==len(set(names)) and 'result' not in names
for n in names:
    assert types[n] in ('int','bool') and type(witness[n]) is (int if types[n]=='int' else bool)
    if type(witness[n]) is int: assert witness[n].bit_length() <= 256
ops={ast.Add:operator.add,ast.Sub:operator.sub,ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge}
def expression(x,env):
    if isinstance(x,ast.Expression): return expression(x.body,env)
    if isinstance(x,ast.Constant) and type(x.value) in (int,bool,str):
        if type(x.value) is int: assert x.value.bit_length() <= 256
        if type(x.value) is str: assert len(x.value) <= 4096 and all(32 <= ord(c) <= 126 and c != chr(92) for c in x.value)
        return x.value
    if isinstance(x,ast.Name): return env[x.id]
    if isinstance(x,ast.UnaryOp):
        v=expression(x.operand,env)
        if isinstance(x.op,ast.Not): assert type(v) is bool; return not v
        assert type(v) is int
        if isinstance(x.op,ast.USub): return -v
        if isinstance(x.op,ast.UAdd): return v
    if isinstance(x,ast.BinOp) and type(x.op) in (ast.Add,ast.Sub):
        l=expression(x.left,env); r=expression(x.right,env); assert type(l) is int and type(r) is int
        v=ops[type(x.op)](l,r); assert v.bit_length() <= 512; return v
    if isinstance(x,ast.BoolOp) and isinstance(x.op,(ast.And,ast.Or)):
        vals=[expression(y,env) for y in x.values]; assert all(type(v) is bool for v in vals)
        return all(vals) if isinstance(x.op,ast.And) else any(vals)
    if isinstance(x,ast.IfExp):
        test=expression(x.test,env); assert type(test) is bool
        return expression(x.body if test else x.orelse,env)
    if isinstance(x,ast.Compare):
        vals=[expression(y,env) for y in [x.left,*x.comparators]]
        for left,op,right in zip(vals,x.ops,vals[1:]):
            assert type(left) is type(right) and type(op) in ops
            if type(op) not in (ast.Eq,ast.NotEq): assert type(left) is int
            if not ops[type(op)](left,right): return False
        return True
    raise ValueError('unsupported expression')
allowed_expr=(ast.Constant,ast.Name,ast.UnaryOp,ast.BinOp,ast.BoolOp,ast.IfExp,ast.Compare)
allowed_ops=(ast.Add,ast.Sub,ast.Not,ast.USub,ast.UAdd,ast.And,ast.Or,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE)
local_names=set(names)
for stmt in fn.body:
    for node in ast.walk(stmt):
        if isinstance(node,ast.Assign):
            assert len(node.targets)==1 and isinstance(node.targets[0],ast.Name)
            local_names.add(node.targets[0].id)
for stmt in fn.body:
    for node in ast.walk(stmt):
        if isinstance(node,ast.stmt): assert isinstance(node,(ast.Assign,ast.Return,ast.If))
        if isinstance(node,ast.expr): assert isinstance(node,allowed_expr)
        if isinstance(node,ast.Name): assert node.id in local_names
        if isinstance(node,(ast.operator,ast.unaryop,ast.boolop,ast.cmpop)): assert isinstance(node,allowed_ops)
        if isinstance(node,ast.Assign): assert len(node.targets)==1 and isinstance(node.targets[0],ast.Name)
        if isinstance(node,ast.Constant):
            assert type(node.value) in (int,bool,str)
            if type(node.value) is int: assert node.value.bit_length() <= 256
            if type(node.value) is str: assert len(node.value) <= 4096 and all(32 <= ord(c) <= 126 and c != chr(92) for c in node.value)
for arg in a.args: arg.annotation=None
fn.returns=None
isolated=ast.Module(body=[fn],type_ignores=[]); ast.fix_missing_locations(isolated)
namespace={'__builtins__':{}}
exec(compile(isolated,'<verified-function>','exec',dont_inherit=True),namespace)
pre=ast.parse(D['requires'],mode='eval'); post=ast.parse(D['ensures'],mode='eval')
assert len(list(ast.walk(pre)))<=1200 and len(list(ast.walk(post)))<=1200
valid=expression(pre,witness); assert type(valid) is bool and valid
value=namespace[D['function']](**witness); assert type(value) in (int,bool,str)
out=expression(post,{**witness,'result':value}); assert type(out) is bool and out is D['expect_post']
print(json.dumps({'status':'passed','source_sha256':D['sha256'],'witness':witness,'result':value,'postcondition':out,'interpreter':sys.version,'assumptions':['The selected function is called directly; module binding and callers are outside this reproduction.']}))
"""



def reproduction_program() -> str:
    """Return the trusted isolated witness-replay program used by the worker."""
    return _REPRODUCE


def _source_tree(repo: Path, rev: str) -> dict[str, tuple[int, bytes]]:
    deadline = time.monotonic() + 60
    roots = list(gitio.ls_tree(repo, rev, "-z", [], deadline))
    records = list(gitio.ls_tree(repo, rev, "-rtz", [], deadline))
    scopes = {row.split(b"\t", 1)[1] for row in roots}
    gitio.verify_listing(repo, rev, records, scopes, roots)
    return gitio.read_blobs(repo, project.tree_entries(records, allow_links=True))


def _target_python(files: dict[str, tuple[int, bytes]]) -> str:
    """Accept only a supported minor-version declaration in a tracked file."""
    marker = files.get(".python-version")
    if marker is None:
        return "3.12"
    mode, content = marker
    supported = (b"3.12", b"3.12\n", b"3.12\r\n", b"3.14", b"3.14\n", b"3.14\r\n")
    if mode not in (0o644, 0o755) or content not in supported:
        raise ValueError("unsupported or invalid tracked .python-version")
    return content.rstrip(b"\r\n").decode("ascii")


class LocalWorker(_Cancellable):
    """Lima guest execution with no candidate host execution or host mounts."""

    name = "mentat-autonomy"

    def __init__(self, trusted_repo: Path | None = None, expected_profile: dict | None = None) -> None:
        super().__init__()
        self._units: set[str] = set()
        self.trusted_repo = (trusted_repo or Path(__file__).resolve().parents[2]).resolve()
        self.expected_profile = dict(expected_profile) if expected_profile is not None else None

    def _profile(self) -> dict:
        actual = autonomy_worker.profile_identity(self.trusted_repo)
        if self.expected_profile is not None and any(actual[key] != self.expected_profile.get(key) for key in actual):
            raise ValueError("trusted worker profile differs from pinned policy")
        if self.expected_profile is None:
            self.expected_profile = actual
        return actual

    def cancel_all(self) -> None:
        with self._lock:
            units = list(self._units)
        super().cancel_all()
        if not units:
            return
        # Stopping the entire dedicated VM also fences a unit whose remote start
        # races the cancellation request. Confirm shutdown before returning.
        try:
            for unit in units:
                try:
                    _run(["limactl", "shell", self.name, "sudo", "systemctl", "stop", unit], timeout=10)
                except (OSError, RuntimeError, TimeoutError):
                    pass
            _run(["limactl", "stop", "--force", self.name], timeout=60)
            entries = [json.loads(line) for line in _run(["limactl", "list", "--json"], timeout=15).splitlines() if line.strip()]
            match = next((entry for entry in entries if entry.get("name") == self.name), None)
            if match is None or match.get("status") != "Stopped":
                raise RuntimeError("worker VM shutdown could not be confirmed")
        except (OSError, TimeoutError, ValueError, TypeError) as exc:
            raise RuntimeError("worker VM shutdown could not be confirmed") from exc

    def diagnostics(self) -> dict:
        if not shutil.which("limactl"):
            return {"status": "incomplete", "reason": "limactl is unavailable"}
        try:
            output = _run(["limactl", "list", "--json"], timeout=15)
            instances = [json.loads(line) for line in output.splitlines() if line.strip()]
            match = next((item for item in instances if item.get("name") == self.name), None)
            if not match or match.get("status") != "Running":
                return {"status": "incomplete", "reason": "managed Lima worker is not running"}
            config = match.get("config")
            if not isinstance(config, dict) or config.get("mounts") not in ([], None):
                return {"status": "incomplete", "reason": "worker host mounts cannot be ruled out"}
            forwards = config.get("portForwards")
            if not isinstance(forwards, list) or not forwards or any(not rule.get("ignore") for rule in forwards):
                return {"status": "incomplete", "reason": "worker port forwarding is not disabled"}
            check = "test \"$(uname -m)\" = aarch64 && . /etc/os-release && test \"$VERSION_ID\" = 24.04 && command -v bwrap >/dev/null && command -v systemd-run >/dev/null && test -x /opt/lean-4.35.0/bin/lean && test \"$(stat -c %U /opt/lean-4.35.0)\" = root && test ! -w /opt/lean-4.35.0 && test -f /etc/mentat-autonomy-provisioned && test \"$(stat -c %U /etc/mentat-autonomy-provisioned)\" = root && sudo -n -u nobody /usr/bin/bwrap --unshare-user --unshare-net --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib -- /bin/true"
            _run(["limactl", "shell", self.name, "sh", "-c", check], timeout=20)
            profile = self._profile()
            marker = _run(["limactl", "shell", self.name, "sudo", "sh", "-c", "test -z \"$(find /opt/mentat-tools \\( ! -user root -o \\( ! -type l -a -perm /022 \\) \\) -print -quit)\" && cat /opt/mentat-tools/profile.json"], timeout=30)
            if json.loads(marker) != profile:
                return {"status": "incomplete", "reason": "worker baseline does not match pinned profile"}
            return {"status": "ready", "name": self.name, "profile": profile}
        except (OSError, RuntimeError, TimeoutError, ValueError, TypeError) as exc:
            return {"status": "incomplete", "reason": str(exc)}

    def provision(self) -> dict:
        if not shutil.which("limactl"):
            return {"status": "incomplete", "reason": "limactl is unavailable"}
        try:
            existing = _run(["limactl", "list", "--json"], timeout=15)
            self._profile()
            entries = [json.loads(line) for line in existing.splitlines() if line.strip()]
            current = next((item for item in entries if item.get("name") == self.name), None)
            if current is not None:
                config = current.get("config")
                if not isinstance(config, dict) or config.get("mounts") not in ([], None) or not config.get("portForwards") or any(not rule.get("ignore") for rule in config["portForwards"]):
                    return {"status": "incomplete", "reason": "existing worker isolation cannot be verified"}
                if current.get("status") == "Stopped":
                    _run(["limactl", "start", self.name], timeout=1200)
                elif current.get("status") != "Running":
                    return {"status": "incomplete", "reason": "existing worker state is unknown"}
            config = {"arch": "aarch64", "cpus": 4, "memory": "6GiB", "disk": "40GiB", "mounts": [],
                      "portForwards": [{"guestPortRange": [1, 65535], "ignore": True}],
                      "images": [{"location": "https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-arm64.img", "arch": "aarch64", "digest": "sha256:50e24e1fb79e89116e939d90aa7738f7af48c442fa1bc2510131e0b39c50348e"}]}
            if current is None:
                with tempfile.TemporaryDirectory(prefix="mentat-lima-") as temp:
                    config_path = Path(temp) / "worker.yaml"
                    config_path.write_text(json.dumps(config), encoding="utf-8")
                    _run(["limactl", "start", "--name", self.name, str(config_path)], timeout=1200)
            installer = Path(__file__).resolve().parents[2] / "scripts" / "install-proof-checker"
            installer_bytes = installer.read_bytes() if installer.is_file() else zlib.decompress(base64.b64decode(_EMBEDDED_CHECKER_INSTALLER))
            if hashlib.sha256(installer_bytes).hexdigest() != "cd989b4f9c37e42300aef1181e668ba0bac400e38858370cc491c172dca8043c":
                return {"status": "incomplete", "reason": "trusted proof checker installer digest mismatch"}
            try:
                _run(["limactl", "shell", self.name, "test", "-x", "/opt/lean-4.35.0/bin/lean"], timeout=15)
            except RuntimeError:
                with tempfile.TemporaryFile() as source:
                    source.write(installer_bytes)
                    source.seek(0)
                    code, _, err = process.run(["limactl", "shell", self.name, "sudo", "bash", "-s"], stdin=source, output_limit=_OUTPUT_LIMIT, timeout=1200)
                if code:
                    return {"status": "incomplete", "reason": "proof checker installation failed: " + err.decode("utf-8", "replace")[-2000:]}
            # Ubuntu can restrict user namespaces through AppArmor. Install the same
            # digest-pinned bubblewrap profiles used by the protected CI checker.
            profile_script = r'''set -eu
if ! runuser -u nobody -- /usr/bin/bwrap --unshare-user --unshare-net --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib -- /bin/true; then
  test ! -e /etc/apparmor.d/bwrap-userns-restrict
  command -v apparmor_parser >/dev/null
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' EXIT
  curl --fail --location --proto '=https' --tlsv1.2 --retry 3 'https://gitlab.com/apparmor/apparmor/-/raw/1979af7710d0f38db6680bd7c19c80902f11f969/profiles/apparmor/profiles/extras/bwrap-userns-restrict' --output "$tmp"
  printf '%s  %s\n' 634d3d3427c483f123cb5ed53b71ea13040187e07d9f67ca74421d42a6170f0e "$tmp" | sha256sum --check -
  apparmor_parser --skip-kernel-load --skip-cache "$tmp"
  install -o root -g root -m 0644 "$tmp" /etc/apparmor.d/bwrap-userns-restrict
  apparmor_parser --replace /etc/apparmor.d/bwrap-userns-restrict
  runuser -u nobody -- /usr/bin/bwrap --unshare-user --unshare-net --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib -- /bin/true
fi
'''
            with tempfile.TemporaryFile() as source:
                source.write(profile_script.encode())
                source.seek(0)
                code, _, err = process.run(["limactl", "shell", self.name, "sudo", "bash", "-s"], stdin=source, output_limit=_OUTPUT_LIMIT, timeout=180)
            if code:
                return {"status": "incomplete", "reason": "bubblewrap setup failed: " + err.decode("utf-8", "replace")[-2000:]}
            try:
                _run(["limactl", "shell", self.name, "test", "-f", "/opt/mentat-tools/profile.json"], timeout=15)
            except RuntimeError:
                profile_bytes = autonomy_worker.provision_script(self.trusted_repo)
                profile = self._profile()
                provisioner = autonomy_worker.PROVISION_SCRIPT
                if not profile_bytes.endswith(provisioner) or hashlib.sha256(provisioner).hexdigest() != profile["provisioner_sha256"]:
                    raise ValueError("trusted worker provisioner differs from pinned profile") from None
                prefix = profile_bytes[:-len(provisioner)]
                input_keys = [("MENTAT_BASELINE_PROJECT", "pyproject_sha256"),
                              ("MENTAT_BASELINE_LOCK", "uv_lock_sha256")]
                input_keys += [(key, profile_key) for key, profile_key, _ in autonomy_worker.worker_assets()]
                pattern = rb"#!/usr/bin/env bash\n" + b"".join(
                    rb"export " + key.encode() + rb"='([A-Za-z0-9+/=]+)'\nexport " + key.encode() + rb"_SHA256='([0-9a-f]{64})'\n"
                    for key, _ in input_keys
                ) + rb"export MENTAT_PROVISIONER_SHA256='([0-9a-f]{64})'\n"
                match = re.fullmatch(pattern, prefix)
                if match is None:
                    raise ValueError("trusted worker inputs differ from pinned profile") from None
                for index, (_, profile_key) in enumerate(input_keys):
                    digest = hashlib.sha256(base64.b64decode(match[2 * index + 1], validate=True)).hexdigest()
                    if digest != profile[profile_key] or match[2 * index + 2].decode() != digest:
                        raise ValueError("trusted worker inputs differ from pinned profile") from None
                if match[2 * len(input_keys) + 1].decode() != profile["provisioner_sha256"]:
                    raise ValueError("trusted worker inputs differ from pinned profile") from None
                with tempfile.TemporaryFile() as source:
                    source.write(profile_bytes)
                    source.seek(0)
                    code, _, err = process.run(["limactl", "shell", self.name, "sudo", "bash", "-s"], stdin=source, output_limit=_OUTPUT_LIMIT, timeout=1200)
                if code:
                    return {"status": "incomplete", "reason": "trusted worker profile setup failed: " + err.decode("utf-8", "replace")[-2000:]}
            _run(["limactl", "shell", self.name, "sudo", "touch", "/etc/mentat-autonomy-provisioned"], timeout=20)
            return self.diagnostics()
        except (OSError, RuntimeError, TimeoutError, ValueError, TypeError) as exc:
            return {"status": "incomplete", "reason": str(exc)}

    def run(self, repo: Path, rev: str, argv: list[str], *, timeout_seconds: int = 600, trusted_checker: bool = False) -> dict:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a and "\0" not in a for a in argv):
            raise ValueError("argv must contain nonempty strings")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 7200:
            raise ValueError("invalid worker timeout")
        if not re.fullmatch(r"[0-9a-f]{40,64}", rev):
            raise ValueError("worker revision must be an immutable commit hash")
        ready = self.diagnostics()
        if ready["status"] != "ready":
            return ready
        if trusted_checker:
            if argv != ["scripts/verify-worker"]:
                return {"status": "incomplete", "reason": "trusted checker requires the exact pinned command"}
            return self._run_checker(repo, rev, timeout_seconds=timeout_seconds)
        try:
            gitio.verify_revision(repo, rev)
            files = _source_tree(repo, rev)
            selected = _target_python(files)
            interpreter = "/opt/mentat-tools/.venv-3.14/bin/python" if selected == "3.14" else "/opt/mentat-tools/.venv/bin/python"
            tools_output = self._execute(["limactl", "shell", self.name, "sh", "-c", "export PATH=/opt/mentat-tools/bin:/opt/mentat-tools/node/bin:/usr/bin:/bin; " + interpreter + " --version && uv --version && node --version && chromium --version && /opt/lean-4.35.0/bin/lean --version"], timeout=30)
            tools = {"versions": tools_output.strip(), "test_jobs": "1", "selected_python": interpreter,
                     "selected_python_version": tools_output.strip().splitlines()[0] if tools_output.strip() else ""}
            if not tools["versions"]:
                raise ValueError("worker tool versions unavailable")
            bundle = autonomy_worker.prepare_bundle(repo, rev)
            with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as archive:
                with tarfile.open(fileobj=source, mode="w") as content_archive:
                    for path, (mode, content) in sorted(files.items()):
                        if mode not in (0o644, 0o755):
                            raise ValueError("candidate tree contains unsupported non-regular file")
                        item = tarfile.TarInfo(path)
                        item.size, item.mode = len(content), mode
                        content_archive.addfile(item, io.BytesIO(content))
                source_size = source.tell()
                source.seek(0)
                with tarfile.open(fileobj=archive, mode="w") as envelope:
                    for name, size, data in (("source.tar", source_size, source), ("source.bundle", len(bundle), io.BytesIO(bundle))):
                        item = tarfile.TarInfo(name)
                        item.size = size
                        envelope.addfile(item, data)
                archive.seek(0)
                unit = "mentat-job-" + uuid.uuid4().hex
                with self._lock:
                    if self._cancelled:
                        raise RuntimeError("operation cancelled")
                    self._units.add(unit)
                try:
                    output = self._execute(["limactl", "shell", self.name, "sudo", "python3", "-c", _GUEST_RUNNER, base64.b64encode(json.dumps(argv).encode()).decode(), str(timeout_seconds), unit, rev], stdin=archive, timeout=timeout_seconds + 45)
                except _CommandFailed as exc:
                    marker = f"MENTAT_FINISHED:{unit}:{exc.code}"
                    if marker not in exc.stderr.splitlines():
                        self.cancel_all()
                        raise
                    if f"MENTAT_BOOTSTRAP_FAILED:{unit}" in exc.stderr.splitlines():
                        return {"status": "incomplete", "reason": "candidate package setup failed", "stdout": exc.stdout, "stderr": exc.stderr.replace(marker, "").strip(), "revision": rev, "tools": tools}
                    return {"status": "failed", "returncode": exc.code, "stdout": exc.stdout, "stderr": exc.stderr.replace(marker, "").strip(), "revision": rev, "tools": tools}
                except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError):
                    self.cancel_all()
                    raise
                finally:
                    with self._lock:
                        self._units.discard(unit)
            return {"status": "succeeded", "returncode": 0, "stdout": output, "stderr": "", "revision": rev, "tools": tools}
        except (OSError, RuntimeError, TimeoutError, ValueError, subprocess.SubprocessError) as exc:
            return {"status": "incomplete", "reason": str(exc)}

    def _run_checker(self, repo: Path, rev: str, *, timeout_seconds: int) -> dict:
        """Run only the pinned baseline checker fixture on the dedicated VM as root."""
        baseline = (self.expected_profile or {}).get("source_revision")
        if not isinstance(baseline, str) or not re.fullmatch(r"[0-9a-f]{40,64}", baseline):
            return {"status": "incomplete", "reason": "trusted checker baseline is not pinned"}
        try:
            gitio.verify_revision(self.trusted_repo, baseline)
            gitio.verify_revision(repo, rev)
            inputs = {}
            for path in _CHECKER_INPUTS:
                trusted = gitio.read_file(self.trusted_repo, baseline, path)
                candidate = gitio.read_file(repo, rev, path)
                if trusted != candidate:
                    return {"status": "incomplete", "reason": f"protected checker input changed: {path}"}
                inputs[path] = hashlib.sha256(trusted).hexdigest()
            files = _source_tree(self.trusted_repo, baseline)
            tools_output = self._execute(["limactl", "shell", self.name, "sh", "-c", "export PATH=/opt/mentat-tools/bin:/opt/mentat-tools/node/bin:/usr/bin:/bin UV_PYTHON_INSTALL_DIR=/opt/mentat-tools/python; /opt/mentat-tools/.venv/bin/python --version && $(uv python find 3.14) --version && uv --version && node --version && chromium --version && /opt/lean-4.35.0/bin/lean --version"], timeout=30)
            if not tools_output.strip():
                raise ValueError("worker tool versions unavailable")
            metadata = {"revision": rev, "support_baseline_revision": baseline, "protected_input_digests": inputs, "gate_scope": "protected checker environment and unchanged checker implementation", "tools": {"versions": tools_output.strip()}}
            with tempfile.TemporaryFile() as archive:
                with tarfile.open(fileobj=archive, mode="w") as bundle:
                    for path, (mode, content) in sorted(files.items()):
                        if mode not in (0o644, 0o755):
                            raise ValueError("trusted baseline contains unsupported non-regular file")
                        item = tarfile.TarInfo(path)
                        item.size, item.mode = len(content), mode
                        bundle.addfile(item, io.BytesIO(content))
                archive.seek(0)
                unit = "mentat-checker-" + uuid.uuid4().hex
                with self._lock:
                    if self._cancelled:
                        raise RuntimeError("operation cancelled")
                    self._units.add(unit)
                try:
                    output = self._execute(["limactl", "shell", self.name, "sudo", "python3", "-c", _CHECKER_GUEST_RUNNER, unit, str(timeout_seconds)], stdin=archive, timeout=timeout_seconds + 45)
                except _CommandFailed as exc:
                    marker = f"MENTAT_FINISHED:{unit}:{exc.code}"
                    if marker not in exc.stderr.splitlines():
                        self.cancel_all()
                        raise
                    return {**metadata, "status": "failed", "returncode": exc.code, "stdout": exc.stdout, "stderr": exc.stderr.replace(marker, "").strip()}
                except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError):
                    self.cancel_all()
                    raise
                finally:
                    with self._lock:
                        self._units.discard(unit)
            if re.search(r"(?m)^SKIPPED\b|\b[1-9][0-9]* skipped\b", output):
                return {**metadata, "status": "incomplete", "reason": "protected checker fixtures were skipped", "stdout": output}
            if not re.search(r"\b[1-9][0-9]* passed\b", output):
                return {**metadata, "status": "incomplete", "reason": "protected checker produced no passing fixture count", "stdout": output}
            return {**metadata, "status": "succeeded", "returncode": 0, "stdout": output, "stderr": ""}
        except (OSError, RuntimeError, TimeoutError, ValueError, subprocess.SubprocessError) as exc:
            return {"status": "incomplete", "reason": str(exc)}

    def reproduce(self, repo: Path, rev: str, evidence: dict, *, timeout_seconds: int = 600) -> dict:
        """Reproduce an original source counterexample in isolated CPython."""
        return self._replay_witness(repo, rev, evidence, repair=False, timeout_seconds=timeout_seconds)

    def verify_repair(self, repo: Path, rev: str, evidence: dict, *, timeout_seconds: int = 600) -> dict:
        """Replay the original counterexample and check the repaired postcondition."""
        return self._replay_witness(repo, rev, evidence, repair=True, timeout_seconds=timeout_seconds)

    def _replay_witness(self, repo: Path, rev: str, evidence: dict, *, repair: bool, timeout_seconds: int) -> dict:
        try:
            if evidence.get("status") != "counterexample":
                raise ValueError("source evidence is not a counterexample")
            identity = evidence["source_identity"]
            contract = evidence["contract"]
            original_rev = gitio.verify_revision(repo, identity["revision"])
            original = gitio.read_file(repo, original_rev, identity["path"])
            if hashlib.sha256(original).hexdigest() != identity["source_sha256"]:
                raise ValueError("original counterexample source hash differs")
            if not repair and original_rev != rev:
                raise ValueError("counterexample revision differs")
            gitio.verify_revision(repo, rev)
            raw = gitio.read_file(repo, rev, identity["path"])
            digest = hashlib.sha256(raw).hexdigest()
            data = {"source": base64.b64encode(raw).decode(), "sha256": digest, "function": identity["function"], "inputs": contract["inputs"], "requires": contract["requires"], "ensures": contract["ensures"], "witness": evidence["counterexample"], "expect_post": repair}
            payload = base64.b64encode(json.dumps(data, allow_nan=False).encode()).decode()
            result = self.run(repo, rev, ["python3", "-I", "-S", "-c", _REPRODUCE, payload], timeout_seconds=timeout_seconds)
            if result.get("status") != "succeeded" or result.get("returncode") != 0 or not result.get("tools"):
                return {"status": "incomplete", "reason": "isolated witness replay did not complete", "worker_result": result}
            output = json.loads(result["stdout"])
            if output.get("status") != "passed" or output.get("source_sha256") != digest or output.get("witness") != evidence["counterexample"] or output.get("postcondition") is not repair:
                raise ValueError("witness replay output mismatch")
            common = {**output, "revision": rev, "evidence_id": evidence.get("id"), "inputs": evidence["counterexample"], "precondition": True, "tools": result["tools"]}
            if repair:
                return {**common, "postcondition": True, "method": "protected-runtime-repair"}
            return {**common, "violated": True, "method": "protected-runtime-reproduction"}
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return {"status": "incomplete", "reason": str(exc)}


class GitHub:
    def __init__(self, required_checks: list[list[str]] | None = None, *, merge_policy: dict | None = None) -> None:
        self.required_checks = required_checks
        self.merge_policy = autonomy_private_merge.validate_policy(merge_policy)
        self.private_prepare: Callable[[dict], None] | None = None
        self.private_dispatch: Callable[[dict], None] | None = None
        self.private_resolve: Callable[[str, str, str], None] | None = None
        self.private_lookup: Callable[[str, str, str], dict | None] | None = None

    def reconcile(self, repo: Path, branch: str, expected_head: str, expected_base: str) -> dict:
        """Read remote PR state after an ambiguous publish or squash-merge response."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch) or ".." in branch or branch.endswith("/") or not all(isinstance(x, str) and re.fullmatch(r"[0-9a-f]{40,64}", x) for x in (expected_head, expected_base)):
            return {"status": "incomplete", "reason": "invalid pull request identity"}
        try:
            policy = autonomy_private_merge.validate_policy(self.merge_policy)
            fields = "number,url,state,headRefName,headRefOid,baseRefName,baseRefOid,isCrossRepository,mergeCommit"
            rows = json.loads(_gh(repo, "pr", "list", *(("--repo", "https://github.com/" + policy["repository"]) if policy is not None else ()), "--state", "all", "--head", branch, "--base", "main", "--limit", "100", "--json", fields))
            if not isinstance(rows, list) or len(rows) >= 100:
                return {"status": "incomplete", "reason": "pull request search is incomplete"}
            expected_pr = None
            if policy is not None:
                if not callable(self.private_lookup) or not callable(self.private_resolve):
                    return {"status": "incomplete", "reason": "durable private merge authority unavailable"}
                saved = self.private_lookup(expected_head, expected_base, branch)
                if saved is not None:
                    expected_pr = saved["intent"]["pr_number"] if saved.get("intent") else saved.get("publication_pr_number")
            matches = [row for row in rows if isinstance(row, dict) and row.get("headRefName") == branch and (expected_pr is None or row.get("number") == expected_pr) and (row.get("headRefOid") == expected_head or policy is not None) and row.get("baseRefName") == "main" and row.get("isCrossRepository") is False]
            if not matches:
                return {"status": "not_found", "reason": "no matching PR observed; absence is not proof of no prior publish"}
            if len(matches) != 1:
                return {"status": "incomplete", "reason": "multiple matching pull requests"}
            pr = matches[0]
            if type(pr.get("number")) is not int or not isinstance(pr.get("url"), str):
                return {"status": "incomplete", "reason": "pull request identity is incomplete"}
            if policy is not None:
                def api(path: str) -> Any:
                    return json.loads(_gh(repo, "api", "--hostname", policy["host"], path))
                if not callable(self.private_lookup) or not callable(self.private_resolve):
                    return {"status": "incomplete", "reason": "durable private merge authority unavailable"}
                return autonomy_private_merge.reconcile(repo, policy, branch, pr["number"], expected_head, expected_base, git, api, self.private_lookup, self.private_resolve)
            if pr.get("state") == "OPEN":
                if pr.get("baseRefOid") != expected_base:
                    return {"status": "incomplete", "reason": "pull request base changed"}
                return {"status": "found", "state": "open", "number": pr["number"], "url": pr["url"], "head": expected_head, "base": expected_base}
            if pr.get("state") == "MERGED":
                merge_oid = (pr.get("mergeCommit") or {}).get("oid")
                if not isinstance(merge_oid, str) or not re.fullmatch(r"[0-9a-f]{40,64}", merge_oid):
                    return {"status": "incomplete", "reason": "merged commit identity unavailable"}
                name = json.loads(_gh(repo, "repo", "view", "--json", "nameWithOwner"))["nameWithOwner"]
                commit = json.loads(_gh(repo, "api", f"repos/{name}/commits/{merge_oid}"))
                parents = commit.get("parents") or []
                head_tree = git(repo, "rev-parse", "--verify", expected_head + "^{tree}")
                if len(parents) != 1 or parents[0].get("sha") != expected_base or commit.get("commit", {}).get("tree", {}).get("sha") != head_tree:
                    return {"status": "incomplete", "reason": "merged commit does not match the pinned squash inputs"}
                return {"status": "found", "state": "merged", "number": pr["number"], "url": pr["url"], "head": expected_head, "base": expected_base, "merge_commit": merge_oid}
            return {"status": "incomplete", "reason": "matching pull request is closed without merge"}
        except (OSError, RuntimeError, TimeoutError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            return {"status": "incomplete", "reason": str(exc)}

    def publish(self, repo: Path, branch: str, title: str, body_file: Path) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch) or branch in {"main", "master"} or ".." in branch or branch.endswith("/"):
            raise ValueError("invalid pull request branch")
        if not title.strip() or not body_file.is_file():
            raise ValueError("PR title and body file required")
        try:
            # Explicit SSH transport is required; do not allow caller Git config to install hooks.
            remote = git(repo, "remote", "get-url", "origin")
            policy = autonomy_private_merge.validate_policy(self.merge_policy)
            if policy is not None:
                def api(path: str) -> Any:
                    return json.loads(_gh(repo, "api", "--hostname", policy["host"], path))
                autonomy_private_merge.inspect_repository(repo, policy, git, api)
                remote = autonomy_private_merge.remote_url(policy)
            if not (remote.startswith("git@") or remote.startswith("ssh://")):
                raise ValueError("origin must use SSH")
            git(repo, "push", remote if policy is not None else "origin", f"HEAD:refs/heads/{branch}")
            url = _gh(repo, "pr", "create", *(("--repo", "https://github.com/" + policy["repository"]) if policy is not None else ()), "--base", "main", "--head", branch, "--title", title, "--body-file", str(body_file.resolve())).strip()
            if policy is not None and not re.fullmatch(r"https://github\.com/" + re.escape(policy["repository"]) + r"/pull/[1-9][0-9]*", url):
                return {"status": "failed", "error": "private publication identity is unconfirmed"}
            return {"status": "succeeded", "url": url}
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            return {"status": "failed", "error": str(exc)}

    def merge(self, repo: Path, pr_number: int, expected_head: str, expected_base: str, *, checks: list[dict], reviews: list[dict], active: bool | Callable[[], bool]) -> dict:
        is_active = active if callable(active) else lambda: active
        if not is_active():
            return {"status": "incomplete", "reason": "session is not active"}
        if type(pr_number) is not int or pr_number <= 0 or not all(re.fullmatch(r"[0-9a-f]{40,64}", x) for x in (expected_head, expected_base)):
            return {"status": "incomplete", "reason": "invalid merge identity"}
        if not self.required_checks or any(not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg for arg in command) for command in self.required_checks):
            return {"status": "incomplete", "reason": "trusted required checks are unavailable"}
        required_commands = [tuple(command) for command in self.required_checks]
        if len(set(required_commands)) != len(required_commands):
            return {"status": "incomplete", "reason": "duplicate required check"}
        try:
            policy = autonomy_private_merge.validate_policy(self.merge_policy)
            name = policy["repository"] if policy is not None else json.loads(_gh(repo, "repo", "view", "--json", "nameWithOwner"))["nameWithOwner"]
            def api(path: str) -> Any:
                args = ("--hostname", policy["host"]) if policy is not None else ()
                return json.loads(_gh(repo, "api", *args, path))
            if policy is not None:
                autonomy_private_merge.inspect_repository(repo, policy, git, api)
                required = {}
            else:
                protection = api(f"repos/{name}/branches/main/protection")
                required = protection.get("required_status_checks") or {}
                bypass = ((protection.get("required_pull_request_reviews") or {}).get("bypass_pull_request_allowances") or {})
                if required.get("strict") is not True or (protection.get("enforce_admins") or {}).get("enabled") is not True or any(bypass.get(key) for key in ("users", "teams", "apps")):
                    return {"status": "incomplete", "reason": "branch protection is not strict or permits bypass"}
            git(repo, "fetch", autonomy_private_merge.remote_url(policy) if policy is not None else "origin", "+refs/heads/main:refs/remotes/origin/main")
            if git(repo, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}") != expected_base:
                return {"status": "incomplete", "reason": "base changed"}
            pr = api(f"repos/{name}/pulls/{pr_number}")
            if pr.get("state") != "open" or pr["head"]["sha"] != expected_head or pr["base"]["sha"] != expected_base or pr["base"]["ref"] != "main":
                return {"status": "incomplete", "reason": "pull request identity changed"}
            if not checks or any(check.get("head") != expected_head or check.get("base") != expected_base or check.get("status") != "passed" or not isinstance(check.get("command"), list) or not check["command"] or not isinstance(check.get("tools"), dict) or not check["tools"] or type(check.get("returncode")) is not int or check["returncode"] != 0 for check in checks):
                return {"status": "incomplete", "reason": "local check evidence is incomplete or stale"}
            commands = [tuple(check["command"]) for check in checks]
            if any(commands.count(command) != 1 for command in required_commands):
                return {"status": "incomplete", "reason": "required local checks are missing or duplicated"}
            files = api(f"repos/{name}/pulls/{pr_number}/files?per_page=100")
            if not isinstance(files, list) or len(files) >= 100 or any(not isinstance(f.get("filename"), str) for f in files):
                return {"status": "incomplete", "reason": "changed files cannot be completely verified"}
            sensitive = any(re.search(r"(^|/)(proof|checker|autonomy|source_verify|requirements|CAS)|^\.github/", f["filename"], re.I) for f in files)
            raw_diff = gitio.run(repo, "diff", "--binary", "--no-ext-diff", "--no-textconv", expected_base, expected_head, limit=16_000_000)
            digest = hashlib.sha256(raw_diff).hexdigest()
            reviewers = set()
            for review in reviews:
                reviewer = review.get("reviewer_id")
                if review.get("head") != expected_head or review.get("base") != expected_base or review.get("status") != "passed" or review.get("diff_digest") != digest or not isinstance(reviewer, str) or not reviewer or reviewer == review.get("author_id") or reviewer in reviewers or not review.get("author_id"):
                    return {"status": "incomplete", "reason": "review evidence is incomplete, stale, or not independent"}
                reviewers.add(reviewer)
            if len(reviewers) < (2 if sensitive else 1):
                return {"status": "incomplete", "reason": "not enough independent reviews"}
            if policy is not None:
                if not callable(self.private_prepare) or not callable(self.private_lookup) or not callable(self.private_dispatch) or not callable(self.private_resolve):
                    return {"status": "incomplete", "reason": "durable private merge authority unavailable"}
                return autonomy_private_merge.merge(repo, policy, pr_number, expected_head, expected_base, git, api, is_active, self.private_prepare, self.private_lookup, self.private_dispatch, self.private_resolve)
            rulesets = api(f"repos/{name}/rulesets?includes_parents=true&per_page=100")
            if not isinstance(rulesets, list) or len(rulesets) >= 100:
                return {"status": "incomplete", "reason": "repository rulesets are not fully visible"}
            for ruleset in rulesets:
                details = api(ruleset["_links"]["self"]["href"])
                if details.get("enforcement") == "active" and details.get("bypass_actors"):
                    return {"status": "incomplete", "reason": "active ruleset permits bypass"}
            check_apps = {check["context"]: check.get("app_id") for check in (required.get("checks") or [])}
            contexts = set(required.get("contexts") or []) | set(check_apps)
            if not contexts:
                return {"status": "incomplete", "reason": "branch protection has no required checks"}
            statuses = api(f"repos/{name}/commits/{expected_head}/status")
            runs = api(f"repos/{name}/commits/{expected_head}/check-runs?per_page=100")
            if runs.get("total_count", 0) > len(runs.get("check_runs", [])):
                return {"status": "incomplete", "reason": "check runs are incomplete"}
            for context in contexts:
                app_id = check_apps.get(context)
                matches = [s.get("state") == "success" for s in statuses.get("statuses", []) if s.get("context") == context and app_id in (None, -1)]
                matches += [r.get("status") == "completed" and r.get("conclusion") == "success" for r in runs.get("check_runs", []) if r.get("name") == context and r.get("head_sha") == expected_head and (app_id in (None, -1) or (r.get("app") or {}).get("id") == app_id)]
                if len(matches) != 1 or not matches[0]:
                    return {"status": "incomplete", "reason": f"required check not passing: {context}"}
            # Recheck immediately before merge; GitHub's match-head gate also fences the PR head.
            git(repo, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
            if git(repo, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}") != expected_base:
                return {"status": "incomplete", "reason": "base changed"}
            fresh = api(f"repos/{name}/pulls/{pr_number}")
            if fresh["head"]["sha"] != expected_head or fresh["base"]["sha"] != expected_base:
                return {"status": "incomplete", "reason": "pull request identity changed"}
            if not is_active():
                return {"status": "incomplete", "reason": "session is not active"}
            _gh(repo, "pr", "merge", str(pr_number), "--match-head-commit", expected_head, "--squash", "--delete-branch")
            return {"status": "succeeded", "head": expected_head, "base": expected_base, "pr_number": pr_number}
        except (OSError, RuntimeError, TimeoutError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            return {"status": "incomplete", "reason": str(exc)}
