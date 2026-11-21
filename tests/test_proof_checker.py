"""Opt-in tests of the real pinned Linux checker; no checker functions are mocked."""

import ast
import fcntl
import hashlib
import math
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

from mentat import evidence, project_formal, proof_checker

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



@pytest.fixture(autouse=True)
def one_real_check_at_a_time():
    # The real checker refuses to start beside another one on this host, and parallel
    # test workers may run these tests at the same time. Wait for a turn instead.
    path = Path(tempfile.gettempdir()) / f"mentat-test-proof-checker-{os.getuid()}.lock"
    with open(path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


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



def _driver_helpers():
    # Executes the real staging and build helpers from the driver source; no Linux service runs.
    tree = ast.parse(proof_checker._DRIVER)
    names = {"Incomplete", "stage_olean", "build", "LEAN", "ENV", "WORK", "INPUT"}

    def wanted(node):
        if isinstance(node, ast.Assign):
            return any(isinstance(name, ast.Name) and name.id in names
                       for target in node.targets for name in ast.walk(target))
        return isinstance(node, (ast.Import, ast.ImportFrom)) or getattr(node, "name", "") in names
    # The driver's own imports are executed too, so a name the helpers need but the driver
    # does not import fails here instead of only inside the Linux service.
    body = [node for node in tree.body if wanted(node)]
    namespace = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), "<driver>", "exec"), namespace)
    return namespace


def test_driver_stages_only_the_compiled_module_into_a_fresh_folder(tmp_path):
    build = tmp_path / ".lake"
    (build / "LeanExport").mkdir(parents=True)
    (build / "LeanExport" / "Basic.olean").write_bytes(b"planted exporter")
    (build / "Solution.olean").write_bytes(b"compiled module")
    folder = tmp_path / "Solution-export"
    _driver_helpers()["stage_olean"](str(build / "Solution.olean"), str(folder))
    assert sorted(path.name for path in folder.iterdir()) == ["Solution.olean"]
    assert (folder / "Solution.olean").read_bytes() == b"compiled module"


def test_driver_build_exports_only_the_staged_module(tmp_path):
    helpers = _driver_helpers()
    work, inputs = tmp_path / "work", tmp_path / "input"
    work.mkdir()
    inputs.mkdir()
    (inputs / "Solution.lean").write_text("import Init\n")
    calls = []

    def sandbox(args, project, *, writable=False, env=None, export=None):
        calls.append((args, env))
        if writable:
            # The compile step writes its module and a planted exporter library into .lake.
            (Path(project) / ".lake" / "LeanExport").mkdir(parents=True)
            (Path(project) / ".lake" / "Solution.olean").write_bytes(b"compiled module")
            (Path(project) / ".lake" / "LeanExport" / "Basic.olean").write_bytes(b"planted exporter")
        return 0, b""

    helpers.update(WORK=str(work), INPUT=str(inputs), sandbox=sandbox)
    helpers["build"]("Solution")
    assert len(calls) == 3
    exporter_args, exporter_env = calls[1]
    assert exporter_args[1] == "--run"
    folder = work / "Solution-export"
    assert exporter_env["LEAN_PATH"] == str(folder)
    assert sorted(path.name for path in folder.iterdir()) == ["Solution.olean"]
    assert (folder / "Solution.olean").read_bytes() == b"compiled module"
    assert all(".lake" not in value for _, env in calls[1:] for value in (env or {}).values())


@pytest.mark.parametrize("case", ["symlink", "missing", "oversized", "directory", "fifo"])
def test_driver_refuses_unsafe_compiled_module_before_creating_export_folder(tmp_path, case):
    helpers = _driver_helpers()
    source = tmp_path / "Solution.olean"
    if case == "symlink":
        (tmp_path / "real.olean").write_bytes(b"compiled module")
        source.symlink_to(tmp_path / "real.olean")
    elif case == "oversized":
        source.write_bytes(b"x" * 9)
    elif case == "directory":
        source.mkdir()
    elif case == "fifo":
        os.mkfifo(source)
    folder = tmp_path / "Solution-export"
    with pytest.raises(helpers["Incomplete"]) as refused:
        helpers["stage_olean"](str(source), str(folder), limit=8)
    assert str(refused.value) == "Lean source compilation did not complete"
    assert not folder.exists()


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
    ("eval-file-write", APPROVED.replace(b"end Proof", b'#eval do\n  IO.FS.createDirAll ".lake/LeanExport"\n  IO.FS.writeFile ".lake/LeanExport/Basic.olean" "forged"\nend Proof')),
    ("include-str-host-file", APPROVED.replace(b"end Proof", b'example : Nat := include_str "/etc/hostname"\nend Proof')),
    ("native-decide", APPROVED.replace(b"end Proof", b"example : True := by native_decide\nend Proof")),
    ("by-elab-term", APPROVED.replace(b"end Proof", b"example : True := by_elab pure (Lean.mkConst ``True.intro)\nend Proof")),
    ("unsafe-term", APPROVED.replace(b"end Proof", b"example : Nat := unsafe 1\nend Proof")),
    ("tactic-set-option-pp", APPROVED.replace(b"end Proof", b"example : True := by\n  set_option pp.all true in trivial\nend Proof")),
    ("initialize", APPROVED.replace(b"end Proof", b"initialize counter : IO.Ref Nat <- IO.mkRef 0\nend Proof")),
    ("implemented-by-attribute", VALID.replace(b"def relay (n : Nat) : Nat := n", b"@[implemented_by relay] def relay (n : Nat) : Nat := n")),
    ("disallowed-set-option", VALID.replace(b"namespace Proof", b"set_option pp.all true\nnamespace Proof")),
    ("macro", VALID.replace(b"end Proof", b'macro "forged" : term => `(0)\nend Proof')),
    ("changed-other-target", VALID.replace(b"theorem second (n : Nat) : n = n := by sorry", b"theorem second (n : Nat) : True := by sorry")),
    ("disallowed-import", VALID.replace(b"import Init", b"import Lean")),
])
def test_real_checker_rejects_invalid_solution(checker_identity, case, solution):
    before = bytes(solution)
    result = proof_checker.check_submission(APPROVED, solution, **POLICY)
    assert_result(result, "rejected", APPROVED, solution, checker_identity)
    assert solution == before, case


