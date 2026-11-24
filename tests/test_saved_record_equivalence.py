"""Saved-record validation matches the frozen hand checks, outcome and message, on mutated fixtures.

saved_record_oracle holds the pre-model validators and the claim grouping, copied from commit
3e085c7 and self-contained. Each fixture run record is mutated in seeded ways, one defect at a
time and two at a time: keys set to a spread of values (lone surrogates, boundary numbers, and
values taken from the record's own ids so cross-record branches fire), deleted, given extra keys,
and absent optional keys inserted. Both implementations must agree on each result.
"""

import copy
import json
import random
import re
from pathlib import Path

import pytest
import saved_record_oracle as oracle
from test_assurance import assurance as sample_assurance
from test_evidence_comparison import save
from typer.testing import CliRunner

from mentat import evidence, evidence_report, project, records
from mentat.cli import app

ROOT = Path(__file__).parent / "fixtures" / "records"
MANIFESTS = sorted(ROOT.glob("*/state/runs/*/manifest.json"))
MISSING = object()
SAMPLES = [
    None,
    True,
    False,
    0,
    1,
    2,
    1.0,
    0.5,
    -1,
    124,
    125,
    2**53,
    10**30,
    -1.5,
    "",
    "x",
    "\ud800",
    "a\udfff b",
    "\u0000",
    "é",
    "<img src=x onerror=1>",
    "../x",
    "/private/x",
    [],
    ["x"],
    [None],
    ["\ud800"],
    [1, "\ud800"],
    {},
    {"a": 1},
    {"\ud800": 1},
    MISSING,
]
# Extra keys added at every container level, the root included: a normal one and a lone surrogate.
EXTRA_KEYS = ["__extra__", "\ud800"]
# Optional keys the old checks read only when present; inserting one tests its absent-key path.
OPTIONAL_KEYS = [
    "assurance",
    "artifact_omissions",
    "log_omissions",
    "stdout",
    "stderr",
    "case_results",
    "required_cases",
    "negative_control",
    "exit_code",
    "command_status",
    "qualified",
    "evidence",
    "behavior_checks",
    "claim_review",
    "recorded_at",
    "groups",
    "titles",
    "verification_limits",
    "case_id",
    "source_qualification_status",
    "claim_ids",
    "title",
    "checks",
    "assumptions",
]
SINGLE_CASES = 400
PAIR_CASES = 300
INSERT_CASES = 300


