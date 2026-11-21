"""Proof manifests, ledger entries, checker results and saved proof checks match the frozen hand checks.

proof_record_oracle holds the pre-model checks. Corpora come from real campaigns and a real saved
proof (the test_proof fixtures). Each corpus is mutated deterministically: every key and item, at
any depth, is set to a spread of values (including lone surrogates), deleted, or given an extra
key, and the whole value is replaced at the root (the ledger excepted: its root is the chain, and
a non-list root cannot be written as entries). A seeded pass applies two mutations in a row. The
models and the code that remains must agree with the oracle on outcome, exception type and message.
"""

import copy
import hashlib
import random
import shutil

import proof_record_oracle as oracle
import pytest
from test_proof import campaign as proof_campaign  # noqa: F401
from test_proof import recorded_proof as proof_record  # noqa: F401
from test_proof import recorded_source  # noqa: F401

import mentat
from mentat import evidence, proof

SAMPLES = [
    None, True, False, 0, 1, 2, 1.0, 0.5, -1, 2**53, 10**30,
    "", "x", "\ud800", "a\udfff b", "\x00", "\x01", "a\nb", "a" * 4097,
    "reserve", "complete", "accepted", "rejected", "incomplete", "pass", "lean-comparator",
    "0" * 32, "0" * 64,
    [], [None], ["\ud800"], [1, "\ud800"],
    {}, {"a": 1}, {"\ud800": 1},
]
# Checker logs are bytes in memory, so the checker corpus also carries bytes.
CHECKER_SAMPLES = [*SAMPLES, b"", b"x"]
EXTRA_KEYS = ["extra", "\ud800"]
SEED = 59
MANIFEST_PAIRS = 2000
CHECKER_PAIRS = 2000
LEDGER_PAIRS = 1000
# Every single mutation of the six-entry ledger (1963), plus LEDGER_PAIRS seeded pairs, is run.
LEDGER_CASE_TOTAL = 2963
LEDGER_MESSAGES = {
    "Invalid proof campaign ledger entry",
    "Invalid proof attempt ID",
    "Invalid proof attempt result",
    "Invalid proof attempt completion",
    "Duplicate proof attempt",
    "Proof campaign ledger failed integrity validation",
}
SOURCE = b"-- hand source\n"
ATTEMPT = "a" * 32
RESERVE = {
    "kind": "reserve", "attempt": ATTEMPT, "target": "first",
    "source_sha256": hashlib.sha256(SOURCE).hexdigest(), "recheck": False, "denied": False,
}
COMPLETE = {
    "kind": "complete", "attempt": ATTEMPT, "target": "second", "status": "bogus",
    "duration_seconds": 0.0, "logs": 1,
}
# Hand-picked two-defect ledgers, each with the message the frozen code raises.
HAND_LEDGERS = [
    ([{**RESERVE, "attempt": "zz", "target": 1}], "Invalid proof attempt ID"),
    ([RESERVE, {**RESERVE, "target": 1}], "Duplicate proof attempt"),
    ([RESERVE, COMPLETE], "Invalid proof attempt completion"),
    ([{"kind": "bogus", "attempt": "zz"}], "Invalid proof attempt ID"),
]


