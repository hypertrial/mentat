"""Validated project contracts and immutable Git input snapshots."""

import hashlib
import os
import re
import selectors
import subprocess
import time
import unicodedata
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evidence import decode_json

CHECKS_PREFIX = "{checks}/"
# Presentation modules stay out of the verifier digest, and only cli may import them.
PRESENTATION_MODULES = ("evidence_html", "claims_notion", "openspec")
# Every other module directly under src/mentat must be listed here; tests enforce this.
VERIFIER_MODULES = (
    "__init__",
    "claims",
    "cli",
    "colima",
    "command",
    "evidence",
    "evidence_report",
    "project",
    "project_formal",
    "proof",
    "proof_checker",
    "proof_lean",
    "qualification",
    "runner",
    "spec_review",
)
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


class ClaimCitation(_Model):
    commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    blob: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def valid_reference(self):
        _path(self.path)
        if len(self.commit) != len(self.blob) or self.end_line < self.start_line:
            raise ValueError("invalid citation object IDs or line range")
        return self


class ClaimProvenance(_Model):
    origin: Literal["declared", "inferred"]
    citations: list[ClaimCitation] = Field(min_length=1, max_length=16)


class EvidenceRef(_Model):
    check_id: str
    case_ids: list[str] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def valid_reference(self):
        if (
            not _NAME.fullmatch(self.check_id)
            or len(set(self.case_ids)) != len(self.case_ids)
            or any(
                not case or len(case) > 256 or any(unicodedata.category(c) == "Cc" for c in case)
                for case in self.case_ids
            )
        ):
            raise ValueError("invalid evidence reference")
        return self


class Bound(_Model):
    id: str
    statement: str = Field(pattern=r"\S", max_length=4096)
    kind: Literal["enforced", "assumed"]
    checks: list[EvidenceRef] = Field(default_factory=list, max_length=128)
    assumption: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def valid_bound(self):
        if not _NAME.fullmatch(self.id):
            raise ValueError("invalid bound ID")
        if self.kind == "enforced":
            if not self.checks or "assumption" in self.model_fields_set:
                raise ValueError("enforced bounds require checks and forbid an assumption")
        elif self.checks or not self.assumption:
            raise ValueError("assumed bounds require an assumption and forbid checks")
        return self


class Exclusion(_Model):
    scenario: str = Field(pattern=r"\S", max_length=4096)
    reason: str = Field(pattern=r"\S", max_length=4096)
    effect: str = Field(pattern=r"\S", max_length=4096)
    affected_claims: list[str] = Field(min_length=1, max_length=128)


class Deviation(_Model):
    citation: ClaimCitation
    rationale: str = Field(pattern=r"\S", max_length=4096)
    effect: str = Field(pattern=r"\S", max_length=4096)


class Assurance(_Model):
    implementation_checks: list[EvidenceRef] = Field(default_factory=list, max_length=128)
    witness_checks: list[EvidenceRef] = Field(default_factory=list, max_length=128)
    bounds: list[Bound] = Field(default_factory=list, max_length=128)
    exclusions: list[Exclusion] = Field(default_factory=list, max_length=128)
    deviations: list[Deviation] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def distinct_bounds(self):
        ids = [bound.id for bound in self.bounds]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate bound ID")
        return self


class Claim(_Model):
    id: str
    statement: str = Field(min_length=1, max_length=4096)
    assumptions: list[str] = Field(default_factory=list, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=256)
    provenance: ClaimProvenance | None = None
    assurance: Assurance | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("title")
    @classmethod
    def title_line(cls, value):
        if value is not None and (
            not value.strip() or len(value.splitlines()) != 1 or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("claim title must be a nonempty single line")
        return value


class ReviewExample(_Model):
    label: str = Field(min_length=1, max_length=256)
    summary: str = Field(pattern=r"\S", max_length=4096)
    url: str = Field(min_length=1, max_length=4096)

    @field_validator("label")
    @classmethod
    def label_line(cls, value):
        if not value.strip() or len(value.splitlines()) != 1 or any(ord(c) < 32 for c in value):
            raise ValueError("example label must be a nonempty single line")
        return value

    @field_validator("url")
    @classmethod
    def https_url(cls, value):
        try:
            url = urlsplit(value)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or any(c.isspace() or ord(c) < 32 for c in value)
                or "\\" in value
            ):
                raise ValueError("example URL must be an HTTPS URL")
            _ = url.port
        except ValueError:
            raise ValueError("example URL must be an HTTPS URL") from None
        return value


