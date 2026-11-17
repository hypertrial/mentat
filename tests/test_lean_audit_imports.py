import shutil
import subprocess
from types import SimpleNamespace

import pytest

from mentat import project_formal


@pytest.fixture(scope="module")
def lean_tools():
    executable = shutil.which("lean")
    if not executable:
        pytest.skip("Lean 4.31.0 is unavailable")
    version = subprocess.run([executable, "--version"], text=True, capture_output=True, check=True)
    if "version 4.31.0" not in version.stdout:
        pytest.skip("Lean 4.31.0 is required")
    libdir = subprocess.run([executable, "--print-libdir"], text=True, capture_output=True, check=True)
    return {"lean": {"version": "4.31.0", "executable": executable, "libdir": libdir.stdout.strip()}}


def audit(source, theorem, tools):
    check = SimpleNamespace(id="proof", claim="claim", source="Model.lean", theorem=theorem)
    return project_formal._run_lean(check, {"Model.lean": source.encode()}, tools)


@pytest.mark.parametrize(("source", "theorem"), [
    ("import Std\nnamespace Trial\ntheorem α₀! : True := trivial\nend Trial\n", "Trial.α₀!"),
    ("import Lean\nnamespace Trial\ntheorem name_eq : Lean.Name.anonymous = Lean.Name.anonymous := rfl\nend Trial\n", "Trial.name_eq"),
    ("import Lean.Elab.Print\nnamespace Trial\ntheorem checked : True := trivial\nend Trial\n", "Trial.checked"),
])
def test_standard_imports_and_qualified_theorems_are_audited(lean_tools, source, theorem):
    result = audit(source, theorem, lean_tools)
    assert result["status"] == "pass", result
    assert result["evidence"]["theorem"] == theorem
    assert result["evidence"]["axioms"] == []


@pytest.mark.parametrize(("source", "detail"), [
    ("theorem verified : False := by sorry\n", "disallowed axioms"),
    ("import NoSuchModule\ntheorem verified : True := trivial\n", "standard library"),
    ("import Std\ntheorem verified : False := by exact True.intro\n", "did not compile"),
])
def test_failed_source_or_sorry_cannot_pass(lean_tools, source, detail):
    result = audit(source, "verified", lean_tools)
    assert result["status"] == "incomplete", result
    assert detail in result["detail"]


def test_duplicate_lean_audit_output_is_rejected(lean_tools, monkeypatch):
    original = project_formal._run

    def duplicate(*args, **kwargs):
        result = original(*args, **kwargs)
        if str(args[0][-1]).endswith("Audit.lean"):
            return subprocess.CompletedProcess(result.args, result.returncode,
                                               result.stdout + result.stdout, result.stderr)
        return result

    monkeypatch.setattr(project_formal, "_run", duplicate)
    result = audit("import Std\ntheorem verified : True := trivial\n", "verified", lean_tools)
    assert result["status"] == "incomplete", result
    assert "unambiguous" in result["detail"]


def test_source_cannot_forge_axiom_audit_with_shadowed_print(lean_tools):
    source = '''import Lean
axiom bad : False
theorem verified : False := bad
syntax (name := forgedPrint) (priority := high) "#print" " axioms " ident : command
macro_rules (kind := forgedPrint)
  | `(#print axioms $x:ident) => `(#eval IO.println "'verified' does not depend on any axioms")
'''
    result = audit(source, "verified", lean_tools)
    assert result["status"] == "incomplete", result


def test_import_free_qualified_unicode_theorem(lean_tools):
    result = audit("namespace Example\ntheorem α₁ : True := trivial\nend Example\n", "Example.α₁", lean_tools)
    assert result["status"] == "pass", result
    assert result["evidence"]["axioms"] == []


@pytest.mark.parametrize("declaration", [
    "private theorem helper : False := forbidden",
    "opaque helper : False := forbidden",
])
def test_transitive_disallowed_axioms_are_rejected(lean_tools, declaration):
    source = f"axiom forbidden : False\n{declaration}\ntheorem verified : False := helper\n"
    result = audit(source, "verified", lean_tools)
    assert result["status"] == "incomplete", result
    assert "forbidden" in result["evidence"]["axioms"]


def test_source_cannot_shadow_origin_guard(lean_tools):
    source = '''import Lean
syntax (name := forgedRun) (priority := high) "run_cmd " term : command
macro_rules (kind := forgedRun)
  | `(run_cmd $x:term) => `(#eval pure ())
'''
    result = audit(source, "Nat.add_zero", lean_tools)
    assert result["status"] == "incomplete", result


@pytest.mark.parametrize("tamper", ["extra-output", "run-failure", "run-timeout"])
def test_driver_unexpected_output_and_failure_are_incomplete(lean_tools, monkeypatch, tamper):
    original = project_formal._run
    invoked = []

    def altered(*args, **kwargs):
        argv = args[0]
        if "--run" in argv and str(argv[-1]).endswith("Audit.lean"):
            invoked.append(True)
            if tamper == "run-timeout":
                raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 15))
            result = original(*args, **kwargs)
            if tamper == "run-failure":
                return subprocess.CompletedProcess(result.args, 1, result.stdout, "driver error")
            return subprocess.CompletedProcess(result.args, result.returncode,
                                               result.stdout + "unrelated output\n", result.stderr)
        return original(*args, **kwargs)

    monkeypatch.setattr(project_formal, "_run", altered)
    result = audit("theorem verified : True := trivial\n", "verified", lean_tools)
    assert invoked, "the trusted driver was not run"
    assert result["status"] == "incomplete", result
