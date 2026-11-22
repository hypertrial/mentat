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


def _git(repo: Path, *args: str, limit: int) -> bytes:
    return gitio.run(repo, *args, limit=limit, timeout=30)


def snapshot(repo: Path, revision: str) -> Snapshot:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ValueError("revision must be a full commit SHA")
    resolved = gitio.verify_revision(repo, revision.lower())
    records = [
        record
        for record in _git(
            repo,
            "ls-tree",
            "-rtz",
            "--full-tree",
            resolved,
            "--",
            _PREFIX,
            limit=(_MAX_FILES + gitio.MAX_TREES) * (_MAX_PATH + 100),
        ).split(b"\0")
        if record
    ]
    gitio.verify_listing(repo, resolved, records, {_PREFIX.rstrip("/").encode()})
    entries = {}
    for record in records:
        meta, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = meta.split(b" ")
        if kind == b"tree":
            continue
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
        entries[relative] = (0o644, object_id)
        if len(entries) > _MAX_FILES:
            raise ValueError("candidate CLI has too many files")
    if "command/setup.py" not in entries:
        raise ValueError("commit has no setup CLI source")
    files = gitio.read_blobs(repo, entries, max_file=MAX_CANDIDATE_FILE, max_total=_MAX_TOTAL)
    digest = hashlib.sha256()
    for relative, (_, content) in files.items():
        raw_path = (_PREFIX + relative).encode()
        digest.update(len(raw_path).to_bytes(4, "big"))
        digest.update(raw_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return Snapshot(
        resolved,
        digest.hexdigest(),
        tuple((relative, content) for relative, (_, content) in files.items()),
    )


def run_case(
    snapshot: Snapshot, context: str, case, selected_properties: tuple[str, ...], run_id: str
) -> dict:
    from . import docker_runtime

    return docker_runtime.run_case(snapshot, context, case, selected_properties, run_id)
