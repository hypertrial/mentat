"""Typed approval, acceptance and source-qualification models: corpus fit and strictness."""

import ast
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from mentat import evidence, records
from mentat.canonical import json_bytes

ROOT = Path(__file__).parent / "fixtures" / "records"
MODULE = Path(records.__file__)
DIGEST = "b" * 64
COMMIT = "a" * 40
APPROVAL = {
    "repo": "/repo",
    "base": COMMIT,
    "runner": "host",
    "contract_digest": DIGEST,
    "approved_at": "2026-01-02T03:04:05+00:00",
}
ACCEPTANCE = {
    "run_id": "c" * 32,
    "manifest_digest": DIGEST,
    "revision": COMMIT,
    "contract_digest": DIGEST,
    "accepted_at": "2026-01-02T03:04:05+00:00",
}
DECLARATION = {
    "id": "flip",
    "claim": "alpha",
    "check": "native",
    "expected_case": "target",
    "required_cases": ["target", "other"],
    "violation_exit_codes": [1, 2],
}
QUALIFICATION = {
    "schema_version": 1,
    "id": "flip",
    "claim": "alpha",
    "check": "native",
    "expected_case": "target",
    "path": "source.py",
    "status": "pass",
    "outcome": "killed",
    "detail": "Expected case failed",
    "bindings": {"run_id": "c" * 32},
    "applied_count": 1,
    "assertion_failed": True,
}


def _corpus() -> list[tuple[type, dict]]:
    """Every approval, acceptance and source-qualification entry in the fixture corpus."""
    entries = []
    for path in sorted(ROOT.glob("*/state/approvals/*.json")):
        entries.append((records.Approval, json.loads(path.read_bytes())))
    for path in sorted(ROOT.glob("*/state/acceptances/*.json")):
        entries.append((records.Acceptance, json.loads(path.read_bytes())))
    for path in sorted(ROOT.glob("*/state/runs/*/manifest.json")):
        manifest = json.loads(path.read_bytes())
        for item in manifest.get("source_mutation_checks", []):
            entries.append((records.SourceMutationCheck, item))
        for item in manifest.get("source_qualifications", []):
            entries.append((records.SourceQualification, item))
    return entries


CORPUS = _corpus()


def test_corpus_has_every_record_kind():
    kinds = {model for model, _ in CORPUS}
    assert kinds == {
        records.Approval,
        records.Acceptance,
        records.SourceMutationCheck,
        records.SourceQualification,
    }


@pytest.mark.parametrize(("model", "raw"), CORPUS)
def test_corpus_entry_round_trips_through_its_model(model, raw):
    validated = model.model_validate(raw)
    assert json_bytes(validated.model_dump(exclude_unset=True)) == json_bytes(raw)


@pytest.mark.parametrize(
    ("model", "raw"),
    [
        (records.Approval, APPROVAL),
        (records.Acceptance, ACCEPTANCE),
        (records.SourceMutationCheck, DECLARATION),
        (records.SourceQualification, QUALIFICATION),
    ],
)
@pytest.mark.parametrize("value", [[], "text", None, 1])
def test_non_object_is_rejected(model, raw, value):
    with pytest.raises(ValidationError):
        model.model_validate(value)


@pytest.mark.parametrize(
    ("model", "raw", "key"),
    [
        *[(records.Approval, APPROVAL, key) for key in APPROVAL],
        *[(records.Acceptance, ACCEPTANCE, key) for key in ACCEPTANCE],
        *[(records.SourceMutationCheck, DECLARATION, key) for key in DECLARATION],
        # path and assertion_failed are not required: today's validator never requires them.
        *[
            (records.SourceQualification, QUALIFICATION, key)
            for key in QUALIFICATION
            if key not in {"path", "assertion_failed"}
        ],
    ],
)
def test_missing_required_key_is_rejected(model, raw, key):
    with pytest.raises(ValidationError):
        model.model_validate({k: v for k, v in raw.items() if k != key})