class ReviewGroup(_Model):
    title: str = Field(min_length=1, max_length=256)
    claim_ids: list[str] = Field(min_length=1, max_length=128)

    @field_validator("title")
    @classmethod
    def title_line(cls, value):
        if not value.strip() or len(value.splitlines()) != 1 or any(ord(c) < 32 for c in value):
            raise ValueError("group title must be a nonempty single line")
        return value.strip()


class Review(_Model):
    overview: str = Field(pattern=r"\S", max_length=4096)
    scope_limits: list[str] = Field(min_length=1, max_length=128)
    examples: list[ReviewExample] = Field(default_factory=list, max_length=128)
    groups: list[ReviewGroup] | None = Field(default=None, min_length=1, max_length=128)
    verification_limits: list[str] | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("groups", "verification_limits", mode="before")
    @classmethod
    def present_list(cls, value):
        if value is None:
            raise ValueError("must be a list when present")
        return value

    @model_validator(mode="after")
    def grouped_layout(self):
        if self.groups is None:
            if "verification_limits" in self.model_fields_set:
                raise ValueError("verification limits require groups")
            return self
        reserved = {
            "claims spec",
            "what the app does",
            "product limits",
            "examples",
            "verification coverage and limits",
            "coverage limits",
            "behavior checks",
            "formal checks and assumptions",
        }
        titles = [group.title.casefold() for group in self.groups]
        if len(set(titles)) != len(titles) or reserved.intersection(titles):
            raise ValueError("group titles must be unique and not collide with generated headings")
        return self

    @field_validator("scope_limits", "verification_limits")
    @classmethod
    def limits(cls, value):
        if value is not None and any(not item.strip() or len(item) > 4096 for item in value):
            raise ValueError("invalid scope limit")
        return value


