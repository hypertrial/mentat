"""Typed views of saved approval, acceptance, source-qualification, run and proof records.

Models validate; the raw parsed dict stays the carrier. Writers, hashes and comparisons use
the raw dict, and callers keep their own messages by catching pydantic.ValidationError.
Models carry no string constraints (non-empty, pattern and membership checks stay as code in
evidence), so strings that JSON can carry, such as lone surrogates, get the same outcome as before.
The one exception is the frozen contract assurance shape (the Assurance section): its string
constraints are the contract's own, and removing them would change which records load.
Imports stay limited to the standard library, pydantic and canonical.
"""

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)


class _Record(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", hide_input_in_errors=True)


def check(model: type[BaseModel], value: object) -> None:
    """Validate the declared fields of `value` against `model`; raise pydantic.ValidationError.

    Only declared keys are handed to pydantic. Records may carry any other JSON key, and
    pydantic refuses some keys the old hand checks ignored (for example a lone surrogate).
    """
    if isinstance(value, dict):
        value = {key: value[key] for key in model.model_fields if key in value}
    model.model_validate(value)


class Approval(_Record):
    """An operator's approval of one contract for one repository, base and runner."""

    repo: StrictStr
    base: StrictStr
    runner: StrictStr
    contract_digest: StrictStr
    approved_at: StrictStr
    # Any JSON value is accepted today. A non-string value is not shown at all, and only a
    # non-empty string is labelled; a string type would reject values that are accepted now.
    decision_source: Any = None


class Acceptance(_Record):
    """An operator's local acceptance of one saved run."""

    run_id: StrictStr
    manifest_digest: StrictStr
    revision: StrictStr
    contract_digest: StrictStr
    accepted_at: StrictStr
    # Same rule as Approval.decision_source.
    decision_source: Any = None


class SourceMutationCheck(_Record):
    """One declared source mutation in a run's source_mutation_checks."""

    id: StrictStr
    claim: StrictStr
    check: StrictStr
    expected_case: StrictStr
    required_cases: Annotated[list[StrictStr], Field(min_length=1)]
    violation_exit_codes: Annotated[
        list[Annotated[StrictInt, Field(ge=1, le=124)]], Field(min_length=1)
    ]


class SourceQualification(_Record):
    """One source mutation result in a run's source_qualifications."""

    schema_version: Annotated[StrictInt, Field(ge=1, le=1)]
    id: StrictStr
    claim: StrictStr
    check: StrictStr
    expected_case: StrictStr
    outcome: StrictStr
    status: StrictStr
    detail: StrictStr
    applied_count: Annotated[StrictInt, Field(ge=0, le=1)]
    assertion_failed: StrictBool | None = None
    bindings: dict[Any, Any]


# The contract's assurance shape as saved, frozen here rather than imported from project.
# Unknown keys stay rejected and messages keep their old text, so the config is the contract's.
# The string constraints below are kept on purpose: the contract refused some strings through
# them, and removing them would change which saved records load.
_MAX_PATH = 512  # gitio.MAX_PATH when the shape was frozen
_OBJECT_ID = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")


def _safe_relative_path(value: str) -> str:
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


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ClaimCitation(_Frozen):
    commit: str = Field(pattern=_OBJECT_ID)
    blob: str = Field(pattern=_OBJECT_ID)
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def valid_reference(self):
        _safe_relative_path(self.path)
        if len(self.commit) != len(self.blob) or self.end_line < self.start_line:
            raise ValueError("invalid citation object IDs or line range")
        return self


class EvidenceRef(_Frozen):
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


class Bound(_Frozen):
    id: str
    statement: str = Field(pattern=r"\S", max_length=4096)
    # Literal keeps the old "Input should be" message for this one closed set.
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


class Exclusion(_Frozen):
    scenario: str = Field(pattern=r"\S", max_length=4096)
    reason: str = Field(pattern=r"\S", max_length=4096)
    effect: str = Field(pattern=r"\S", max_length=4096)
    affected_claims: list[str] = Field(min_length=1, max_length=128)


class Deviation(_Frozen):
    citation: ClaimCitation
    rationale: str = Field(pattern=r"\S", max_length=4096)
    effect: str = Field(pattern=r"\S", max_length=4096)


class Assurance(_Frozen):
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


# Saved run records (kind "project-verification", schema 2). Non-empty and membership checks
# for these fields are in evidence.validate_run_record.


class RunArtifacts(_Record):
    # Keys and values are checked as code: pydantic would refuse some keys the old checks accepted.
    artifacts: dict[Any, Any]


class Claim(_Record):
    id: StrictStr
    statement: StrictStr
    assumptions: list[StrictStr] = []


class ReviewGroup(_Record):
    title: StrictStr
    claim_ids: list[StrictStr]


class RunInventory(_Record):
    behavior_skipped: StrictBool
    model_checks: list[Any]
    cases: list[Any]


# Each model covers one group of fields, in the order the checks have always run. Membership
# checks (claim, status, kind) run as code before the first group, and each group's defect is
# reported before the next group's, as before.


class FormalCheck(_Record):
    detail: StrictStr
    artifacts: dict[Any, Any] = {}
    id: StrictStr
    claim: Any
    status: Any
    kind: Any


class BehaviorCheck(_Record):
    detail: StrictStr
    artifacts: dict[Any, Any] = {}
    id: StrictStr
    claim: Any
    status: Any


class NegativeControl(_Record):
    detail: StrictStr
    qualified: StrictBool = False
    artifacts: dict[Any, Any] = {}
    status: Any


class References(_Record):
    """The artifact references of a result, control or projected qualification."""

    artifacts: dict[Any, Any] = {}


class ArtifactOmissions(_Record):
    artifact_omissions: list[StrictStr] = []


class LogOmissions(_Record):
    log_omissions: list[StrictStr] = []


class RequiredCases(_Record):
    required_cases: list[StrictStr] = []


class CaseResults(_Record):
    case_results: dict[Any, Any] = {}


class FormalEvidence(_Record):
    evidence: dict[Any, Any] = {}


class ExitCode(_Record):
    exit_code: StrictInt | None = None


class BehaviorCase(_Record):
    checks: list[Any] = Field(min_length=1)


class RunRecord(_Record):
    """The top level of a saved run, as a typed view. Sections are checked by evidence."""

    kind: StrictStr
    schema_version: Annotated[StrictInt, Field(ge=2, le=2)]
    run_id: StrictStr
    revision: StrictStr
    base: StrictStr
    contract_digest: StrictStr
    runner: StrictStr
    status: StrictStr
    artifacts: dict[Any, Any]
    claims: list[Any]
    claim_review: Any = None
    behavior_skipped: StrictBool
    model_checks: list[Any]
    cases: list[Any]


class ClaimId(_Record):
    id: StrictStr


class CheckStatus(_Record):
    id: StrictStr
    status: StrictStr


class CaseChecks(_Record):
    checks: list[Any]


class QualificationContext(_Record):
    """What a source qualification is checked against. Items and statuses are checked as code."""

    claims: list[Any]
    behavior_skipped: StrictBool
    cases: list[Any]


class QualificationRun(_Record):
    """Run fields a source qualification binds to; presence is checked here, values as code."""

    run_id: Any
    revision: Any
    snapshot_digest: Any
    base: Any
    contract_digest: Any
    runner: Any
    runtime: dict[Any, Any]
    comparison_inputs: dict[Any, Any]
    source_mutation_manifest_sha256: StrictStr


# Proof campaign manifests, ledger entries, checker results and saved proof checks. Only type
# shapes are modelled: hashes, identities, the ledger hash chain, budgets and every comparison
# stay in proof and evidence, on the raw dicts. Items of lists and dicts are checked as code.


class ProofManifest(_Record):
    """The fields of a proof campaign manifest that proof reads before it trusts the manifest."""

    repo: StrictStr
    base: StrictStr
    runner: StrictStr
    revision: StrictStr
    contract_digest: StrictStr
    verifier_digest: StrictStr
    snapshot_digest: StrictStr
    approval: dict[Any, Any]
    checker_identity: dict[Any, Any]
    targets: list[Any]
    all_targets: list[Any]
    obligations: list[Any]


class ProofTarget(_Record):
    """One approved target in a manifest's all_targets."""

    id: StrictStr
    approved_sha256: StrictStr


class LedgerEntry(_Record):
    """A ledger entry as it is read from disk, before its place in the chain is known."""

    previous: StrictStr


class LedgerAttempt(_Record):
    attempt: StrictStr


class LedgerReserve(_Record):
    target: StrictStr


class LedgerCompletion(_Record):
    logs: dict[Any, Any]


class CheckerResult(_Record):
    """The status and logs of a checker result; detail and identity are checked as code."""

    status: StrictStr
    logs: dict[Any, Any]


class ProofCheck(_Record):
    """A saved lean-comparator check; only its evidence field is typed here."""

    evidence: dict[Any, Any] = {}
