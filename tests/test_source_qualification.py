import copy
import hashlib
import json
import os
import sys
from dataclasses import replace

import pytest

from mentat import runner
from mentat.evidence import json_bytes
from mentat.project import Contract, Project, Snapshot, _digest
from mentat.qualification import approved_mutations, run_source_mutations

SCRIPT = '''import json, os, sys, time
from pathlib import Path
mode = Path("source.py").read_text().strip()
if sys.argv[1:] == ["--control"]:
    mode = "killed"
if mode == "timeout":
    time.sleep(3)
if mode == "bad-json":
    Path(os.environ["MENTAT_ARTIFACTS_DIR"], "cases.json").write_text("{")
    sys.exit(10)
if mode == "missing":
    cases = {"first": "pass"}
elif mode == "skip":
    cases = {"first": "fail", "second": "skip"}
elif mode == "error":
    cases = {"first": "fail", "second": "error"}
elif mode == "wrongcode":
    cases = {"first": "fail", "second": "pass"}
elif mode == "killed":
    cases = {"first": "fail", "second": "pass"}
elif mode == "both":
    cases = {"first": "fail", "second": "fail"}
elif mode == "other":
    cases = {"first": "pass", "second": "fail"}
else:
    cases = {"first": "pass", "second": "pass"}
Path(os.environ["MENTAT_ARTIFACTS_DIR"], "cases.json").write_text(json.dumps({"schema_version": 1, "cases": cases}))
print("synthetic output")
sys.exit(1 if mode == "wrongcode" else 10 if mode in ("killed", "both", "other", "skip", "error") else 0)
'''


def mutation(**changes):
    return {"check": "behavior", "path": "source.py", "before": "baseline", "after": "killed", "expected_case": "first", **changes}


def project(tmp_path, mutants=None, **behavior_changes):
    check = {"id": "behavior", "claim": "claim", "argv": [sys.executable, "{checks}/check.py"],
             "timeout_seconds": 30, "violation_exit_codes": [10], "artifacts": ["cases.json"],
             "case_report": {"artifact": "cases.json", "required_cases": ["first", "second"]},
             **behavior_changes}
    contract = Contract.model_validate({
        "version": 1, "allow_host": True, "source_mutations": "mutants.json",
        "claims": [{"id": "claim", "statement": "candidate is valid"}],
        "formal": [{"id": "proof", "claim": "claim", "engine": "z3", "source": "proof.smt2", "expected": "sat"}],
        "behavior": [check],
    })
    manifest = json.dumps({"version": 1, "mutants": mutants if mutants is not None else {"mutant": mutation()}}).encode()
    files = {"check.py": SCRIPT.encode(), "mutants.json": manifest}
    candidate_files = {"source.py": (0o644, b"baseline")}
    candidate = Snapshot(revision="a" * 40, digest=_digest(candidate_files), files=candidate_files)
    return Project(root=tmp_path, base="b" * 40, runner="host", contract=contract, files=files,
                   fingerprint="c" * 64, verifier_digest="d" * 64), candidate


def execute(project, candidate, baseline=None, info=None):
    info = info or {"runner": "host", "path": os.environ.get("PATH", ""), "executables": {}}
    if baseline is None:
        baseline, _ = runner.run_behavior(project, candidate, None, info, "run-identifier")
    return run_source_mutations(project, candidate, None, info, "run-identifier", baseline)