class Formal(_Model):
    id: str
    claim: str
    engine: Literal["z3", "lean", "lean-comparator"]
    source: str
    expected: Literal["sat", "unsat"] | None = None
    counterexample: str | None = None
    theorem: str | None = None
    allowed_imports: list[str] | None = Field(
        default=None, max_length=128, exclude_if=lambda value: value is None
    )
    permitted_axioms: list[str] | None = Field(
        default=None, max_length=3, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def shape(self):
        policy = {"allowed_imports", "permitted_axioms"}
        if self.engine != "lean-comparator" and policy & self.model_fields_set:
            raise ValueError("Lean comparator policy is only valid for lean-comparator")
        if self.engine == "lean-comparator":
            from .project_formal import _NAME as lean_name

            if (
                {"expected", "counterexample"} & self.model_fields_set
                or not self.theorem
                or not policy <= self.model_fields_set
                or self.allowed_imports is None
                or self.permitted_axioms is None
            ):
                raise ValueError(
                    "lean-comparator requires theorem and explicit policy and forbids z3 fields"
                )
            if not lean_name.fullmatch(self.theorem):
                raise ValueError("lean-comparator requires a plain qualified Lean theorem name")
            if len(set(self.allowed_imports)) != len(self.allowed_imports) or any(
                not re.fullmatch(r"(?:Init|Std|Lean)(?:\.[A-Za-z_][A-Za-z0-9_']*)*", name)
                for name in self.allowed_imports
            ):
                raise ValueError("invalid allowed Lean imports")
            if len(set(self.permitted_axioms)) != len(self.permitted_axioms) or set(
                self.permitted_axioms
            ) - {"propext", "Classical.choice", "Quot.sound"}:
                raise ValueError("invalid permitted Lean axioms")
        elif self.engine == "z3":
            if self.expected is None or "theorem" in self.model_fields_set:
                raise ValueError("z3 requires expected and forbids theorem")
            if (self.expected == "unsat") != (self.counterexample is not None) or (
                self.expected == "sat" and "counterexample" in self.model_fields_set
            ):
                raise ValueError("unsat requires a counterexample; sat forbids one")
        elif {"expected", "counterexample"} & self.model_fields_set or not self.theorem:
            raise ValueError("lean requires theorem and forbids z3 fields")
        return self


class CaseReport(_Model):
    artifact: str
    required_cases: list[str] = Field(min_length=1, max_length=128)

    @field_validator("required_cases")
    @classmethod
    def case_ids(cls, value):
        if len(set(value)) != len(value) or any(
            not item or len(item) > 256 or any(unicodedata.category(c) == "Cc" for c in item)
            for item in value
        ):
            raise ValueError("case IDs must be distinct nonempty strings")
        return value


class NegativeControl(_Model):
    args: list[str] = Field(min_length=1, max_length=128)
    violation_exit_code: int = Field(ge=1, le=124)
    failing_case: str | None = None


class Behavior(_Model):
    id: str
    claim: str
    argv: list[str] = Field(min_length=1, max_length=128)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    violation_exit_codes: list[int] = Field(
        default_factory=lambda: [1], min_length=1, max_length=124
    )
    artifacts: list[str] = Field(default_factory=list, max_length=128)
    selectors: list[str] | None = Field(default=None, max_length=128)
    # Omit when empty so claim fingerprints match contracts written before this field.
    host_environment: list[str] = Field(
        default_factory=list, max_length=128, exclude_if=lambda value: not value
    )
    case_report: CaseReport | None = None
    negative_control: NegativeControl | None = None

    @model_validator(mode="after")
    def selector_arguments(self):
        if self.case_report and self.case_report.artifact not in self.artifacts:
            raise ValueError("case report must be a declared artifact")
        if self.negative_control:
            control = self.negative_control
            if control.violation_exit_code not in self.violation_exit_codes:
                raise ValueError("negative control exit code must be a violation exit code")
            if self.case_report:
                if control.failing_case not in self.case_report.required_cases:
                    raise ValueError("negative control must name a required failing case")
            elif control.failing_case is not None:
                raise ValueError("negative control failing case requires a case report")
            if "{selectors}" in control.args:
                raise ValueError("negative control arguments cannot reference staged paths")
        if self.selectors is None:
            return self
        if self.argv.count("{selectors}") != 1:
            raise ValueError("selectors require one standalone {selectors} argument")
        if any(
            not value or len(value) > 4096 or any(unicodedata.category(c) == "Cc" for c in value)
            for value in self.selectors
        ):
            raise ValueError("invalid selector")
        return self

    def effective_argv(self) -> list[str]:
        if self.selectors is None:
            return self.argv
        return [
            value
            for arg in self.argv
            for value in (self.selectors if arg == "{selectors}" else [arg])
        ]

    @field_validator("host_environment")
    @classmethod
    def environment_names(cls, value):
        if len(set(value)) != len(value) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            or name.upper() in ("PATH", "HOME")
            or name.upper().startswith("MENTAT_")
            for name in value
        ):
            raise ValueError("invalid, reserved, or duplicate host environment name")
        return value

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


class HostRequirements(_Model):
    executables: list[str] = Field(default_factory=list, max_length=128)
    python_distributions: list[str] = Field(default_factory=list, max_length=128)
    loopback: bool = False

    @field_validator("executables")
    @classmethod
    def executable_names(cls, value):
        if len(set(value)) != len(value) or any(
            name in ("", ".", "..", "/")
            or len(name) > _MAX_PATH
            or any(unicodedata.category(c) == "Cc" for c in name)
            or (
                "/" in name
                and (
                    not name.startswith("/")
                    or str(PurePosixPath(name)) != name
                    or any(part in (".", "..") for part in name.split("/"))
                )
            )
            or "\\" in name
            for name in value
        ):
            raise ValueError("executables must be bare names or absolute paths")
        return value

    @field_validator("python_distributions")
    @classmethod
    def distribution_names(cls, value):
        if len(set(value)) != len(value) or any(
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?", name)
            for name in value
        ):
            raise ValueError("invalid Python distribution name")
        return value