def _paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _paths(value, (*prefix, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _paths(value, (*prefix, index))


def _get(record, path):
    for step in path:
        record = record[step]
    return record


def _pool(record: dict) -> list:
    """Strings the record already names: ids, claims, check and case names, artifact files."""
    found = set()
    for path in _paths(record):
        value = _get(record, path) if path else record
        if isinstance(value, str) and 0 < len(value) <= 64:
            found.add(value)
        if path and isinstance(path[-1], str) and 0 < len(path[-1]) <= 64:
            found.add(path[-1])
    return sorted(found)


def _operations(record: dict, rng: random.Random) -> list[tuple]:
    """Every single change: (path, operation, value). Values come from the samples and the pool."""
    pool = _pool(record)
    operations = [((), "extra", key) for key in EXTRA_KEYS]
    for path in _paths(record):
        if not path:
            continue
        container = _get(record, path[:-1])
        operations.extend((path, "set", value) for value in SAMPLES if value is not MISSING)
        operations.extend((path, "set", value) for value in rng.sample(pool, min(6, len(pool))))
        operations.append((path, "del", None))
        if isinstance(container, dict):
            operations.extend((path, "extra", key) for key in EXTRA_KEYS)
    for path in _paths(record):
        node = _get(record, path)
        if isinstance(node, dict):
            for key in OPTIONAL_KEYS:
                if key not in node:
                    values = rng.sample(SAMPLES[:-1] + pool, 4)
                    operations.extend((path, "insert", (key, value)) for value in values)
    return operations


def _apply(record: dict, path: tuple, operation: str, value) -> dict:
    changed = copy.deepcopy(record)
    if operation == "extra":
        _get(changed, path[:-1])[value] = {"nested": [1, "\ud800"]}
        return changed
    if operation == "insert":
        key, inserted = value
        _get(changed, path)[key] = inserted
        return changed
    parent = _get(changed, path[:-1])
    if operation == "set":
        parent[path[-1]] = value
    else:
        del parent[path[-1]]
    return changed


def _section(path: tuple) -> tuple:
    """The check or claim a path belongs to; two defects in one section are the hard case."""
    if not path:
        return ()
    if path[0] == "cases" and len(path) >= 4:
        return path[:4]
    if path[0] in ("model_checks", "claims", "behavior_checks") and len(path) >= 2:
        return path[:2]
    return path[:1]


def _outcome(fn):
    try:
        return ("ok", fn())
    except Exception as exc:  # noqa: BLE001 - every failure kind must match
        return ("error", type(exc).__name__, str(exc))


def _validate_new(record: dict):
    evidence.validate_run_record(record)
    return evidence_report.claim_groups(record)


def _compare(changed: dict) -> tuple | None:
    old = (_outcome(lambda: oracle.validate_record(changed)), _outcome(
        lambda: oracle.source_qualifications(changed)
    ))
    new = (_outcome(lambda: _validate_new(changed)), _outcome(
        lambda: evidence.source_qualifications(changed)
    ))
    return None if old == new else (old, new)


def _label(manifest: Path) -> str:
    return f"{manifest.parts[-5]}-{manifest.parts[-2][:8]}"


@pytest.mark.parametrize("manifest", MANIFESTS, ids=_label)
def test_mutated_record_outcomes_match_the_frozen_oracle(manifest):
    record = json.loads(manifest.read_bytes())
    assert _compare(record) is None
    rng = random.Random(58)
    operations = _operations(record, rng)
    inserts = [op for op in operations if op[1] == "insert"]
    singles = [op for op in operations if op[1] != "insert"]
    chosen = rng.sample(singles, min(SINGLE_CASES, len(singles)))
    chosen += rng.sample(inserts, min(INSERT_CASES, len(inserts)))
    by_section: dict[tuple, list] = {}
    for op in singles:
        by_section.setdefault(_section(op[0]), []).append(op)
    sections = [group for group in by_section.values() if len(group) > 1]
    for _ in range(PAIR_CASES):
        if sections and rng.random() < 0.5:
            first, second = rng.sample(rng.choice(sections), 2)
        else:
            first, second = rng.sample(singles, 2)
        try:
            changed = _apply(record, *first)
            changed = _apply(changed, *second)
        except (KeyError, IndexError, TypeError):
            continue
        chosen.append(("pair", first, second))
    mismatches = []
    for item in chosen:
        if item[0] == "pair":
            first, second = item[1], item[2]
            try:
                changed = _apply(_apply(record, *first), *second)
            except (KeyError, IndexError, TypeError):
                continue
            detail = (first, second)
        else:
            changed = _apply(record, *item)
            detail = item
        difference = _compare(changed)
        if difference is not None:
            mismatches.append((detail, difference))
    assert not mismatches, mismatches[:3]


def _fixture_record() -> dict:
    return json.loads(MANIFESTS[0].read_bytes())


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        # Item 1: old order is filenames, then the log stream type; then the case duplicates.
        (
            lambda r: r["model_checks"][0].update(
                artifact_omissions=["a/b"], log_omissions=5
            ),
            "Invalid saved evidence filename",
        ),
        (
            lambda r: r["cases"][0]["checks"][0].update(
                required_cases=["x", "x"], case_results=5
            ),
            "Invalid saved required cases",
        ),
    ],
)
def test_two_defects_in_one_section_report_the_old_message(edit, expected):
    record = _fixture_record()
    edit(record)
    assert _compare(record) is None
    with pytest.raises(ValueError, match=re.escape(expected)):
        evidence.validate_run_record(record)


