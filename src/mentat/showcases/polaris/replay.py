"""Capture committed CLI sources without executing code from the candidate tree."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from mentat import gitio

_PREFIX = "client/python/apache_polaris/cli/"
MAX_CANDIDATE_FILE = 2 * 1024 * 1024
_MAX_TOTAL = 16 * 1024 * 1024
_MAX_FILES = 4096
_MAX_PATH = 512


@dataclass(frozen=True)
class Snapshot:
    revision: str
    digest: str
    files: tuple[tuple[str, bytes], ...]


def _git(repo: Path, *args: str, limit: int, data: bytes | None = None) -> bytes:
    return gitio.run(repo, *args, limit=limit, timeout=30, data=data)


def snapshot(repo: Path, revision: str) -> Snapshot:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ValueError("revision must be a full commit SHA")
    resolved = (
        _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}", limit=128).decode().strip()
    )
    if resolved != revision.lower():
        raise ValueError("revision did not resolve to the supplied commit")
    entries = _git(
        repo,
        "ls-tree",
        "-rz",
        "--full-tree",
        resolved,
        "--",
        _PREFIX,
        limit=_MAX_FILES * (_MAX_PATH + 100),
    )
    paths = []
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
            or not path.startswith(_PREFIX)
            or len(relative) > _MAX_PATH
            or not re.fullmatch(r"[A-Za-z0-9_/-]+\.py", relative)
        ):
            raise ValueError("candidate CLI contains an unsupported file")
        paths.append((raw_path, relative, object_id))
        if len(paths) > _MAX_FILES:
            raise ValueError("candidate CLI has too many files")
    if not any(path == "command/setup.py" for _, path, _ in paths):
        raise ValueError("commit has no setup CLI source")
    ids = b"".join(oid + b"\n" for _, _, oid in paths)
    headers = _git(
        repo,
        "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        data=ids,
        limit=len(paths) * 100,
    ).splitlines()
    if len(headers) != len(paths):
        raise ValueError("incomplete Git objects")
    sizes = []
    for (_, _, oid), header in zip(paths, headers, strict=True):
        parts = header.split()
        if len(parts) != 3 or parts[0] != oid or parts[1] != b"blob" or not parts[2].isdigit():
            raise ValueError("invalid Git object")
        sizes.append(int(parts[2]))
    if any(size > MAX_CANDIDATE_FILE for size in sizes) or sum(sizes) > _MAX_TOTAL:
        raise ValueError("candidate CLI exceeds the snapshot size limit")
    limit = sum(sizes) + sum(
        len(oid) + len(str(size)) + 8 for (_, _, oid), size in zip(paths, sizes, strict=True)
    )
    data = _git(repo, "cat-file", "--batch", data=ids, limit=limit)
    files = []
    digest = hashlib.sha256()
    offset = 0
    for (raw_path, relative, oid), size in zip(paths, sizes, strict=True):
        header = oid + b" blob " + str(size).encode() + b"\n"
        if data[offset : offset + len(header)] != header:
            raise ValueError("invalid Git batch output")
        offset += len(header)
        content = data[offset : offset + size]
        offset += size
        if len(content) != size or data[offset : offset + 1] != b"\n":
            raise ValueError("candidate blob size changed")
        offset += 1
        digest.update(len(raw_path).to_bytes(4, "big"))
        digest.update(raw_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        files.append((relative, content))
    if offset != len(data):
        raise ValueError("invalid Git batch output")
    return Snapshot(resolved, digest.hexdigest(), tuple(files))


def run_case(
    snapshot: Snapshot, context: str, case, selected_properties: tuple[str, ...], run_id: str
) -> dict:
    from . import docker_runtime

    return docker_runtime.run_case(snapshot, context, case, selected_properties, run_id)
