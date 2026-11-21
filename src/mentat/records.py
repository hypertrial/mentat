"""Typed views of saved approval, acceptance and source-qualification records.

Models validate; the raw parsed dict stays the carrier. Writers, hashes and comparisons use
the raw dict, and callers keep their own messages by catching pydantic.ValidationError.
Imports stay limited to the standard library, pydantic and canonical.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


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
    bindings: dict[str, Any]
