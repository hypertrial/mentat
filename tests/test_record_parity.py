"""Parity between the typed boundaries and the frozen pre-model hand rules.

The functions named _frozen_* are the hand checks for approval, acceptance and source
qualification records as they stood before the typed models (WORK-57), copied verbatim from
the parent commit. They are a frozen oracle: over a mutation corpus built from the fixture
records, the models plus the code that remains must refuse and accept the same inputs with the
same messages. Do not edit them to follow new code; a difference is a behavior change to list.
"""

import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from mentat import cli, evidence, evidence_html
from mentat.canonical import digest, json_bytes
from mentat.evidence_report import decision_source

ROOT = Path(__file__).parent / "fixtures" / "records"
DIGEST = "b" * 64
COMMIT = "a" * 40
RUN = "c" * 32
DECLARATION = "Invalid source qualification declaration"
RESULT = "Invalid source qualification result"
BINDINGS = "Source qualification bindings or outcome are invalid"
APPROVAL = "Approval record is invalid. Check and approve the current contract"
RECEIPT = "Invalid or mismatched receipt"
ORACLED = {DECLARATION, RESULT, BINDINGS}
_MISSING = object()
# Odd but valid values: wrong types, bool for int, 1.0, a lone surrogate, empty, very long.
SAMPLES = [
    _MISSING,
    None,
    True,
    False,
    0,
    1,
    2,
    1.0,
    "",
    "x",
    "\ud800",
    "a" * 5000,
    [],
    [1],
    [None],
    [True],
    [0],
    [124],
    [125],
    -1,
    ["x"],
    ["\ud800"],
    {},
    {"a": 1},
]
APPROVAL_BASE = {
    "repo": "/repo",
    "base": COMMIT,
    "runner": "host",
    "contract_digest": DIGEST,
    "approved_at": "2026-01-02T03:04:05+00:00",
}
RECEIPT_BASE = {
    "run_id": RUN,
    "revision": COMMIT,
    "contract_digest": DIGEST,
    "manifest_digest": DIGEST,
    "accepted_at": "2026-01-02T03:04:05Z",
}


# ---- Frozen pre-model hand rules (copied from the parent commit) ----------------------------


def _frozen_approval_refused(record, scope) -> bool:
    return (
        not isinstance(record, dict)
        or any(record.get(key) != value for key, value in scope.items())
        or not isinstance(record.get("approved_at"), str)
    )


def _frozen_string(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid saved verification record")
    return value


def _frozen_receipt_status(receipt, record, digest) -> str:
    try:
        if (
            not isinstance(receipt, dict)
            or any(
                receipt.get(k) != v
                for k, v in {
                    "run_id": record["run_id"],
                    "revision": record["revision"],
                    "contract_digest": record["contract_digest"],
                    "manifest_digest": digest,
                }.items()
            )
            or not evidence.acceptable(record)
        ):
            raise ValueError
        accepted = datetime.fromisoformat(_frozen_string(receipt.get("accepted_at")))
        if accepted.tzinfo is None:
            raise ValueError
        source = decision_source(receipt)
        return "Recorded at " + receipt["accepted_at"] + (f" ({source})" if source else "")
    except (OSError, ValueError, TypeError, KeyError):
        return RECEIPT


def _frozen_declaration_refused(item, claims, declared) -> bool:
    return (
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not item["id"]
        or item["id"] in declared
        or not isinstance(item.get("claim"), str)
        or item["claim"] not in claims
        or not isinstance(item.get("check"), str)
        or not isinstance(item.get("required_cases"), list)
        or not item["required_cases"]
        or any(not isinstance(case, str) for case in item["required_cases"])
        or len(set(item["required_cases"])) != len(item["required_cases"])
        or item.get("expected_case") not in item["required_cases"]
        or not isinstance(item.get("violation_exit_codes"), list)
        or not item["violation_exit_codes"]
        or any(
            type(code) is not int or not 1 <= code <= 124 for code in item["violation_exit_codes"]
        )
    )


def _frozen_bindings_refused(result, item, record) -> bool:
    binding = result.get("bindings")
    expected_binding = {
        key: record[key]
        for key in ("run_id", "revision", "snapshot_digest", "base", "contract_digest")
    }
    expected_binding.update(
        verifier_digest=record["comparison_inputs"]["verifier"],
        approved_manifest_sha256=record["source_mutation_manifest_sha256"],
    )
    return (
        type(result.get("schema_version")) is not int
        or result["schema_version"] != 1
        or any(result.get(key) != item[key] for key in ("claim", "check", "expected_case"))
        or not isinstance(binding, dict)
        or any(binding.get(key) != value for key, value in expected_binding.items())
        or binding.get("runtime")
        != {"runner": record["runner"], "sha256": digest(json_bytes(record["runtime"]))}
        or not isinstance(result.get("outcome"), str)
        or result["outcome"] not in {"killed", "survived", "error"}
        or result.get("status")
        != {"killed": "pass", "survived": "violation", "error": "incomplete"}.get(
            result.get("outcome")
        )
        or type(result.get("applied_count")) is not int
        or result["applied_count"] not in (0, 1)
        or not isinstance(result.get("detail"), str)
        or result.get("assertion_failed") is not None
        and type(result["assertion_failed"]) is not bool
    )


def _frozen_qualification_refusal(record):
    """The first frozen hand rule that refuses a saved record's declarations or results."""
    claims = {claim["id"] for claim in record["claims"]}
    declared = {}
    for item in record["source_mutation_checks"]:
        if _frozen_declaration_refused(item, claims, declared):
            return DECLARATION
        declared[item["id"]] = item
    if record["behavior_skipped"]:
        return None
    observed = set()
    for result in record["source_qualifications"]:
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("id"), str)
            or result["id"] not in declared
            or result["id"] in observed
        ):
            return RESULT
        observed.add(result["id"])
        if _frozen_bindings_refused(result, declared[result["id"]], record):
            return BINDINGS
    return None