@pytest.mark.parametrize(
    ("model", "raw", "key", "value"),
    [
        (records.Approval, APPROVAL, "repo", 1),
        (records.Approval, APPROVAL, "approved_at", None),
        (records.Approval, APPROVAL, "base", ["x"]),
        (records.Acceptance, ACCEPTANCE, "run_id", 1),
        (records.Acceptance, ACCEPTANCE, "accepted_at", None),
        (records.SourceMutationCheck, DECLARATION, "required_cases", "target"),
        (records.SourceMutationCheck, DECLARATION, "required_cases", []),
        (records.SourceMutationCheck, DECLARATION, "violation_exit_codes", []),
        (records.SourceMutationCheck, DECLARATION, "violation_exit_codes", [True]),
        (records.SourceMutationCheck, DECLARATION, "violation_exit_codes", [1.0]),
        (records.SourceMutationCheck, DECLARATION, "violation_exit_codes", [0]),
        (records.SourceMutationCheck, DECLARATION, "violation_exit_codes", [125]),
        (records.SourceQualification, QUALIFICATION, "schema_version", True),
        (records.SourceQualification, QUALIFICATION, "schema_version", 1.0),
        (records.SourceQualification, QUALIFICATION, "schema_version", 2),
        (records.SourceQualification, QUALIFICATION, "applied_count", True),
        (records.SourceQualification, QUALIFICATION, "applied_count", 1.0),
        (records.SourceQualification, QUALIFICATION, "applied_count", 2),
        (records.SourceQualification, QUALIFICATION, "assertion_failed", "yes"),
        (records.SourceQualification, QUALIFICATION, "assertion_failed", 1),
        (records.SourceQualification, QUALIFICATION, "bindings", []),
        (records.SourceQualification, QUALIFICATION, "detail", None),
    ],
)
def test_wrong_type_or_value_is_rejected(model, raw, key, value):
    with pytest.raises(ValidationError):
        model.model_validate({**raw, key: value})


@pytest.mark.parametrize(
    ("model", "raw"),
    [
        (records.Approval, APPROVAL),
        (records.Acceptance, ACCEPTANCE),
        (records.SourceMutationCheck, DECLARATION),
        (records.SourceQualification, QUALIFICATION),
    ],
)
def test_unknown_keys_are_kept(model, raw):
    extended = {**raw, "future_key": {"nested": [1, 2]}}
    validated = model.model_validate(extended)
    assert validated.model_dump(exclude_unset=True)["future_key"] == {"nested": [1, 2]}
    assert json_bytes(validated.model_dump(exclude_unset=True)) == json_bytes(extended)


@pytest.mark.parametrize("model", [records.Approval, records.Acceptance])
def test_decision_source_is_emitted_only_when_set(model):
    raw = APPROVAL if model is records.Approval else ACCEPTANCE
    assert "decision_source" not in model.model_validate(raw).model_dump(exclude_unset=True)
    relayed = {**raw, "decision_source": "unverified_agent_relay"}
    assert model.model_validate(relayed).model_dump(exclude_unset=True) == relayed


@pytest.mark.parametrize("model", [records.Approval, records.Acceptance])
@pytest.mark.parametrize("source", [None, 5, ["x"], "<img src=x>*_`"])
def test_decision_source_keeps_any_json_value(model, source):
    # Saved records accept any decision_source. Only a non-empty string is labelled, so it
    # is not typed as a string.
    raw = APPROVAL if model is records.Approval else ACCEPTANCE
    assert model.model_validate({**raw, "decision_source": source}).decision_source == source


def test_qualification_optional_assertion_is_absent_or_null_or_bool():
    for value in (None, True, False):
        model = records.SourceQualification.model_validate(
            {**QUALIFICATION, "assertion_failed": value}
        )
        assert model.assertion_failed is value
    absent = {k: v for k, v in QUALIFICATION.items() if k != "assertion_failed"}
    assert "assertion_failed" not in records.SourceQualification.model_validate(absent).model_dump(
        exclude_unset=True
    )


@pytest.mark.parametrize("value", ["", "\ud800", "a" * 5000])
def test_declaration_id_has_no_string_constraint(value):
    # Any str is accepted, as the hand check accepted it; emptiness is refused by the caller.
    assert records.SourceMutationCheck.model_validate({**DECLARATION, "id": value}).id == value
    assert records.SourceQualification.model_validate({**QUALIFICATION, "id": value}).id == value