@pytest.mark.parametrize(("mode", "expected_status", "expected_outcome", "failed"), [
    ("killed", "pass", "killed", True),
    ("both", "pass", "killed", True),
    ("survived", "violation", "survived", False),
    ("other", "incomplete", "error", False),
    ("missing", "incomplete", "error", False),
    ("skip", "incomplete", "error", True),
    ("error", "incomplete", "error", True),
    ("wrongcode", "incomplete", "error", True),
    ("bad-json", "incomplete", "error", False),
    ("timeout", "incomplete", "error", None),
])
def test_real_host_qualification_requires_the_named_failure(tmp_path, mode, expected_status, expected_outcome, failed):
    # Only the timeout case needs a short limit; the rest must not race a loaded machine.
    limit = {"timeout_seconds": 1} if mode == "timeout" else {}
    project_value, candidate = project(tmp_path, {"example": mutation(after=mode)}, **limit)
    before = copy.deepcopy(candidate.files)
    info = {"runner": "host", "path": os.environ.get("PATH", ""), "executables": {}}
    baseline, _ = runner.run_behavior(project_value, candidate, None, info, "run-identifier")
    assert baseline[0]["status"] == "pass"
    results, files = execute(project_value, candidate, baseline, info)
    assert len(results) == 1
    result = results[0]
    assert (result["status"], result["outcome"]) == (expected_status, expected_outcome)
    assert (result["id"], result["claim"], result["check"], result["expected_case"]) == ("example", "claim", "behavior", "first")
    assert result["applied_count"] == 1
    if failed is not None:
        assert result["assertion_failed"] is failed
    assert candidate.files == before
    bindings = result["bindings"]
    assert bindings["run_id"] == "run-identifier"
    assert bindings["revision"] == candidate.revision and bindings["snapshot_digest"] == candidate.digest
    assert bindings["base"] == project_value.base and bindings["contract_digest"] == project_value.fingerprint
    assert bindings["verifier_digest"] == project_value.verifier_digest
    assert bindings["approved_manifest_sha256"] == hashlib.sha256(project_value.files["mutants.json"]).hexdigest()
    assert bindings["source_before_sha256"] == hashlib.sha256(b"baseline").hexdigest()
    assert bindings["source_after_sha256"] == hashlib.sha256(mode.encode()).hexdigest()
    assert bindings["runtime"]["runner"] == "host"
    assert bindings["runtime"]["sha256"] == hashlib.sha256(json_bytes(info)).hexdigest()
    assert all(name.startswith("mutation-001.") for name in files)
    if mode in {"killed", "both", "survived", "other"}:
        report = result["check_result"]["artifacts"]["cases.json"]
        assert report in files and report.startswith("mutation-001.")
        assert json.loads(files[report])["cases"] == result["case_results"]
        stdout = result["check_result"]["stdout"]
        assert stdout.startswith("mutation-001.") and files[stdout] == b"synthetic output\n"


@pytest.mark.parametrize("baseline", [
    [],
    [{"id": "behavior", "claim": "claim", "status": "violation", "command_status": "violation", "exit_code": 10, "case_results": {"first": "fail", "second": "pass"}}],
    [{"id": "behavior", "claim": "claim", "status": "pass", "command_status": "pass", "exit_code": 0, "case_results": {"first": "pass"}}],
    [{"id": "behavior", "claim": "claim", "status": "pass", "command_status": "pass", "exit_code": 0, "case_results": {"first": "pass", "second": "skip"}}],
    [{"id": "behavior", "claim": "another", "status": "pass", "command_status": "pass", "exit_code": 0, "case_results": {"first": "pass", "second": "pass"}}],
])
def test_unverified_baseline_never_runs_mutation(tmp_path, baseline):
    project_value, candidate = project(tmp_path)
    results, artifacts = execute(project_value, candidate, baseline)
    assert results[0]["status"] == "incomplete" and results[0]["applied_count"] == 0
    assert not artifacts


@pytest.mark.parametrize("source", [b"missing", b"baseline baseline", b"baselinebaseline"])
def test_missing_or_multiple_matches_are_incomplete_without_mutation(tmp_path, source):
    project_value, candidate = project(tmp_path)
    baseline = [{"id": "behavior", "claim": "claim", "status": "pass", "command_status": "pass", "exit_code": 0, "case_results": {"first": "pass", "second": "pass"}}]
    files = {"source.py": (0o644, source)}
    candidate = replace(candidate, files=files, digest=_digest(files))
    results, artifacts = execute(project_value, candidate, baseline)
    assert results[0]["status"] == "incomplete" and results[0]["applied_count"] == 0
    assert not artifacts and candidate.files == files


