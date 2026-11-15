"""Validated project contracts and immutable Git input snapshots."""

import hashlib
import os
import re
import selectors
import subprocess
import time
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evidence import decode_json

CHECKS_PREFIX = "{checks}/"
_MAX_FILE = 1_000_000
_MAX_TOTAL = 32_000_000
_MAX_FILES = 4096
_MAX_PATH = 512
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
_IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[0-9a-f]{64}\Z")


def _path(value: str) -> str:
    if (
        not value
        or len(value) > _MAX_PATH
        or "\\" in value
        or any(ord(c) < 32 for c in value)
        or any(p.casefold() in ("", ".", "..", ".git") for p in value.split("/"))
        or str(PurePosixPath(value)) != value
    ):
        raise ValueError(f"unsafe relative path: {value!r}")
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Claim(_Model):
    id: str
    statement: str = Field(min_length=1, max_length=4096)
    assumptions: list[str] = Field(default_factory=list, max_length=128)


class Formal(_Model):
    id: str
    claim: str
    engine: Literal["z3", "lean"]
    source: str
    expected: Literal["sat", "unsat"] | None = None
    counterexample: str | None = None
    theorem: str | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.engine == "z3":
            if self.expected is None or "theorem" in self.model_fields_set:
                raise ValueError("z3 requires expected and forbids theorem")
            if (self.expected == "unsat") != (self.counterexample is not None) or (
                self.expected == "sat" and "counterexample" in self.model_fields_set
            ):
                raise ValueError("unsat requires a counterexample; sat forbids one")
        elif {"expected", "counterexample"} & self.model_fields_set or not self.theorem:
            raise ValueError("lean requires theorem and forbids z3 fields")
        return self


class Behavior(_Model):
    id: str
    claim: str
    argv: list[str] = Field(min_length=1, max_length=128)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    violation_exit_codes: list[int] = Field(
        default_factory=lambda: [1], min_length=1, max_length=124
    )
    artifacts: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("violation_exit_codes")
    @classmethod
    def codes(cls, value):
        if any(not 1 <= code <= 124 for code in value) or len(set(value)) != len(value):
            raise ValueError("invalid violation exit codes")
        return value

    @field_validator("artifacts")
    @classmethod
    def artifact_names(cls, value):
        if len(set(value)) != len(value) or any(not _NAME.fullmatch(name) for name in value):
            raise ValueError("artifact names must be distinct flat filenames")
        return value


class Contract(_Model):
    version: Literal[1]
    claims: list[Claim] = Field(min_length=1, max_length=128)
    formal: list[Formal] = Field(min_length=1, max_length=128)
    behavior: list[Behavior] = Field(min_length=1, max_length=128)
    docker_image: str
    allow_host: bool = False

    @field_validator("version", mode="before")
    @classmethod
    def version_number(cls, value):
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value

    @model_validator(mode="after")
    def relationships(self):
        if not _IMAGE.fullmatch(self.docker_image):
            raise ValueError("docker image must be pinned by sha256 digest")
        ids = [item.id for group in (self.claims, self.formal, self.behavior) for item in group]
        if any(not _NAME.fullmatch(i) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("IDs must be safe and unique")
        claims = {item.id for item in self.claims}
        formal = {item.claim for item in self.formal}
        behavior = {item.claim for item in self.behavior}
        if not formal <= claims or behavior != claims or not formal & behavior:
            raise ValueError("checks must reference claims and every claim needs behavior")
        if any(not s or len(s) > 4096 for claim in self.claims for s in claim.assumptions):
            raise ValueError("invalid assumption")
        return self


@dataclass(frozen=True)
class Snapshot:
    revision: str
    digest: str
    files: dict[str, tuple[int, bytes]]


@dataclass(frozen=True)
class Project:
    root: Path
    base: str
    runner: str
    contract: Contract
    files: dict[str, bytes]
    fingerprint: str


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


def _git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            input=data,
            capture_output=True,
            env=_git_env(),
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"git command failed: {args[0]}") from exc
    return result.stdout


def _ls_tree(
    root: Path, revision: str, flags: str, scope: list[bytes], deadline: float
) -> Iterator[bytes]:
    # Match the stored path bytes even on filesystems where Git normally precomposes Unicode.
    try:
        with (
            subprocess.Popen(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "core.precomposeUnicode=false",
                    "ls-tree",
                    flags,
                    "--full-tree",
                    revision,
                    "--",
                    *scope,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_git_env(),
            ) as process,
            selectors.DefaultSelector() as selector,
        ):
            assert process.stdout is not None
            selector.register(process.stdout, selectors.EVENT_READ)
            pending = b""
            try:
                while (remaining := deadline - time.monotonic()) > 0 and selector.select(remaining):
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        if pending or process.wait(timeout=max(0, deadline - time.monotonic())):
                            raise ValueError("git command failed: ls-tree")
                        return
                    *records, pending = (pending + chunk).split(b"\0")
                    yield from records
                    if len(pending) > 4 * _MAX_PATH + 100:
                        raise ValueError("Git tree path exceeds size limit")
                raise ValueError("git command timed out: ls-tree")
            finally:
                if process.poll() is None:
                    process.kill()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("git command failed: ls-tree") from exc


def _tree_records(root: Path, revision: str, *, approved: bool) -> Iterator[bytes]:
    deadline = time.monotonic() + 60
    if approved:
        yield from _ls_tree(root, revision, "-rtz", [b".mentat"], deadline)
        return

    # ls-tree accepts literal paths but cannot exclude a subtree by pathspec.
    with closing(_ls_tree(root, revision, "-z", [], deadline)) as roots:
        scope = []
        size = 0
        for record in roots:
            raw_path = record.split(b"\t", 1)[1]
            if raw_path == b".mentat":
                continue
            if b"/" in raw_path:
                raise ValueError("invalid Git root entry")
            _git_path(raw_path)
            pathspec = b":(literal)" + raw_path
            weight = len(pathspec) + 9
            if size + weight > 32_768:
                yield from _ls_tree(root, revision, "-rtz", scope, deadline)
                scope = []
                size = 0
            scope.append(pathspec)
            size += weight
        if scope:
            yield from _ls_tree(root, revision, "-rtz", scope, deadline)


