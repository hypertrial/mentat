"""Git reads for verifier inputs: one environment and argument policy, bounded output and time."""

import functools
import hashlib
import os
import re
import selectors
import subprocess
import tempfile
import time
from collections.abc import Generator, Iterable
from contextlib import nullcontext
from pathlib import Path

from . import process

MAX_FILE = 1_000_000
MAX_TOTAL = 32_000_000
MAX_FILES = 4096
MAX_PATH = 512
# Tree objects read to check a listing: a count and a byte budget across all directory levels.
MAX_TREES = 8192
MAX_TREE_BYTES = 16_000_000
# Long enough for a repository path or an object name from rev-parse.
_LINE = 4096
_INTEGRITY = "Git object failed integrity validation"
_KINDS = (b"blob", b"tree", b"commit", b"tag")
_HASHES = {40: "sha1", 64: "sha256"}
_LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"
_MAX_COMMIT = 1_000_000
_MODE = re.compile(rb"[0-7]{5,6}")


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
            source.write(data)  # pyright: ignore[reportArgumentType]  # data is not None whenever source is opened
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
) -> Generator[bytes, None, None]:
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


def _objects(
    root: Path, oids: list[bytes], max_size: int, max_total: int
) -> list[tuple[bytes, bytes]]:
    """Return (type, content) for each object ID, with each content checked against its ID.

    Git's batch reads return stored bytes without this check, so the check is done here.
    """
    if not oids:
        return []
    ids = b"".join(oid + b"\n" for oid in oids)
    # Each object adds a header, a newline after it and a newline after its content. The header
    # allowance covers the longest kind and a size of up to 20 digits.
    limit = max_total + sum(len(oid) + len(b"commit") + 20 + 4 for oid in oids)
    try:
        content = run(root, "cat-file", "--batch", data=ids, limit=limit)
    except ValueError as exc:
        raise ValueError("Git objects exceed size limit") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("git command failed: cat-file") from exc
    parsed = []
    offset = 0
    for oid in oids:
        end = content.find(b"\n", offset)
        if end < 0:
            raise ValueError("truncated Git object")
        parts = content[offset:end].split()
        if (
            len(parts) != 3
            or parts[0] != oid
            or parts[1] not in _KINDS
            or not (parts[2].isdigit() and len(parts[2]) <= 20)
        ):
            raise ValueError("invalid Git object")
        kind, size = parts[1], int(parts[2])
        offset = end + 1
        body = content[offset : offset + size]
        offset += size
        if len(body) != size or content[offset : offset + 1] != b"\n":
            raise ValueError("truncated Git object")
        offset += 1
        parsed.append((kind, size, body))
    if offset != len(content):
        raise ValueError("unexpected Git batch output")
    sizes = [size for _, size, _ in parsed]
    if any(size > max_size for size in sizes) or sum(sizes) > max_total:
        raise ValueError("Git objects exceed size limit")
    objects = []
    for oid, (kind, size, body) in zip(oids, parsed, strict=True):
        digest = hashlib.new(_HASHES[len(oid)], b"%s %d\0" % (kind, size) + body)
        if digest.hexdigest().encode() != oid:
            raise ValueError(_INTEGRITY)
        objects.append((kind, body))
    return objects


def _commit_tree(root: Path, revision: str) -> bytes:
    kind, body = _objects(root, [revision.encode()], _MAX_COMMIT, _MAX_COMMIT)[0]
    match = re.match(rb"tree ([0-9a-f]{40}|[0-9a-f]{64})\n", body)
    if kind != b"commit" or match is None:
        raise ValueError("invalid commit ID")
    return match.group(1)


def _rewritten(root: Path, rev: str) -> bool:
    try:
        _commit_tree(root, rev)
    except ValueError as exc:
        return str(exc) == _INTEGRITY
    return False


@functools.lru_cache(maxsize=64)
def _object_format(root: Path) -> bytes:
    # A repository's object format is fixed when it is created, so only that is cached, by root.
    return _git(root, "rev-parse", "--show-object-format", limit=_LINE).strip()


def verify_revision(root: Path, rev: str) -> str:
    fmt = _object_format(root.resolve())
    length = {b"sha1": 40, b"sha256": 64}.get(fmt)
    if length is None or not re.fullmatch(rf"[0-9a-f]{{{length}}}", rev):
        raise ValueError("revision must be a full commit ID")
    try:
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
    except ValueError as exc:
        # rev-parse fails on a commit whose content does not match its ID. Report that case as an
        # integrity failure and keep the original error for anything else.
        if _rewritten(root, rev):
            raise ValueError(_INTEGRITY) from exc
        raise
    if result != rev:
        raise ValueError("invalid commit ID")
    return result


def read_blobs(
    root: Path,
    entries: dict[str, tuple[int, bytes]],
    *,
    max_file: int | None = None,
    max_total: int | None = None,
) -> dict[str, tuple[int, bytes]]:
    if not entries:
        return {}
    objects = _objects(
        root,
        [oid for _, oid in entries.values()],
        MAX_FILE if max_file is None else max_file,
        MAX_TOTAL if max_total is None else max_total,
    )
    files = {}
    for (path, (mode, _)), (kind, blob) in zip(entries.items(), objects, strict=True):
        if kind != b"blob":
            raise ValueError("invalid Git object")
        if blob.startswith(_LFS_POINTER):
            raise ValueError(f"unresolved Git LFS pointer: {path}")
        files[path] = (mode, blob)
    return files