def _qualified_manifest() -> dict:
    for path in sorted(ROOT.glob("*/state/runs/*/manifest.json")):
        manifest = json.loads(path.read_bytes())
        if manifest.get("source_mutation_checks") and manifest.get("source_qualifications"):
            return manifest
    raise AssertionError("fixture corpus has no qualified run")


DECLARATION_MESSAGE = "Invalid source qualification declaration"
BINDINGS_MESSAGE = "Source qualification bindings or outcome are invalid"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("source_mutation_checks", 0, "violation_exit_codes"), [True], DECLARATION_MESSAGE),
        (("source_mutation_checks", 0, "violation_exit_codes"), [1.0], DECLARATION_MESSAGE),
        (("source_mutation_checks", 0, "required_cases"), [1], DECLARATION_MESSAGE),
        (("source_mutation_checks", 0, "id"), "", DECLARATION_MESSAGE),
        (("source_qualifications", 0, "schema_version"), True, BINDINGS_MESSAGE),
        (("source_qualifications", 0, "schema_version"), 1.0, BINDINGS_MESSAGE),
    ],
)
def test_source_qualification_boundary_refuses_with_the_fixed_message(path, value, message):
    manifest = _qualified_manifest()
    evidence.source_qualifications(manifest)
    target = manifest
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        evidence.source_qualifications(manifest)


def test_error_messages_do_not_echo_the_record():
    with pytest.raises(ValidationError) as info:
        records.Approval.model_validate({**APPROVAL, "repo": ["/secret/path"]})
    assert "/secret/path" not in str(info.value)


def _allowed(module: str) -> bool:
    return module.split(".")[0] in sys.stdlib_module_names or module.split(".")[0] == "pydantic"


def test_records_imports_only_the_standard_library_pydantic_and_canonical():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            assert node.level == 1 and node.module == "canonical", node.module
        elif isinstance(node, ast.ImportFrom):
            assert _allowed(node.module), node.module
        elif isinstance(node, ast.Import):
            assert all(_allowed(alias.name) for alias in node.names), node.names


def test_records_imports_nothing_from_project():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(name and "project" in name for name in names)


def _saved_run(tmp_path: Path) -> tuple[Path, str]:
    """Copy one released fixture run into a state directory the tests may change."""
    source = ROOT / "v0.1.2" / "state"
    state = tmp_path / "state"
    shutil.copytree(source, state)
    run_id = next(path.name for path in (state / "runs").iterdir())
    return state, run_id


def _rewrite_manifest(state: Path, run_id: str, raw: bytes) -> None:
    """Replace a saved manifest and its receipt, so only the manifest's content is under test."""
    (state / "runs" / run_id / "manifest.json").write_bytes(raw)
    (state / "receipts" / run_id).write_text(evidence.digest(raw))


def test_saved_manifest_with_duplicate_keys_is_refused(tmp_path):
    state, run_id = _saved_run(tmp_path)
    raw = (state / "runs" / run_id / "manifest.json").read_bytes()
    duplicated = b'{"run_id":"%s",' % run_id.encode() + raw[1:]
    _rewrite_manifest(state, run_id, duplicated)
    with pytest.raises(ValueError, match="^duplicate JSON key$"):
        evidence.read_run_snapshot(state, run_id)


def test_large_valid_saved_manifest_still_loads(tmp_path):
    state, run_id = _saved_run(tmp_path)
    record = json.loads((state / "runs" / run_id / "manifest.json").read_bytes())
    record["padding"] = "x" * 3_000_000
    raw = evidence.json_bytes(record)
    assert len(raw) > 3_000_000
    _rewrite_manifest(state, run_id, raw)
    _, loaded, _ = evidence.read_run_snapshot(state, run_id)
    assert len(loaded["padding"]) == 3_000_000


@pytest.mark.parametrize("model", [records.Approval, records.Acceptance])
def test_check_ignores_undeclared_keys_that_pydantic_cannot_encode(model):
    raw = APPROVAL if model is records.Approval else ACCEPTANCE
    hostile = {**raw, "\ud800": 1, "nested": {"\ud800": [1]}}
    with pytest.raises(ValidationError):
        model.model_validate(hostile)
    records.check(model, hostile)
    declared = next(iter(model.model_fields))
    with pytest.raises(ValidationError):
        records.check(model, {**raw, declared: 1, "\ud800": 1})
