import copy

import pytest
from pydantic import ValidationError

from mentat.project import Contract

CITATION = {"commit": "a" * 40, "blob": "b" * 40, "path": "src/example.py", "start_line": 1, "end_line": 2}


def contract():
    return {
        "version": 1,
        "allow_host": True,
        "claims": [
            {"id": "alpha", "statement": "Primary claim", "assumptions": ["trusted input"],
             "provenance": {"origin": "declared", "citations": [CITATION]}},
            {"id": "beta", "statement": "Supporting claim"},
        ],
        "formal": [{"id": "proof", "claim": "alpha", "engine": "z3", "source": "model.smt2", "expected": "sat"}],
        "behavior": [
            {"id": "native", "claim": "alpha", "argv": ["python3", "{checks}/check.py"], "artifacts": ["cases.json"],
             "case_report": {"artifact": "cases.json", "required_cases": ["one", "two"]}},
            {"id": "support", "claim": "beta", "argv": ["python3", "{checks}/check.py"]},
        ],
    }


def assurance():
    return {
        "implementation_checks": [{"check_id": "native", "case_ids": ["one"]}, {"check_id": "support"}],
        "witness_checks": [{"check_id": "proof"}, {"check_id": "native", "case_ids": ["two"]}],
        "bounds": [
            {"id": "enforced", "statement": "Bounded behavior", "kind": "enforced", "checks": [{"check_id": "native", "case_ids": ["one"]}]},
            {"id": "assumed", "statement": "Input boundary", "kind": "assumed", "assumption": "trusted input"},
        ],
        "exclusions": [{"scenario": "Replay", "reason": "Not modeled", "effect": "No guarantee", "affected_claims": ["alpha", "beta"]}],
        "deviations": [{"citation": CITATION, "rationale": "Different environment", "effect": "Limited claim"}],
    }


def with_assurance():
    value = contract()
    value["claims"][0]["assurance"] = assurance()
    return value


def test_assurance_allows_shared_checks_cases_and_existing_citation():
    parsed = Contract.model_validate(with_assurance())
    item = parsed.claims[0].assurance
    assert item is not None
    assert [ref.check_id for ref in item.implementation_checks] == ["native", "support"]
    assert item.witness_checks[1].case_ids == ["two"]
    assert item.bounds[1].assumption == "trusted input"
    assert item.deviations[0].citation.model_dump() == CITATION


def test_absent_assurance_and_source_mutations_do_not_appear_in_legacy_dump():
    parsed = Contract.model_validate(contract())
    dumped = parsed.model_dump(mode="json", exclude_none=True)
    assert all("assurance" not in claim for claim in dumped["claims"])
    assert "source_mutations" not in dumped


@pytest.mark.parametrize("change", [
    lambda a: a["implementation_checks"].append({"check_id": "proof"}),
    lambda a: a["implementation_checks"].append({"check_id": "unknown"}),
    lambda a: a["implementation_checks"][0].update(case_ids=["missing"]),
    lambda a: a["implementation_checks"][1].update(case_ids=["one"]),
    lambda a: a["witness_checks"][0].update(case_ids=["one"]),
    lambda a: a["witness_checks"][1].update(case_ids=["one", "one"]),
    lambda a: a["bounds"][0].update(checks=[]),
    lambda a: a["bounds"][0].update(assumption="trusted input"),
    lambda a: a["bounds"][1].update(checks=[{"check_id": "native"}]),
    lambda a: a["bounds"][1].update(assumption="invented"),
    lambda a: a["bounds"].append(copy.deepcopy(a["bounds"][0])),
    lambda a: a["exclusions"][0].update(affected_claims=[]),
    lambda a: a["exclusions"][0].update(affected_claims=["unknown"]),
    lambda a: a["exclusions"][0].update(affected_claims=["alpha", "alpha"]),
    lambda a: a["deviations"][0].update(citation={**CITATION, "blob": "c" * 40}),
    lambda a: a["witness_checks"][0].update(unrecognized=True),
    lambda a: a.update(unrecognized=True),
], ids=["formal-implementation", "missing-check", "unknown-case", "case-without-inventory", "formal-case", "duplicate-case", "enforced-no-checks", "enforced-assumption", "assumed-checks", "unknown-assumption", "duplicate-bound", "empty-exclusions", "unknown-exclusion", "duplicate-exclusion", "unlisted-citation", "unknown-ref-field", "unknown-assurance-field"])
def test_rejects_invalid_assurance_relationships(change):
    value = with_assurance()
    change(value["claims"][0]["assurance"])
    with pytest.raises((ValueError, ValidationError)):
        Contract.model_validate(value)