def _parse_tree(raw: bytes, width: int) -> dict[bytes, tuple[bytes, bytes, bytes]]:
    """Map each name in a raw tree to (mode, type, object ID), with the mode as ls-tree prints it."""
    rows = []
    offset = 0
    try:
        while offset < len(raw):
            space = raw.index(b" ", offset)
            nul = raw.index(b"\0", space)
            end = nul + 1 + width
            if end > len(raw):
                raise ValueError("truncated tree entry")
            if not _MODE.fullmatch(raw[offset:space]):
                raise ValueError("invalid tree mode")
            mode = b"%06o" % int(raw[offset:space], 8)
            kind = {b"040000": b"tree", b"160000": b"commit"}.get(mode, b"blob")
            rows.append((raw[space + 1 : nul], (mode, kind, raw[nul + 1 : end].hex().encode())))
            offset = end
    except ValueError as exc:
        raise ValueError(_INTEGRITY) from exc
    entries = dict(rows)
    # A well-formed tree can still repeat a name, which Git tooling rejects as ambiguous.
    if len(entries) != len(rows):
        raise ValueError("duplicate Git tree path")
    return entries


def _load_trees(
    root: Path, tree: bytes, directories: Iterable[bytes], listed: dict[bytes, bytes]
) -> dict[bytes, dict[bytes, tuple[bytes, bytes, bytes]]]:
    """Return entries of verified trees keyed by path (b"" is the root), including ancestors.

    `listed` maps the paths of tree records to their IDs. Those trees are read in the first
    batch, and each is trusted only once the caller checks its record against its parent entry.
    Trees that no listing names are reached level by level from the root.
    """
    wanted = set()
    for directory in directories:
        parts = directory.split(b"/") if directory else []
        wanted.update(b"/".join(parts[:index]) for index in range(1, len(parts) + 1))
    loaded = {}
    level = {b"": tree, **listed}
    total = 0
    while level:
        if len(loaded) + len(level) > MAX_TREES:
            raise ValueError("too many Git trees")
        paths = list(level)
        objects = _objects(
            root,
            [level[path] for path in paths],
            MAX_TREE_BYTES,
            MAX_TREE_BYTES - total,
        )
        next_level = {}
        for path, (kind, raw) in zip(paths, objects, strict=True):
            if kind != b"tree":
                raise ValueError(_INTEGRITY)
            total += len(raw)
            loaded[path] = _parse_tree(raw, len(level[path]) // 2)
            for name, (_, child_kind, oid) in loaded[path].items():
                child = path + b"/" + name if path else name
                if child_kind == b"tree" and child in wanted and child not in listed:
                    next_level[child] = oid
        level = next_level
    return loaded


def _in_scope(path: bytes, scopes: set[bytes]) -> bool:
    parts = path.split(b"/")
    return any(b"/".join(parts[:index]) in scopes for index in range(1, len(parts) + 1))


def verify_listing(
    root: Path,
    revision: str,
    records: list[bytes],
    scopes: set[bytes],
    top: list[bytes] | None = None,
) -> None:
    """Check ls-tree records against the verified tree objects they describe.

    `records` are recursive listings of the paths in `scopes`, and `top` is the root's
    non-recursive listing. Each record must equal its entry in the verified parent tree. A
    listed tree inside a scope must list exactly its verified children, and the root must list
    exactly its verified entries when `top` is given. Raises ValueError on any mismatch.
    """
    items = []
    # A scope is checked even when Git omits its own record, so omitted children cannot hide.
    covered = set(scopes)
    listed = {}
    for record in records:
        meta, path = record.split(b"\t", 1)
        mode, kind, oid = meta.split(b" ")
        parent, _, name = path.rpartition(b"/")
        items.append((parent, name, (mode, kind, oid)))
        if kind == b"tree":
            listed[path] = oid
            if _in_scope(path, scopes):
                covered.add(path)
    for record in top or []:
        meta, path = record.split(b"\t", 1)
        mode, kind, oid = meta.split(b" ")
        parent, _, name = path.rpartition(b"/")
        items.append((parent, name, (mode, kind, oid)))
    if top is not None:
        covered.add(b"")
    tree = _commit_tree(root, revision)
    loaded = _load_trees(root, tree, {parent for parent, _, _ in items} | covered, listed)
    names: dict[bytes, set[bytes]] = {}
    for parent, name, entry in items:
        if loaded.get(parent, {}).get(name) != entry:
            raise ValueError(_INTEGRITY)
        names.setdefault(parent, set()).add(name)
    for directory in covered:
        if set(loaded.get(directory, {})) != names.get(directory, set()):
            raise ValueError(_INTEGRITY)


def read_file(root: Path, revision: str, path: str) -> bytes:
    """Return one committed regular file, read through its verified parent tree."""
    directory, _, name = path.rpartition("/")
    loaded = _load_trees(root, _commit_tree(root, revision), [directory.encode()], {})
    entry = loaded.get(directory.encode(), {}).get(name.encode())
    if entry is None or entry[0] not in (b"100644", b"100755"):
        raise ValueError(f"missing Git file: {path}")
    return read_blobs(root, {path: (int(entry[0], 8) & 0o777, entry[2])})[path][1]