def test_real_checker_rejects_tactic_metaprogramming_under_lean_import(checker_identity):
    approved = b'''import Lean
namespace Proof
def relay (n : Nat) : Nat := n
theorem first (n : Nat) : relay n = relay n := by sorry
theorem second (n : Nat) : n = n := by sorry
end Proof
'''
    solution = approved.replace(b"by sorry\ntheorem second", b"by run_tac pure ()\ntheorem second")
    policy = {"theorem": "Proof.first", "allowed_imports": ["Lean"], "permitted_axioms": []}
    audit = proof_checker.audit_approved(
        approved, theorems=["Proof.first", "Proof.second"],
        allowed_imports=["Lean"], permitted_axioms=[],
    )
    assert_result(audit, "accepted", approved, approved, checker_identity)
    result = proof_checker.check_submission(approved, solution, **policy)
    assert_result(result, "rejected", approved, solution, checker_identity)


def test_real_checker_accepts_helper_lemma_section_and_scoped_commands(checker_identity):
    solution = b'''import Init
namespace Proof
def relay (n : Nat) : Nat := n
@[simp] theorem relay_eq (n : Nat) : relay n = n := rfl
section
theorem first (n : Nat) : relay n = relay n := by rw [relay_eq]
end
open Nat in
set_option maxHeartbeats 400000 in
theorem second (n : Nat) : n = n := by sorry
end Proof
'''
    result = proof_checker.check_submission(APPROVED, solution, **POLICY)
    assert_result(result, "accepted", APPROVED, solution, checker_identity)


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


@pytest.mark.parametrize("machine,digest,accepted", [
    ("x86_64", "pinned", True),
    ("aarch64", "pinned", True),
    ("x86_64", "f" * 64, False),
    ("aarch64", proof_checker.TOOLCHAIN_SHA256["x86_64"], False),
    ("riscv64", proof_checker.TOOLCHAIN_SHA256["x86_64"], False),
])
def test_only_the_pinned_toolchain_for_this_architecture_is_accepted(
    monkeypatch, machine, digest, accepted
):
    digest = proof_checker.TOOLCHAIN_SHA256[machine] if digest == "pinned" else digest
    monkeypatch.setattr(proof_checker.platform, "machine", lambda: machine)
    monkeypatch.setattr(proof_checker, "_toolchain_digest", lambda: digest)
    if accepted:
        assert proof_checker._pinned_toolchain_digest() == digest
    else:
        with pytest.raises(ValueError, match="does not match the pinned official distribution$"):
            proof_checker._pinned_toolchain_digest()


def test_toolchain_pins_cover_the_installer_architectures():
    installer = (Path(__file__).parents[1] / "scripts/install-proof-checker").read_text()
    architectures = set(re.findall(r"^  (\w+)\)\n    proof_archive=", installer, re.MULTILINE))
    assert architectures == set(proof_checker.TOOLCHAIN_SHA256) == {"x86_64", "aarch64"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in proof_checker.TOOLCHAIN_SHA256.values())
    assert len(set(proof_checker.TOOLCHAIN_SHA256.values())) == 2


def test_pinned_lean_versions_are_explicit():
    # A version bump must be a reviewed change to these literals.
    assert project_formal.LEAN_VERSION == "4.31.0"
    assert proof_checker.LEAN_VERSION == "4.35.0"


def test_saved_proof_checker_versions_include_the_current_checker():
    assert proof_checker.LEAN_VERSION in evidence._SAVED_CHECKER_VERSIONS


def test_driver_sandbox_paths_use_the_pinned_lean_version():
    paths = re.findall(r"/opt/lean-([^/'\"\s]+)", proof_checker._DRIVER)
    assert paths and set(paths) == {proof_checker.LEAN_VERSION}