def test_run_without_inventory_is_refused_by_comparison(tmp_path):
    # Validation accepts this historical shape, but grouping and comparison cannot use it.
    # The supported comparison CLI must report the failure rather than a usage error.
    state = evidence.state_directory(tmp_path / "state")
    run_id = "a" * 32
    save(state, run_id, "pass")
    manifest = state / "runs" / run_id / "manifest.json"
    record = json.loads(manifest.read_bytes())
    record.update(behavior_skipped=True, behavior_checks=None, cases=[])
    raw = evidence.json_bytes(record)
    manifest.write_bytes(raw)
    (state / "receipts" / run_id).write_text(evidence.digest(raw))
    _, saved, _ = evidence.read_run_snapshot(state, run_id)
    evidence.validate_run_record(saved)
    with pytest.raises(TypeError):
        evidence_report.claim_groups(saved)
    result = CliRunner().invoke(
        app,
        [
            "--state",
            str(state),
            "evidence",
            "compare",
            run_id,
            run_id,
            "--json",
        ],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "incomplete"
    assert payload["detail"] == "Invalid saved verification record"


# Item 5: the frozen contract shape must accept and refuse exactly what the contract does.
_ASSURANCE_PAIRS = [
    (project.Assurance, records.Assurance),
    (project.Bound, records.Bound),
    (project.EvidenceRef, records.EvidenceRef),
    (project.Exclusion, records.Exclusion),
    (project.Deviation, records.Deviation),
    (project.ClaimCitation, records.ClaimCitation),
]


def _assurance_mutations(value, path=()):
    yield path, None
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _assurance_mutations(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _assurance_mutations(item, (*path, index))


def _assurance_variants():
    base = {"assurance": sample_assurance()}
    yield base
    for path, _ in list(_assurance_mutations(base)):
        if not path:
            continue
        for value in [MISSING, None, True, 0, 2, "", "x", "\ud800", "../a", [], {}, "enforced", 0.5]:
            changed = copy.deepcopy(base)
            parent = _get(changed, path[:-1])
            if value is MISSING:
                if isinstance(parent, dict):
                    parent.pop(path[-1], None)
                else:
                    continue
            else:
                parent[path[-1]] = value
            yield changed
        if isinstance(_get(base, path[:-1]), dict):
            extra = copy.deepcopy(base)
            _get(extra, path[:-1])["__extra__"] = 1
            yield extra


def _outcome_assurance(model, value):
    try:
        model.model_validate(value)
        return ("ok",)
    except Exception as exc:  # noqa: BLE001
        return ("error", type(exc).__name__, str(exc))


def test_frozen_assurance_accepts_and_refuses_like_the_contract():
    variants = list(_assurance_variants())
    assert len(variants) > 100
    for value in variants:
        expected = _outcome_assurance(project.Assurance, value.get("assurance"))
        assert _outcome_assurance(records.Assurance, value.get("assurance")) == expected


def _normal(text: str) -> str:
    """Names the frozen copy gives its classes, so the two shapes can be compared."""
    return re.sub(r"mentat\.(project|records)\.", "", text)


def _shape(model) -> dict:
    fields = {}
    for name, field in model.model_fields.items():
        fields[name] = {
            "annotation": _normal(repr(field.annotation)),
            "metadata": _normal(repr(field.metadata)),
            "required": field.is_required(),
            "factory": field.default_factory is not None,
        }
    validators = model.__pydantic_decorators__
    return {
        "fields": fields,
        "extra": model.model_config.get("extra"),
        "strict": model.model_config.get("strict"),
        "field_validators": sorted(validators.field_validators),
        "model_validators": sorted(validators.model_validators),
    }


@pytest.mark.parametrize(("contract", "frozen"), _ASSURANCE_PAIRS, ids=lambda m: m.__name__)
def test_frozen_assurance_shape_matches_the_contract(contract, frozen):
    assert _shape(frozen) == _shape(contract)


def test_oracle_corpus_is_not_empty():
    assert MANIFESTS, "fixture corpus has no run records"