def test_overlapping_byte_occurrences_are_not_unique(tmp_path):
    project_value, candidate = project(tmp_path, {"overlap": mutation(before="aaa", after="b")})
    files = {"source.py": (0o644, b"aaaa")}
    candidate = replace(candidate, files=files, digest=_digest(files))
    baseline = [{"id": "behavior", "claim": "claim", "status": "pass", "command_status": "pass", "exit_code": 0, "case_results": {"first": "pass", "second": "pass"}}]
    results, artifacts = execute(project_value, candidate, baseline)
    assert results[0]["status"] == "incomplete" and results[0]["applied_count"] == 0
    assert not artifacts and candidate.files == files


@pytest.mark.parametrize("path", ["tests/check.py", "src/__tests__/x.js", ".mentat/check.py", "package.json", "src/code.test.ts", "../escape"])
def test_manifest_rejects_protected_or_unsafe_targets(tmp_path, path):
    project_value, _ = project(tmp_path, {"mutant": mutation(path=path)})
    with pytest.raises(ValueError):
        approved_mutations(project_value.contract, project_value.files)


def test_manifest_rejects_target_outside_declared_case_inventory(tmp_path):
    project_value, _ = project(tmp_path, {"mutant": mutation(expected_case="other")})
    with pytest.raises(ValueError):
        approved_mutations(project_value.contract, project_value.files)


def test_symlink_target_is_not_mutated(tmp_path):
    project_value, candidate = project(tmp_path)
    files = {"source.py": (0o120000, b"elsewhere")}
    candidate = replace(candidate, files=files, digest=_digest(files))
    baseline = [{"id": "behavior", "claim": "claim", "status": "pass", "command_status": "pass", "exit_code": 0, "case_results": {"first": "pass", "second": "pass"}}]
    results, artifacts = execute(project_value, candidate, baseline)
    assert results[0]["status"] == "incomplete" and results[0]["applied_count"] == 0
    assert not artifacts and candidate.files == files


def test_mutant_artifact_names_and_references_remain_distinct(tmp_path):
    project_value, candidate = project(tmp_path, {
        "kill": mutation(after="killed"), "survive": mutation(after="survived"),
    })
    results, artifacts = execute(project_value, candidate)
    assert [(result["id"], result["outcome"]) for result in results] == [
        ("kill", "killed"), ("survive", "survived")
    ]
    first = results[0]["check_result"]["artifacts"]["cases.json"]
    second = results[1]["check_result"]["artifacts"]["cases.json"]
    assert first.startswith("mutation-001.") and second.startswith("mutation-002.")
    assert first != second and first in artifacts and second in artifacts
    assert json.loads(artifacts[first])["cases"] == {"first": "fail", "second": "pass"}
    assert json.loads(artifacts[second])["cases"] == {"first": "pass", "second": "pass"}


def test_mutation_does_not_change_or_run_existing_argument_negative_control(tmp_path):
    control = {"args": ["--control"], "violation_exit_code": 10, "failing_case": "first"}
    project_value, candidate = project(tmp_path, negative_control=control)
    info = {"runner": "host", "path": os.environ.get("PATH", ""), "executables": {}}
    baseline, _ = runner.run_behavior(project_value, candidate, None, info, "run-identifier")
    assert baseline[0]["status"] == "pass"
    assert baseline[0]["negative_control"]["qualified"] is True
    results, _ = execute(project_value, candidate, baseline, info)
    assert results[0]["outcome"] == "killed"
    assert "negative_control" not in results[0]["check_result"]
    assert project_value.contract.behavior[0].negative_control.model_dump() == control


def test_approved_base_files_are_not_valid_mutation_targets(tmp_path):
    project_value, _ = project(tmp_path)
    project_value = replace(project_value, contract=project_value.contract.model_copy(update={"base_files": ["source.py"]}))
    with pytest.raises(ValueError):
        approved_mutations(project_value.contract, project_value.files)