class Contract(_Model):
    version: Literal[1]
    claims: list[Claim] = Field(min_length=1, max_length=128)
    formal: list[Formal] = Field(min_length=1, max_length=128)
    behavior: list[Behavior] = Field(min_length=1, max_length=128)
    docker_image: str | None = None
    allow_host: bool = False
    review: Review | None = None
    base_files: list[str] = Field(default_factory=list, max_length=_MAX_FILES)
    host_requirements: HostRequirements | None = None
    source_mutations: str | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("base_files")
    @classmethod
    def base_file_paths(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate base file")
        for path in value:
            _path(path)
            if any(unicodedata.category(c) == "Cc" for c in path):
                raise ValueError("invalid base file path")
            if path.split("/", 1)[0].casefold() == ".mentat":
                raise ValueError("base files must be outside .mentat")
        return value

    @field_validator("version", mode="before")
    @classmethod
    def version_number(cls, value):
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value

    @model_validator(mode="after")
    def relationships(self):
        if not self.allow_host and any(
            "host_environment" in check.model_fields_set for check in self.behavior
        ):
            raise ValueError("host_environment requires allow_host")
        if self.review is not None and any(claim.title is None for claim in self.claims):
            raise ValueError("every claim requires a title when review is present")
        if self.docker_image is None:
            if not self.allow_host or "docker_image" in self.model_fields_set:
                raise ValueError("docker_image is required unless allow_host is true")
        elif not _IMAGE.fullmatch(self.docker_image):
            raise ValueError("docker image must be pinned by sha256 digest")
        ids = [item.id for group in (self.claims, self.formal, self.behavior) for item in group]
        if any(not _NAME.fullmatch(i) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("IDs must be safe and unique")
        claims = {item.id for item in self.claims}
        if self.review is not None and self.review.groups is not None:
            grouped_ids = [claim_id for group in self.review.groups for claim_id in group.claim_ids]
            if len(grouped_ids) != len(claims) or set(grouped_ids) != claims:
                raise ValueError("groups must include every claim ID exactly once")
        formal = {item.claim for item in self.formal}
        behavior = {item.claim for item in self.behavior}
        if not formal <= claims or behavior != claims or not formal & behavior:
            raise ValueError("checks must reference claims and every claim needs behavior")
        if any(not s or len(s) > 4096 for claim in self.claims for s in claim.assumptions):
            raise ValueError("invalid assumption")
        checks = {item.id: item for item in [*self.formal, *self.behavior]}
        for claim in self.claims:
            assurance = claim.assurance
            if not assurance:
                continue
            refs = [
                *assurance.implementation_checks,
                *assurance.witness_checks,
                *(ref for bound in assurance.bounds for ref in bound.checks),
            ]
            if any(
                not isinstance(checks.get(ref.check_id), Behavior)
                for ref in assurance.implementation_checks
            ):
                raise ValueError("implementation checks must reference behavior checks")
            for ref in refs:
                check = checks.get(ref.check_id)
                if check is None:
                    raise ValueError("assurance references an unknown check")
                if ref.case_ids and (
                    not isinstance(check, Behavior)
                    or not check.case_report
                    or not set(ref.case_ids) <= set(check.case_report.required_cases)
                ):
                    raise ValueError("assurance references unknown or unsupported cases")
            if any(
                bound.kind == "assumed" and bound.assumption not in claim.assumptions
                for bound in assurance.bounds
            ):
                raise ValueError("assumed bound must reference an existing claim assumption")
            for exclusion in assurance.exclusions:
                if (
                    len(set(exclusion.affected_claims)) != len(exclusion.affected_claims)
                    or not set(exclusion.affected_claims) <= claims
                ):
                    raise ValueError("exclusion references invalid claims")
            if any(
                claim.provenance is None or deviation.citation not in claim.provenance.citations
                for deviation in assurance.deviations
            ):
                raise ValueError("deviation must reference an existing claim citation")
        if self.source_mutations is not None:
            _path(self.source_mutations)
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
    verifier_digest: str


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


def _tree_entries(
    records: Iterable[bytes], *, allow_links: bool = False
) -> dict[str, tuple[int, bytes]]:
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
        if kind != b"blob" or (
            mode not in (b"100644", b"100755") and not (allow_links and mode == b"120000")
        ):
            raise ValueError(f"unsupported Git entry: {path}")
        entries[path] = (0o120000 if mode == b"120000" else int(mode, 8) & 0o777, oid)
        if len(entries) > _MAX_FILES:
            raise ValueError("too many files")
    return entries


def _tree(root: Path, revision: str, *, approved: bool) -> dict[str, tuple[int, bytes]]:
    with closing(_tree_records(root, revision, approved=approved)) as listing:
        entries = _tree_entries(listing, allow_links=not approved)
    return _read_blobs(root, entries)


def _read_blobs(root: Path, entries: dict[str, tuple[int, bytes]]) -> dict[str, tuple[int, bytes]]:
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


def _verifier_files() -> dict[str, tuple[int, bytes]]:
    files = {}
    for name in VERIFIER_MODULES:
        try:
            files[f"{name}.py"] = (0o644, (Path(__file__).parent / f"{name}.py").read_bytes())
        except FileNotFoundError as exc:
            raise ValueError("Verifier source file is missing") from exc
    try:
        files["z3-solver"] = (0o644, metadata.version("z3-solver").encode())
    except metadata.PackageNotFoundError as exc:
        raise ValueError("Install the project's z3-solver dependency.") from exc
    return files


def _validate_staged(files, *, trusted=False):
    entries = {}
    directories = {""}
    seen = {}
    for name, entry in files.items():
        _path(name)
        parts = name.split("/")
        mode, data = (0o644, entry) if trusted else entry
        if mode not in ((0o644, 0o755) if trusted else (0o644, 0o755, 0o120000)) or not isinstance(
            data, bytes
        ):
            raise ValueError("invalid staged file")
        entries[name] = (mode, data)
        for index in range(1, len(parts) + 1):
            spelling = "/".join(parts[:index])
            folded = unicodedata.normalize("NFD", spelling).casefold()
            if seen.setdefault(folded, spelling) != spelling:
                raise ValueError("staged paths collide on this filesystem")
            if index < len(parts):
                directories.add(spelling)
    if directories.intersection(entries):
        raise ValueError("staged file conflicts with directory")
    links = {}
    for name, (mode, data) in entries.items():
        if mode != 0o120000:
            continue
        try:
            target = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid symlink target") from exc
        if (
            not target
            or target.startswith("/")
            or "\\" in target
            or len(target) > 512
            or any(unicodedata.category(c) == "Cc" for c in target)
            or any(part.casefold() == ".mentat" for part in target.split("/"))
        ):
            raise ValueError("invalid symlink target")
        parts = name.split("/")[:-1]
        components = target.split("/")
        for index, part in enumerate(components):
            if part in ("", "."):
                pass
            elif part == "..":
                if not parts:
                    raise ValueError("symlink escapes candidate")
                parts.pop()
            else:
                parts.append(part)
            resolved = "/".join(parts)
            if index < len(components) - 1 and resolved not in directories:
                raise ValueError("symlink traverses non-directory")
        resolved = "/".join(parts)
        if (
            any(part.casefold() == ".mentat" for part in parts)
            or resolved not in entries
            or entries[resolved][0] not in (0o644, 0o755)
        ):
            raise ValueError("symlink target is not a tracked regular file")
        links[name] = target
    return entries, links


def snapshot(root: Path, rev: str) -> Snapshot:
    root = _root(root)
    revision = _revision(root, rev)
    files = _tree(root, revision, approved=False)
    if any(mode == 0o120000 for mode, _ in files.values()):
        _validate_staged(files)
    return Snapshot(revision, _digest(files), files)


def _selected_entries(
    root: Path, revision: str, scope: list[bytes], deadline: float, entries: dict
):
    with closing(_ls_tree(root, revision, "-z", scope, deadline)) as records:
        for record in records:
            meta, raw_path = record.split(b"\t", 1)
            mode, kind, oid = meta.split(b" ")
            path = _git_path(raw_path)
            if mode not in (b"100644", b"100755") or kind != b"blob" or path in entries:
                raise ValueError(f"unsupported base file: {path}")
            entries[path] = (int(mode, 8) & 0o777, oid)


def contract_inputs(repo: Path, base: str) -> tuple[Path, str, Contract, dict[str, bytes]]:
    root = _root(repo)
    revision = _revision(root, base)
    tree = _tree(root, revision, approved=True)
    files = {
        p[len(".mentat/") :]: data for p, (_, data) in tree.items() if p.startswith(".mentat/")
    }
    if "contract.json" not in files:
        raise ValueError("base has no .mentat/contract.json")
    contract = Contract.model_validate(decode_json(files["contract.json"]))
    if contract.base_files:
        if any(path.split("/", 1)[0].casefold() == "base" for path in files):
            raise ValueError(".mentat/base conflicts with base files")
        entries = {}
        deadline = time.monotonic() + 60
        scope = []
        weight = 0
        for path in contract.base_files:
            spec = b":(literal)" + path.encode("utf-8")
            if weight + len(spec) + 9 > 32_768:
                _selected_entries(root, revision, scope, deadline, entries)
                scope, weight = [], 0
            scope.append(spec)
            weight += len(spec) + 9
        _selected_entries(root, revision, scope, deadline, entries)
        if set(entries) != set(contract.base_files):
            raise ValueError("base file is absent or not a regular tracked file")
        selected = _read_blobs(root, entries)
        if (
            len(files) + len(selected) > _MAX_FILES
            or sum(map(len, files.values())) + sum(len(data) for _, data in selected.values())
            > _MAX_TOTAL
        ):
            raise ValueError("combined Git inputs exceed size limit")
        files.update({f"base/{path}": data for path, (_, data) in selected.items()})
    return root, revision, contract, files


def load_project(repo: Path, base: str, runner: str) -> Project:
    from .claims import render_claims

    root, revision, contract, files = contract_inputs(repo, base)
    if "claims.md" in files and files["claims.md"] != render_claims(contract):
        raise ValueError("stale .mentat/claims.md; run mentat claims render and commit it")
    if runner == "docker" and contract.docker_image is None:
        raise ValueError("contract allows only host execution; select --runner host")
    if runner == "docker" and any(check.host_environment for check in contract.behavior):
        raise ValueError("host_environment is not supported by the docker runner")
    if runner not in ("docker", "host") or (runner == "host" and not contract.allow_host):
        raise ValueError("runner is not allowed by contract")
    for check in contract.formal:
        for name in (check.source, check.counterexample):
            if name is not None and _path(name) not in files:
                raise ValueError(f"missing approved file: {name}")
    for check in contract.behavior:
        argv = check.effective_argv()
        if not argv or not argv[0]:
            raise ValueError("behavior command must not be empty")
        for arg in [*argv, *(check.negative_control.args if check.negative_control else [])]:
            if len(arg) > 4096 or "\x00" in arg:
                raise ValueError("invalid behavior argument")
            if (
                arg.startswith(CHECKS_PREFIX)
                and _path(arg.removeprefix(CHECKS_PREFIX)) not in files
            ):
                raise ValueError("invalid approved checks path")
    if contract.source_mutations is not None:
        if contract.source_mutations not in files or contract.source_mutations in {
            f"base/{path}" for path in contract.base_files
        }:
            raise ValueError("missing approved .mentat source mutations manifest")
        from .qualification import approved_mutations

        approved_mutations(contract, files)
    verifier_digest = _digest(_verifier_files())
    binding = hashlib.sha256()
    for value in (
        str(root),
        revision,
        runner,
        _digest({p: (0o644, v) for p, v in files.items()}),
        verifier_digest,
    ):
        data = value.encode()
        binding.update(len(data).to_bytes(8, "big"))
        binding.update(data)
    return Project(root, revision, runner, contract, files, binding.hexdigest(), verifier_digest)