# ---- Mutation corpus ----------------------------------------------------------------------


def _with_value(obj, key, value):
    changed = copy.deepcopy(obj)
    if value is _MISSING:
        changed.pop(key, None)
    else:
        changed[key] = value
    return changed


def _qualified_manifests():
    manifests = []
    for path in sorted(ROOT.glob("*/state/runs/*/manifest.json")):
        manifest = json.loads(path.read_bytes())
        if manifest.get("source_mutation_checks") and manifest.get("source_qualifications"):
            manifests.append(manifest)
    assert manifests, "fixture corpus has no qualified run"
    return manifests


def _qualification_mutations():
    """(label, mutated run record) for every declaration, result and binding field."""
    for number, manifest in enumerate(_qualified_manifests()):
        for index, item in enumerate(manifest["source_mutation_checks"]):
            for key in [*item, "extra", "\ud800"]:
                for value in SAMPLES:
                    record = copy.deepcopy(manifest)
                    record["source_mutation_checks"][index] = _with_value(item, key, value)
                    yield f"{number} decl[{index}].{key}={value!r:.40}", record
        for index, item in enumerate(manifest["source_qualifications"]):
            for key in [*item, "extra", "\ud800"]:
                for value in SAMPLES:
                    record = copy.deepcopy(manifest)
                    record["source_qualifications"][index] = _with_value(item, key, value)
                    yield f"{number} result[{index}].{key}={value!r:.40}", record
            for key in item.get("bindings", {}):
                for value in SAMPLES:
                    record = copy.deepcopy(manifest)
                    bindings = _with_value(item["bindings"], key, value)
                    record["source_qualifications"][index]["bindings"] = bindings
                    yield f"{number} result[{index}].bindings.{key}={value!r:.40}", record


def _real_qualification_message(record):
    try:
        evidence.source_qualifications(record)
    except Exception as exc:  # noqa: BLE001 - later, unchanged checks may raise anything
        return str(exc)
    return None


def test_source_qualification_boundaries_match_the_frozen_hand_rules():
    checked, mismatches = 0, []
    for label, record in _qualification_mutations():
        checked += 1
        oracle = _frozen_qualification_refusal(record)
        real = _real_qualification_message(record)
        if oracle is not None and real != oracle:
            mismatches.append((label, oracle, real))
        if oracle is None and real in ORACLED:
            mismatches.append((label, None, real))
    assert not mismatches, mismatches[:5]
    assert checked >= 300, checked


def test_approval_boundary_matches_the_frozen_hand_rule(monkeypatch):
    project = SimpleNamespace(root=Path("/repo"), base=COMMIT, runner="host", fingerprint=DIGEST)
    scope = {"repo": "/repo", "base": COMMIT, "runner": "host", "contract_digest": DIGEST}
    cases = [APPROVAL_BASE, [], "x", None]
    for key in {*APPROVAL_BASE, "decision_source", "extra", "\ud800"}:
        cases += [_with_value(APPROVAL_BASE, key, value) for value in SAMPLES]
    checked = 0
    for value in cases:
        checked += 1
        monkeypatch.setattr(evidence, "read_json", lambda path, value=value: value)
        try:
            real = cli._approval(Path("/state"), project)
        except ValueError as exc:
            real = str(exc)
        expected = APPROVAL if _frozen_approval_refused(value, scope) else value
        assert real == expected, value
    assert checked >= 100


def test_acceptance_boundary_matches_the_frozen_hand_rule(tmp_path):
    record = {"run_id": RUN, "revision": COMMIT, "contract_digest": DIGEST, "status": "pass"}
    state = tmp_path / "state"
    (state / "acceptances").mkdir(parents=True)
    path = state / "acceptances" / f"{RUN}.json"
    cases = [RECEIPT_BASE, [], "x"]
    for key in {*RECEIPT_BASE, "decision_source", "accepted_at", "extra", "\ud800"}:
        cases += [_with_value(RECEIPT_BASE, key, value) for value in SAMPLES]
    checked = 0
    for value in cases:
        checked += 1
        path.write_text(json.dumps(value))
        real = evidence_html.acceptance_status(state, record, DIGEST)
        assert real == _frozen_receipt_status(value, record, DIGEST), value
    assert checked >= 100