@pytest.mark.parametrize("path", ["../mutants.json", "/tmp/mutants.json", ".git/config", "x/../m.json", "", {"path": "mutants.json"}])
def test_source_mutations_must_be_a_safe_direct_path(path):
    value = contract()
    value["source_mutations"] = path
    with pytest.raises((ValueError, ValidationError)):
        Contract.model_validate(value)


def comparator():
    return {"id": "proof", "claim": "alpha", "engine": "lean-comparator", "source": "proof.lean", "theorem": "Example.theorem", "allowed_imports": ["Init", "Std.Data", "Lean"], "permitted_axioms": ["propext", "Classical.choice", "Quot.sound"]}


def test_comparator_accepts_explicit_lean_policy():
    value = contract()
    value["formal"] = [comparator()]
    assert Contract.model_validate(value).formal[0].engine == "lean-comparator"


@pytest.mark.parametrize("change", [
    lambda c: c.pop("theorem"),
    lambda c: c.pop("allowed_imports"),
    lambda c: c.pop("permitted_axioms"),
    lambda c: c.update(allowed_imports=["Other.Module"]),
    lambda c: c.update(allowed_imports=["LeanEvil"]),
    lambda c: c.update(permitted_axioms=["unsafeAxiom"]),
    lambda c: c.update(expected="sat"),
    lambda c: c.update(counterexample="other.smt2"),
    lambda c: c.update(allowed_imports=["Std", "Std"]),
])
def test_comparator_rejects_invalid_or_missing_policy(change):
    value = contract()
    value["formal"] = [comparator()]
    change(value["formal"][0])
    with pytest.raises((ValueError, ValidationError)):
        Contract.model_validate(value)


@pytest.mark.parametrize("engine", ["z3", "lean"])
def test_old_formal_engines_forbid_comparator_policy(engine):
    value = contract()
    if engine == "lean":
        value["formal"] = [{"id": "proof", "claim": "alpha", "engine": "lean", "source": "proof.lean", "theorem": "thm"}]
    value["formal"][0]["allowed_imports"] = []
    with pytest.raises((ValueError, ValidationError)):
        Contract.model_validate(value)


def test_future_approval_fingerprint_includes_qualification_verifier(tmp_path, monkeypatch):
    import json
    import shutil
    import subprocess
    from pathlib import Path

    import mentat.project as project_module

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.test"], check=True)
    checks = repo / ".mentat"
    checks.mkdir()
    value = contract()
    (checks / "contract.json").write_text(json.dumps(value))
    (checks / "model.smt2").write_text("(assert true)\n")
    (checks / "check.py").write_text("pass\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    verifier = tmp_path / "verifier"
    verifier.mkdir()
    for path in Path(project_module.__file__).parent.glob("*.py"):
        shutil.copyfile(path, verifier / path.name)
    monkeypatch.setattr(project_module, "__file__", str(verifier / "project.py"))
    before = project_module.load_project(repo, base, "host")
    path = verifier / "qualification.py"
    path.write_bytes(path.read_bytes() + b"\n# verifier version change\n")
    after = project_module.load_project(repo, base, "host")
    assert after.verifier_digest != before.verifier_digest
    assert after.fingerprint != before.fingerprint


def test_legacy_claim_render_does_not_invent_assurance_sections():
    from mentat.claims import render_claims

    rendered = render_claims(Contract.model_validate(contract())).decode()
    assert "Primary claim" in rendered and "Supporting claim" in rendered
    assert "Assurance" not in rendered and "Source mutations" not in rendered