def _paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _paths(value, (*prefix, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _paths(value, (*prefix, index))


def _at(node, path):
    for step in path:
        node = node[step]
    return node


def _cases(root, samples, *, root_values=True):
    """Every single mutation, as a one-step tuple; the root is replaced only when root_values."""
    steps = [((), "same", None)]
    if root_values:
        steps.extend(((), "set", value) for value in samples)
    for path in _paths(root):
        if isinstance(_at(root, path), dict):
            steps.extend((path, "extra", key) for key in EXTRA_KEYS)
        if path:
            steps.extend((path, "set", value) for value in samples)
            steps.append((path, "del", None))
    return [(step,) for step in steps]


def _pairs(root, samples, rng, count, *, root_values=True):
    """Seeded two-step cases; each second step is drawn from the cases of the first mutated value."""
    firsts = _cases(root, samples, root_values=root_values)
    pairs = []
    for _ in range(count):
        first = rng.choice(firsts)
        second = rng.choice(_cases(_build(root, first), samples, root_values=root_values))
        pairs.append((*first, *second))
    return pairs


def _apply(root, step):
    path, operation, value = step
    changed = copy.deepcopy(root)
    if operation == "set" and not path:
        return copy.deepcopy(value)
    if operation == "extra":
        _at(changed, path)[value] = {"nested": [1, "\ud800"]}
    elif operation == "set":
        _at(changed, path[:-1])[path[-1]] = copy.deepcopy(value)
    elif operation == "del":
        del _at(changed, path[:-1])[path[-1]]
    return changed


def _build(root, steps):
    changed = root
    for step in steps:
        changed = _apply(changed, step)
    return changed


def _outcome(fn, value):
    try:
        return ("ok", fn(value))
    except Exception as exc:  # noqa: BLE001 - every failure kind must match
        return ("error", type(exc).__name__, str(exc))


def _differences(root, cases, old, new):
    found = []
    for steps in cases:
        changed = _build(root, steps)
        left, right = _outcome(old, changed), _outcome(new, changed)
        if left != right:
            found.append((steps, left, right))
    return found


def _publish(work, items):
    """Write a ledger in the layout _ledger reads; each entry is named by its own hash."""
    entries = work / "entries"
    for stale in entries.iterdir():
        stale.unlink()
    for item in items:
        raw = evidence.json_bytes(item)
        (entries / f"{hashlib.sha256(raw).hexdigest()}.json").write_bytes(raw)
    head = hashlib.sha256(evidence.json_bytes(items[-1])).hexdigest() if items else "0" * 64
    (work / "ledger.json").write_bytes(evidence.json_bytes({"length": len(items), "head": head}))


def _chain(entries):
    """Link hand-written entries so each previous is the hash of the entry before it."""
    linked, previous = [], "0" * 64
    for entry in entries:
        item = {**entry, "previous": previous}
        linked.append(item)
        previous = hashlib.sha256(evidence.json_bytes(item)).hexdigest()
    return linked


def test_manifest_shape_matches_the_frozen_hand_check(proof_campaign):  # noqa: F811
    c = proof_campaign
    ident, _ = c.prepare(targets=["first", "second"])
    manifest = evidence.read_json(c.state / "proof-campaigns" / ident / "manifest.json")
    cases = _cases(manifest, SAMPLES)
    cases += _pairs(manifest, SAMPLES, random.Random(SEED), MANIFEST_PAIRS)
    found = _differences(manifest, cases, oracle._manifest_shape, proof._manifest_shape)
    assert not found, found[:5]
    assert len(cases) > 1000
    assert proof._manifest_shape(manifest) is True


def test_checker_result_matches_the_frozen_hand_check(proof_campaign):  # noqa: F811
    c = proof_campaign
    checker = mentat.proof_checker
    results = [
        checker.audit_approved(b"approved"),
        checker.check_submission(b"approved", b"solution", theorem="Proof.first",
                                 allowed_imports=["Init"], permitted_axioms=[], timeout_seconds=120),
    ]

    def old(value):
        return oracle._valid_checker_result(value, c.identity)

    def new(value):
        return proof._valid_checker_result(value, c.identity)

    for result in results:
        assert new(result) is True
        cases = _cases(result, CHECKER_SAMPLES)
        cases += _pairs(result, CHECKER_SAMPLES, random.Random(SEED), CHECKER_PAIRS)
        found = _differences(result, cases, old, new)
        assert not found, found[:5]


def test_ledger_entries_match_the_frozen_hand_checks(proof_campaign, tmp_path):  # noqa: F811
    c = proof_campaign
    ident, _ = c.prepare("task", targets=["first"])
    submission = c.submission("submission", "first")
    c.outcomes.append("rejected")
    assert proof.check(c.state, ident, submission)["status"] == "violation"
    assert proof.check(c.state, ident, submission)["status"] == "pass"
    assert proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])[0][0]["status"] == "pass"
    source = c.state / "proof-campaigns" / ident
    items = proof._ledger(source)
    assert len(items) == 6
    # Only the entries are rewritten per case; submissions and logs are the real campaign's.
    work = tmp_path / "ledger"
    (work / "entries").mkdir(parents=True)
    shutil.copytree(source / "submissions", work / "submissions")
    shutil.copytree(source / "logs", work / "logs")
    # Ledger cases do not replace the root: it is the chain, and entries are its only layout.
    cases = _cases(items, SAMPLES, root_values=False)
    cases += _pairs(items, SAMPLES, random.Random(SEED), LEDGER_PAIRS, root_values=False)
    assert len(cases) == LEDGER_CASE_TOTAL, len(cases)
    found, messages, outcomes, run = [], set(), set(), 0
    for steps in cases:
        changed = _build(items, steps)
        _publish(work, changed)
        left, right = _outcome(oracle._ledger, work), _outcome(proof._ledger, work)
        if left != right:
            found.append((steps, left, right))
        outcomes.add(right[0])
        if right[0] == "error":
            messages.add(right[2])
        run += 1
    assert run == len(cases) == LEDGER_CASE_TOTAL
    assert not found, found[:5]
    assert outcomes == {"ok", "error"}
    assert LEDGER_MESSAGES <= messages, LEDGER_MESSAGES - messages


@pytest.mark.parametrize("entries, message", HAND_LEDGERS)
def test_hand_ledgers_match_the_frozen_hand_checks(tmp_path, entries, message):
    work = tmp_path / "hand"
    (work / "entries").mkdir(parents=True)
    (work / "submissions").mkdir()
    (work / "logs").mkdir()
    (work / "submissions" / f"{ATTEMPT}.lean").write_bytes(SOURCE)
    _publish(work, _chain(entries))
    left, right = _outcome(oracle._ledger, work), _outcome(proof._ledger, work)
    assert left == right
    assert right == ("error", "ValueError", message)


def test_saved_proof_checks_match_the_frozen_hand_checks(proof_record):  # noqa: F811
    _, _, _, record = proof_record
    cases = _cases(record, SAMPLES)
    found = _differences(record, cases, oracle.proof_evidence, evidence.proof_evidence)
    assert not found, found[:5]
    assert len(cases) > 1000
    assert evidence.proof_evidence(record) is None
