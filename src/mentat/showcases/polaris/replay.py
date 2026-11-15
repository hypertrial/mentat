"""Capture committed CLI sources without executing code from the candidate tree."""

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_PREFIX = "client/python/apache_polaris/cli/"
_MAX_FILE = 2 * 1024 * 1024
_MAX_TOTAL = 16 * 1024 * 1024


@dataclass(frozen=True)
class Snapshot:
    revision: str
    digest: str
    files: tuple[tuple[str, bytes], ...]


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repo), *args],
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=True,
    )
    return result.stdout


def snapshot(repo: Path, revision: str) -> Snapshot:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ValueError("revision must be a full commit SHA")
    resolved = _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    if resolved != revision.lower():
        raise ValueError("revision did not resolve to the supplied commit")
    entries = _git(repo, "ls-tree", "-rz", "--full-tree", resolved, "--", _PREFIX)
    files = []
    total = 0
    digest = hashlib.sha256()
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, object_id = meta.split(b" ")
        path = raw_path.decode("utf-8")
        relative = path.removeprefix(_PREFIX)
        if (
            mode != b"100644"
            or kind != b"blob"
            or not re.fullmatch(r"[A-Za-z0-9_/-]+\.py", relative)
        ):
            raise ValueError("candidate CLI contains an unsupported file")
        size = int(_git(repo, "cat-file", "-s", object_id.decode()).strip())
        total += size
        if size > _MAX_FILE or total > _MAX_TOTAL:
            raise ValueError("candidate CLI exceeds the snapshot size limit")
        content = _git(repo, "cat-file", "blob", object_id.decode())
        if len(content) != size:
            raise ValueError("candidate blob size changed")
        digest.update(len(raw_path).to_bytes(4, "big"))
        digest.update(raw_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        files.append((relative, content))
    if not files or not any(path == "command/setup.py" for path, _ in files):
        raise ValueError("commit has no setup CLI source")
    return Snapshot(resolved, digest.hexdigest(), tuple(files))


def run_case(
    snapshot: Snapshot, context: str, case, selected_properties: tuple[str, ...], run_id: str
) -> dict:
    from . import docker_runtime

    return docker_runtime.run_case(snapshot, context, case, selected_properties, run_id)
