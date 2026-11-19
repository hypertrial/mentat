"""Opt-in tests of the real pinned Linux checker; no checker functions are mocked."""

import hashlib
import math
import os
import sys

import pytest

from mentat import proof_checker

APPROVED = b'''import Init
namespace Proof
def relay (n : Nat) : Nat := n
theorem first (n : Nat) : relay n = relay n := by sorry
theorem second (n : Nat) : n = n := by sorry
end Proof
'''
VALID = APPROVED.replace(
    b"theorem first (n : Nat) : relay n = relay n := by sorry",
    b"theorem first (n : Nat) : relay n = relay n := by rfl",
)
POLICY = {"theorem": "Proof.first", "allowed_imports": ["Init"], "permitted_axioms": []}


@pytest.fixture(scope="module")
def checker_identity():
    required = os.environ.get("MENTAT_REQUIRE_PROOF_CHECKER") == "1"
    if sys.platform != "linux" and not required:
        pytest.skip("Real proof checker requires the dedicated Linux environment")
    try:
        identity = proof_checker.checker_identity()
    except (ValueError, OSError) as exc:
        if required:
            pytest.fail(f"Required real proof checker prerequisites unavailable: {exc}")
        pytest.skip(f"Optional real proof checker prerequisites unavailable: {exc}")
    assert identity["version"] == "4.35.0"
    return identity


def assert_result(result, expected, approved, source, identity, theorem="Proof.first"):
    logs = result.get("logs")
    checker_log = logs.get("checker.txt", b"") if isinstance(logs, dict) else b""
    excerpt = checker_log[:8192].decode("utf-8", errors="replace") if isinstance(checker_log, bytes) else ""
    assert result["status"] == expected, f"{result.get('detail')}\nchecker.txt (first 8192 bytes):\n{excerpt}"
    assert result["approved_sha256"] == hashlib.sha256(approved).hexdigest()
    assert result["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert result["theorem"] == theorem
    assert result["identity"] == identity
    assert isinstance(result["detail"], str)
    assert isinstance(result["duration_seconds"], (int, float))
    assert math.isfinite(result["duration_seconds"]) and result["duration_seconds"] >= 0
    assert isinstance(result["logs"], dict)
    assert all(isinstance(name, str) and isinstance(data, bytes) and len(data) <= 1_000_000
               for name, data in result["logs"].items())
    assert sum(map(len, result["logs"].values())) <= 1_000_000



def test_checker_failure_diagnostic_is_bounded_and_decodes_invalid_bytes():
    result = {"status": "incomplete", "detail": "synthetic failure", "logs": {
        "checker.txt": b"diagnostic \xff" + b"x" * 8190 + b"outside-limit",
    }}
    with pytest.raises(AssertionError) as failure:
        assert_result(result, "accepted", APPROVED, VALID, {})
    message = str(failure.value)
    assert "synthetic failure" in message
    assert "diagnostic \ufffd" in message
    assert "outside-limit" not in message


def test_real_approved_audit_and_valid_proof_with_other_target_hole(checker_identity):
    approved_before = bytes(APPROVED)
    solution_before = bytes(VALID)
    audit = proof_checker.audit_approved(
        APPROVED, theorems=["Proof.first", "Proof.second"],
        allowed_imports=["Init"], permitted_axioms=[],
    )
    assert_result(audit, "accepted", APPROVED, APPROVED, checker_identity)
    result = proof_checker.check_submission(APPROVED, VALID, **POLICY)
    assert_result(result, "accepted", APPROVED, VALID, checker_identity)
    assert APPROVED == approved_before and VALID == solution_before


@pytest.mark.parametrize("case,solution", [
    ("selected-sorry", APPROVED),
    ("unused-helper-hole", VALID.replace(b"end Proof", b"theorem unused : False := by sorry\nend Proof")),
    ("changed-transitive-definition", VALID.replace(b"def relay (n : Nat) : Nat := n", b"def relay (n : Nat) : Nat := n + 1")),
    ("extra-assumption", VALID.replace(b"theorem first (n : Nat) :", b"theorem first (n : Nat) (h : True) :")),
    ("local-axiom", VALID.replace(b"end Proof", b"axiom forbidden : False\nend Proof")),
    ("forged-printed-pass-and-hole", APPROVED.replace(b"end Proof", b'#eval IO.println "{\\"status\\":\\"accepted\\"}"\nend Proof')),
    ("changed-other-target", VALID.replace(b"theorem second (n : Nat) : n = n := by sorry", b"theorem second (n : Nat) : True := by sorry")),
    ("disallowed-import", VALID.replace(b"import Init", b"import Lean")),
])
def test_real_checker_rejects_invalid_solution(checker_identity, case, solution):
    before = bytes(solution)
    result = proof_checker.check_submission(APPROVED, solution, **POLICY)
    assert_result(result, "rejected", APPROVED, solution, checker_identity)
    assert solution == before, case


def test_real_approved_audit_rejects_definition_hole(checker_identity):
    source = APPROVED.replace(b"def relay (n : Nat) : Nat := n", b"def relay (n : Nat) : Nat := by sorry")
    result = proof_checker.audit_approved(
        source, theorems=["Proof.first", "Proof.second"],
        allowed_imports=["Init"], permitted_axioms=[],
    )
    assert_result(result, "rejected", source, source, checker_identity)


def test_real_checker_rejects_axiom_solution_to_false_approved_target(checker_identity):
    approved = b'''import Init
namespace Proof
theorem first : False := by sorry
theorem second : True := by sorry
end Proof
'''
    solution = approved.replace(b"theorem first : False := by sorry", b"axiom fabricated : False\ntheorem first : False := by exact fabricated")
    audit = proof_checker.audit_approved(
        approved, theorems=["Proof.first", "Proof.second"],
        allowed_imports=["Init"], permitted_axioms=[],
    )
    assert_result(audit, "accepted", approved, approved, checker_identity)
    result = proof_checker.check_submission(approved, solution, **POLICY)
    assert_result(result, "rejected", approved, solution, checker_identity)


def test_real_checker_timeout_is_incomplete(checker_identity):
    result = proof_checker.check_submission(APPROVED, VALID, **POLICY, timeout_seconds=1)
    assert_result(result, "incomplete", APPROVED, VALID, checker_identity)