def test_host_setup_failure_never_qualifies_a_mutant(tmp_path):
    project_value, candidate = project(tmp_path)
    check = project_value.contract.behavior[0].model_copy(update={"argv": ["mentat-no-such-executable"]})
    project_value = replace(project_value, contract=project_value.contract.model_copy(update={"behavior": [check]}))
    baseline = [{"id": "behavior", "claim": "claim", "status": "pass", "command_status": "pass", "exit_code": 0, "case_results": {"first": "pass", "second": "pass"}}]
    results, _ = execute(project_value, candidate, baseline)
    assert results[0]["status"] == "incomplete" and results[0]["outcome"] == "error"
    assert results[0]["assertion_failed"] is not True


@pytest.mark.parametrize("manifest", [
    b'{"version":1,"version":1,"mutants":{}}',
    b'{"version":true,"mutants":{}}',
    b'{"version":2,"mutants":{}}',
    b'{"version":1,"mutants":{}}',
    b'{"version":1,"mutants":[],"extra":1}',
    b'not json',
])
def test_rejects_invalid_approved_mutation_manifests(tmp_path, manifest):
    project_value, _ = project(tmp_path)
    with pytest.raises(ValueError):
        approved_mutations(project_value.contract, {**project_value.files, "mutants.json": manifest})


@pytest.mark.parametrize("changes", [
    {"check": "missing"}, {"before": ""}, {"before": "same", "after": "same"},
    {"expected_case": ""}, {"unknown": "value"},
])
def test_rejects_invalid_mutation_entries(tmp_path, changes):
    project_value, _ = project(tmp_path, {"mutant": mutation(**changes)})
    with pytest.raises(ValueError):
        approved_mutations(project_value.contract, project_value.files)


@pytest.mark.parametrize("budget_offset", [-1, 0, 17])
def test_qualification_budget_prioritizes_required_report_over_optional_log(tmp_path, monkeypatch, budget_offset):
    project_value, candidate = project(tmp_path)
    monkeypatch.setattr(runner, "_MAX_RUN_OUTPUT", 2048)
    info = {"runner": "host", "path": os.environ.get("PATH", ""), "executables": {}}
    baseline, baseline_artifacts = runner.run_behavior(project_value, candidate, None, info, "run-identifier")
    assert baseline[0]["status"] == "pass"
    report_size = len(baseline_artifacts[baseline[0]["artifacts"]["cases.json"]])
    budget = report_size + budget_offset
    results, artifacts = run_source_mutations(
        project_value, candidate, None, info, "run-identifier", baseline, output_budget=budget
    )
    result = results[0]
    assert sum(map(len, artifacts.values())) <= budget
    if budget_offset < 0:
        assert (result["status"], result["outcome"]) == ("incomplete", "error")
        assert "cases.json" in result["check_result"]["artifact_omissions"]
    else:
        assert (result["status"], result["outcome"]) == ("pass", "killed")
        assert result["check_result"]["artifacts"]["cases.json"] in artifacts
        if budget_offset == 0:
            assert "stdout" in result["check_result"]["log_omissions"]
            assert "stdout" not in result["check_result"]
        else:
            assert artifacts[result["check_result"]["stdout"]] == b"synthetic output\n"


@pytest.mark.parametrize("budget", [-1, True, 1.5, 2049])
def test_rejects_invalid_qualification_output_budget(tmp_path, monkeypatch, budget):
    project_value, candidate = project(tmp_path)
    monkeypatch.setattr(runner, "_MAX_RUN_OUTPUT", 2048)
    info = {"runner": "host", "path": os.environ.get("PATH", ""), "executables": {}}
    baseline, _ = runner.run_behavior(project_value, candidate, None, info, "run-identifier")
    with pytest.raises(ValueError):
        run_source_mutations(project_value, candidate, None, info, "run-identifier", baseline, output_budget=budget)