def _git_path(raw_path: bytes) -> str:
    try:
        return _path(raw_path.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("non-UTF-8 tree path") from exc


def _root(repo: Path) -> Path:
    return Path(
        os.fsdecode(_git(repo, "rev-parse", "--show-toplevel").removesuffix(b"\n"))
    ).resolve()


def _revision(root: Path, rev: str) -> str:
    fmt = _git(root, "rev-parse", "--show-object-format").strip()
    length = {b"sha1": 40, b"sha256": 64}.get(fmt)
    if length is None or not re.fullmatch(rf"[0-9a-f]{{{length}}}", rev):
        raise ValueError("revision must be a full commit ID")
    result = (
        _git(root, "rev-parse", "--verify", "--end-of-options", rev + "^{commit}")
        .strip()
        .decode("ascii")
    )
    if result != rev:
        raise ValueError("invalid commit ID")
    return result


def _tree_entries(records: Iterable[bytes]) -> dict[str, tuple[int, bytes]]:
    entries = {}
    # Git lists each tree before its children and orders siblings by name.
    parents = [(b"", b"")]
    for record in records:
        meta, raw_path = record.split(b"\t", 1)
        mode, kind, oid = meta.split(b" ")
        path = _git_path(raw_path)
        parent, _, name = raw_path.rpartition(b"/")
        while len(parents) > 1 and parents[-1][0] != parent:
            parents.pop()
        directory = mode == b"040000" and kind == b"tree"
        order = name + b"/" if directory else name
        if path in entries or order == parents[-1][1]:
            raise ValueError(f"duplicate Git tree path: {path}")
        if parent != parents[-1][0] or order < parents[-1][1]:
            raise ValueError(f"invalid Git tree order: {path}")
        parents[-1] = (parent, order)
        if directory:
            parents.append((raw_path, b""))
            continue
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise ValueError(f"unsupported Git entry: {path}")
        entries[path] = (int(mode, 8) & 0o777, oid)
        if len(entries) > _MAX_FILES:
            raise ValueError("too many files")
    return entries


def _tree(root: Path, revision: str, *, approved: bool) -> dict[str, tuple[int, bytes]]:
    with closing(_tree_records(root, revision, approved=approved)) as listing:
        entries = _tree_entries(listing)
    if not entries:
        return {}
    ids = b"".join(oid + b"\n" for _, oid in entries.values())
    headers = _git(
        root, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)", data=ids
    ).splitlines()
    if len(headers) != len(entries):
        raise ValueError("incomplete Git objects")
    sizes = []
    for (_, oid), header in zip(entries.values(), headers, strict=True):
        parts = header.split()
        if len(parts) != 3 or parts[0] != oid or parts[1] != b"blob" or not parts[2].isdigit():
            raise ValueError("invalid Git object")
        sizes.append(int(parts[2]))
    if any(size > _MAX_FILE for size in sizes) or sum(sizes) > _MAX_TOTAL:
        raise ValueError("Git blobs exceed size limit")
    content = _git(root, "cat-file", "--batch", data=ids)
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


def _digest(files: dict[str, tuple[int, bytes]]) -> str:
    digest = hashlib.sha256()
    for path, (mode, content) in sorted(files.items()):
        for item in (path.encode(), str(mode).encode(), content):
            digest.update(len(item).to_bytes(8, "big"))
            digest.update(item)
    return digest.hexdigest()


def snapshot(root: Path, rev: str) -> Snapshot:
    root = _root(root)
    revision = _revision(root, rev)
    files = _tree(root, revision, approved=False)
    return Snapshot(revision, _digest(files), files)


def load_project(repo: Path, base: str, runner: str) -> Project:
    root = _root(repo)
    revision = _revision(root, base)
    tree = _tree(root, revision, approved=True)
    files = {
        p[len(".mentat/") :]: data for p, (_, data) in tree.items() if p.startswith(".mentat/")
    }
    if "contract.json" not in files:
        raise ValueError("base has no .mentat/contract.json")
    contract = Contract.model_validate(decode_json(files["contract.json"]))
    if runner not in ("docker", "host") or (runner == "host" and not contract.allow_host):
        raise ValueError("runner is not allowed by contract")
    for check in contract.formal:
        for name in (check.source, check.counterexample):
            if name is not None and _path(name) not in files:
                raise ValueError(f"missing approved file: {name}")
    for check in contract.behavior:
        if not check.argv[0]:
            raise ValueError("behavior command must not be empty")
        for arg in check.argv:
            if len(arg) > 4096 or "\x00" in arg:
                raise ValueError("invalid behavior argument")
            if (
                arg.startswith(CHECKS_PREFIX)
                and _path(arg.removeprefix(CHECKS_PREFIX)) not in files
            ):
                raise ValueError("invalid approved checks path")
    verifier = {p.name: (0o644, p.read_bytes()) for p in Path(__file__).parent.glob("*.py")}
    binding = hashlib.sha256()
    for value in (
        str(root),
        revision,
        runner,
        _digest({p: (0o644, v) for p, v in files.items()}),
        _digest(verifier),
    ):
        data = value.encode()
        binding.update(len(data).to_bytes(8, "big"))
        binding.update(data)
    return Project(root, revision, runner, contract, files, binding.hexdigest())
