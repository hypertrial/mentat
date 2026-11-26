"""Shared validation of approval, saved evidence and acceptance eligibility."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from . import evidence, records
from .project import Project


def scope(project: Project) -> dict:
    return {
        "repo": str(project.root),
        "base": project.base,
        "runner": project.runner,
        "contract_digest": project.fingerprint,
    }


def approval_path(state: Path, project: Project) -> Path:
    return state / "approvals" / f"{project.fingerprint}.json"


def approval(state: Path, project: Project) -> dict:
    try:
        record = evidence.read_json(approval_path(state, project))
    except FileNotFoundError as exc:
        raise ValueError(
            "Approve this repository, base commit, and runner with mentat spec approve"
        ) from exc
    try:
        records.check(records.Approval, record)
    except ValidationError as exc:
        raise ValueError(
            "Approval record is invalid. Check and approve the current contract"
        ) from exc
    if any(record[key] != value for key, value in scope(project).items()):
        raise ValueError("Approval record is invalid. Check and approve the current contract")
    return record


@contextmanager
def saved_record() -> Iterator[None]:
    """Refuse a saved record whose shape breaks a read or a field access, naming no field."""
    try:
        yield
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid saved verification record") from exc


def saved_run_snapshot(state: Path, run_id: str) -> tuple[Path, dict, str]:
    """Read and validate a saved run and its manifest digest in one integrity pass."""
    with saved_record():
        bundle, record, manifest = evidence.read_run_snapshot(state, run_id)
        if record.get("kind") == "project-verification":
            evidence.validate_run_record(record)
    return bundle, record, manifest


def saved_run(state: Path, run_id: str) -> tuple[Path, dict]:
    """Read a saved run; a project verification is validated before any field is used."""
    bundle, record, _ = saved_run_snapshot(state, run_id)
    return bundle, record


def acceptable(project: Project, approved: dict, record: dict) -> bool:
    # A contract with source mutations must carry its qualification evidence, not just a verdict.
    return (
        record.get("kind") == "project-verification"
        and evidence.acceptable(record)
        and not (
            project.contract.source_mutations is not None
            and "source_qualifications" not in record
        )
        and not any(record.get(key) != value for key, value in scope(project).items())
        and record.get("approval") == approved
    )
