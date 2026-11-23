"""Private, immutable actual-input artifacts for final source review."""

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

from . import gitio
from .project import safe_relative_path


def source_entry(repo: Path, revision: str, path: str) -> tuple[str, str]:
    """Read a verified regular Git entry and its blob identity."""
    safe_relative_path(path)
    rows = list(gitio.ls_tree(repo, revision, "-z", [b":(literal)" + path.encode()], time.monotonic() + 60))
    gitio.verify_listing(repo, revision, rows, set())
    if len(rows) != 1:
        raise ValueError("missing or non-regular file")
    meta, name = rows[0].split(b"\t", 1)
    mode, kind, blob = meta.split(b" ")
    if name != path.encode() or mode not in (b"100644", b"100755") or kind != b"blob":
        raise ValueError("missing or non-regular file")
    gitio.read_file(repo, revision, path)
    return mode.decode(), blob.decode()


def _directory(root: Path, job_id: str, attempt: int, *, create: bool) -> int:
    if not re.fullmatch(r"[0-9a-f]+", job_id) or type(attempt) is not int or attempt < 1:
        raise ValueError("invalid source input directory")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in ("agents", job_id, str(attempt)):
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            info = os.fstat(child)
            mode = stat.S_IMODE(info.st_mode)
            if info.st_uid != os.geteuid() or mode & 0o022 or (not create and mode != 0o700):
                os.close(child)
                raise ValueError("unsafe source input directory")
            if mode != 0o700:
                os.fchmod(child, 0o700)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(fd: int, name: str, limit: int) -> bytes:
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        info = os.fstat(source)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > limit):
            raise ValueError("unsafe source input file")
        chunks = []
        total = 0
        while data := os.read(source, min(65536, limit + 1 - total)):
            total += len(data)
            if total > limit:
                raise ValueError("source input file exceeds limit")
            chunks.append(data)
        return b"".join(chunks)
    finally:
        os.close(source)


def _write(fd: int, name: str, data: bytes) -> None:
    try:
        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    except FileExistsError:
        if _read(fd, name, len(data)) != data:
            raise ValueError("source input artifact identity conflict") from None
    else:
        with os.fdopen(target, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def persist(repo: Path, root: Path, job: dict, prompt: str, context: str, bindings: dict) -> dict:
    """Persist exact prompt and controller-derived manifest before dispatch."""
    supplied = json.loads(context.split("\nSOURCE_CONTEXT_JSON:\n", 1)[1])
    revision = supplied["revision"]
    files = []
    for path, content in sorted(supplied["files"].items()):
        mode, blob = source_entry(repo, revision, path)
        files.append({"path": path, "mode": mode, "blob": blob,
                      "content_sha256": hashlib.sha256(content.encode()).hexdigest()})
    prompt_bytes = prompt.encode("utf-8")
    prompt_hash = hashlib.sha256(prompt_bytes).hexdigest()
    manifest = {**bindings, "prompt_sha256": prompt_hash, "files": files, "omitted": supplied["omitted"]}
    manifest_bytes = json.dumps(manifest, sort_keys=True, ensure_ascii=True).encode()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    fd = _directory(root, job["id"], job["attempt"], create=True)
    try:
        _write(fd, f"prompt-{prompt_hash}.txt", prompt_bytes)
        _write(fd, f"manifest-{manifest_hash}.json", manifest_bytes)
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"prompt_sha256": prompt_hash, "input_manifest_sha256": manifest_hash,
            "input_manifest": f"agents/{job['id']}/{job['attempt']}/manifest-{manifest_hash}.json"}


def validate(repo: Path, root: Path, job: dict, decision: dict, bindings: dict,
             expected_prompt: str, scope: dict) -> None:
    """Validate saved bytes, manifest, Git contents and the expected actual prompt."""
    manifest_hash = decision.get("input_manifest_sha256")
    if not isinstance(manifest_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_hash):
        raise ValueError("invalid source input manifest digest")
    expected = f"agents/{job['id']}/{job['attempt']}/manifest-{manifest_hash}.json"
    if decision.get("input_manifest") != expected:
        raise ValueError("invalid source input manifest path")
    fd = _directory(root, job["id"], job["attempt"], create=False)
    try:
        raw_manifest = _read(fd, f"manifest-{manifest_hash}.json", 1_000_000)
        if hashlib.sha256(raw_manifest).hexdigest() != manifest_hash:
            raise ValueError("source input manifest changed")
        manifest = json.loads(raw_manifest)
        prompt_hash = manifest.get("prompt_sha256")
        if not isinstance(prompt_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", prompt_hash):
            raise ValueError("invalid source prompt digest")
        raw_prompt = _read(fd, f"prompt-{prompt_hash}.txt", 2_000_000)
        if (hashlib.sha256(raw_prompt).hexdigest() != prompt_hash
                or job["outcome"].get("prompt_sha256") != prompt_hash
                or raw_prompt != expected_prompt.encode()):
            raise ValueError("source prompt differs from current controller input")
    finally:
        os.close(fd)
    if any(manifest.get(key) != value for key, value in bindings.items()):
        raise ValueError("source input manifest bindings differ")
    context = json.loads(raw_prompt.decode().split("\nSOURCE_CONTEXT_JSON:\n", 1)[1])
    if (context.get("revision") != bindings["head"] or context.get("source_batch") != scope
            or context.get("omitted") != manifest.get("omitted")):
        raise ValueError("source input context differs from manifest")
    actual = []
    for path, content in sorted(context["files"].items()):
        mode, blob = source_entry(repo, bindings["head"], path)
        if gitio.read_file(repo, bindings["head"], path) != content.encode():
            raise ValueError("source input content differs from Git")
        actual.append({"path": path, "mode": mode, "blob": blob,
                       "content_sha256": hashlib.sha256(content.encode()).hexdigest()})
    if manifest.get("files") != actual:
        raise ValueError("source input file manifest differs")
    if any(item["mode"] == "current-source" and item["path"] not in context["files"]
           for item in scope["obligations"]):
        raise ValueError("source input omitted a required file")
