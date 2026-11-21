"""Saved-record validation matches the frozen hand checks, outcome and message, on mutated fixtures.

saved_record_oracle holds the pre-model validators. Each fixture run record is mutated in
deterministic ways: every sampled key is set to a spread of values (including lone surrogates),
deleted, or given an extra key, at any depth. Both implementations must agree on each result.
"""

import copy
import json
import random
from pathlib import Path

import pytest
import saved_record_oracle as oracle

from mentat import evidence, evidence_report

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
    2**53,
    10**30,
    -1.5,
    MISSING,
]
# Extra keys added at every level, the root included: a normal one and a lone surrogate.
EXTRA_KEYS = ["__extra__", "\ud800"]
PER_RECORD = 350


def _paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _paths(value, (*prefix, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _paths(value, (*prefix, index))


def _mutations(record: dict) -> list[tuple]:
    """Every (path, operation, value) that changes one key or item of the record."""
    cases = _root_extras(record)
    for path in _paths(record):
        if not path:
            continue
        parent = record
        for step in path[:-1]:
            parent = parent[step]
        cases.extend((path, "set", value) for value in SAMPLES if value is not MISSING)
        cases.append((path, "del", None))
        if isinstance(parent, dict):
            cases.extend((path, "extra", key) for key in EXTRA_KEYS)
    return cases


def _root_extras(record: dict) -> list[tuple]:
    return [((), "extra", key) for key in EXTRA_KEYS]


def _apply(record: dict, path: tuple, operation: str, value) -> dict:
    changed = copy.deepcopy(record)
    parent = changed
    for step in path[:-1]:
        parent = parent[step]
    if operation == "set":
        parent[path[-1]] = value
    elif operation == "del":
        del parent[path[-1]]
    else:
        parent[value] = {"nested": [1, "\ud800"]}
    return changed


def _outcome(fn):
    try:
        return ("ok", fn())
    except Exception as exc:  # noqa: BLE001 - every failure kind must match
        return ("error", type(exc).__name__, str(exc))


def _validate_new(record: dict):
    evidence.validate_run_record(record)
    return evidence_report.claim_groups(record)


def _label(manifest: Path) -> str:
    return f"{manifest.parts[-5]}-{manifest.parts[-2][:8]}"


@pytest.mark.parametrize("manifest", MANIFESTS, ids=_label)
def test_mutated_record_outcomes_match_the_frozen_oracle(manifest):
    record = json.loads(manifest.read_bytes())
    assert _outcome(lambda: oracle.validate_record(record)) == _outcome(
        lambda: _validate_new(record)
    )
    assert _outcome(lambda: oracle.source_qualifications(record)) == _outcome(
        lambda: evidence.source_qualifications(record)
    )
    cases = _mutations(record)
    chosen = random.Random(58).sample(cases, min(PER_RECORD, len(cases)))
    mismatches = []
    for path, operation, value in chosen:
        changed = _apply(record, path, operation, value)
        old_validation = _outcome(lambda: oracle.validate_record(changed))
        new_validation = _outcome(lambda: _validate_new(changed))
        old_sources = _outcome(lambda: oracle.source_qualifications(changed))
        new_sources = _outcome(lambda: evidence.source_qualifications(changed))
        if old_validation != new_validation or old_sources != new_sources:
            mismatches.append((path, operation, repr(value), old_validation, new_validation))
    assert not mismatches, mismatches[:5]


def test_oracle_corpus_is_not_empty():
    assert MANIFESTS, "fixture corpus has no run records"
