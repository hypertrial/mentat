"""Git reads for verifier inputs: one environment and argument policy, bounded output and time."""

import os
import re
import selectors
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path

from . import process

MAX_FILE = 1_000_000
MAX_TOTAL = 32_000_000
MAX_FILES = 4096
MAX_PATH = 512
# Long enough for a repository path or an object name from rev-parse.
_LINE = 4096


def _git_env() -> dict[str, str]:
    # Ignore caller-provided Git environment that can redirect the repository or inject config.
    return {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_ALLOW_PROTOCOL": "",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def argv(root: Path, *args: str | bytes) -> list[str | bytes]:
    # The option and GIT_NO_REPLACE_OBJECTS both apply, so every caller reads the real objects.
    return ["git", "--no-replace-objects", "-C", str(root), *args]


def run(
    root: Path, *args: str, limit: int, timeout: float = 60, data: bytes | None = None
) -> bytes:
    """Return a Git command's stdout, with `data` on stdin when given.

    Raises subprocess.CalledProcessError on a failing exit, subprocess.TimeoutExpired past the
    timeout, and ValueError when stdout exceeds `limit`.
    """
    command = argv(root, *args)
    with (tempfile.TemporaryFile() if data is not None else nullcontext()) as source:
        if source is not None:
            source.write(data)
            source.seek(0)
        try:
            code, output, _ = process.run(
                command,
                output_limit=limit,
                timeout=timeout,
                env=_git_env(),
                stdin=source,
                stderr=False,
            )
        except TimeoutError as exc:
            raise subprocess.TimeoutExpired(command, timeout) from exc
        except RuntimeError as exc:
            raise ValueError(f"Git {args[0]} output exceeds size limit") from exc
    if code:
        raise subprocess.CalledProcessError(code, command)
    return output


def _git(root: Path, *args: str, limit: int, data: bytes | None = None) -> bytes:
    try:
        return run(root, *args, limit=limit, data=data)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"git command failed: {args[0]}") from exc


def ls_tree(
    root: Path, revision: str, flags: str, scope: list[bytes], deadline: float
) -> Iterator[bytes]:
    # Streamed rather than run through process.run: callers consume records as they arrive,
    # and the deadline and path-length checks stop a long listing early.
    # Match the stored path bytes even on filesystems where Git normally precomposes Unicode.
    try:
        with (
            subprocess.Popen(
                argv(
                    root,
                    "-c",
                    "core.precomposeUnicode=false",
                    "ls-tree",
                    flags,
                    "--full-tree",
                    revision,
                    "--",
                    *scope,
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_git_env(),
            ) as child,
            selectors.DefaultSelector() as selector,
        ):
            assert child.stdout is not None
            selector.register(child.stdout, selectors.EVENT_READ)
            pending = b""
            try:
                while (remaining := deadline - time.monotonic()) > 0 and selector.select(remaining):
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        if pending or child.wait(timeout=max(0, deadline - time.monotonic())):
                            raise ValueError("git command failed: ls-tree")
                        return
                    *records, pending = (pending + chunk).split(b"\0")
                    yield from records
                    if len(pending) > 4 * MAX_PATH + 100:
                        raise ValueError("Git tree path exceeds size limit")
                raise ValueError("git command timed out: ls-tree")
            finally:
                if child.poll() is None:
                    child.kill()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("git command failed: ls-tree") from exc


def repo_root(repo: Path) -> Path:
    return Path(
        os.fsdecode(_git(repo, "rev-parse", "--show-toplevel", limit=_LINE).removesuffix(b"\n"))
    ).resolve()


def verify_revision(root: Path, rev: str) -> str:
    fmt = _git(root, "rev-parse", "--show-object-format", limit=_LINE).strip()
    length = {b"sha1": 40, b"sha256": 64}.get(fmt)
    if length is None or not re.fullmatch(rf"[0-9a-f]{{{length}}}", rev):
        raise ValueError("revision must be a full commit ID")
    result = (
        _git(
            root,
            "rev-parse",
            "--verify",
            "--end-of-options",
            rev + "^{commit}",
            limit=_LINE,
        )
        .strip()
        .decode("ascii")
    )
    if result != rev:
        raise ValueError("invalid commit ID")
    return result


def read_blobs(root: Path, entries: dict[str, tuple[int, bytes]]) -> dict[str, tuple[int, bytes]]:
    if not entries:
        return {}
    ids = b"".join(oid + b"\n" for _, oid in entries.values())
    headers = _git(
        root,
        "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        data=ids,
        limit=len(entries) * 100,
    ).splitlines()
    if len(headers) != len(entries):
        raise ValueError("incomplete Git objects")
    sizes = []
    for (_, oid), header in zip(entries.values(), headers, strict=True):
        parts = header.split()
        if len(parts) != 3 or parts[0] != oid or parts[1] != b"blob" or not parts[2].isdigit():
            raise ValueError("invalid Git object")
        sizes.append(int(parts[2]))
    if any(size > MAX_FILE for size in sizes) or sum(sizes) > MAX_TOTAL:
        raise ValueError("Git blobs exceed size limit")
    # Each object adds its header, a newline after the header, and a newline after its content.
    limit = sum(sizes) + sum(
        len(oid) + len(str(size)) + 8
        for (_, oid), size in zip(entries.values(), sizes, strict=True)
    )
    content = _git(root, "cat-file", "--batch", data=ids, limit=limit)
    offset = 0
    files = {}
    for (path, (mode, oid)), size in zip(entries.items(), sizes, strict=True):
        header = oid + b" blob " + str(size).encode() + b"\n"
        if content[offset : offset + len(header)] != header:
            raise ValueError("invalid Git batch output")
        offset += len(header)
        blob = content[offset : offset + size]
        offset += size
        if len(blob) != size or content[offset : offset + 1] != b"\n":
            raise ValueError("truncated Git blob")
        offset += 1
        if blob.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
            raise ValueError(f"unresolved Git LFS pointer: {path}")
        files[path] = (mode, blob)
    if offset != len(content):
        raise ValueError("unexpected Git batch output")
    return files
