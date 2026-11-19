import copy
import hashlib
import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from mentat import cli, evidence
from mentat.project import Contract
from mentat.project_formal import run_formal

CHECK = '''import json, os, sys
from pathlib import Path
source = Path("source.py").read_text()
if "VALUE=BROKEN" in source:
    Path(os.environ["MENTAT_ARTIFACTS_DIR"], "cases.json").write_text("{")
    sys.exit(10)
failed = "VALUE=BAD" in source
cases = {"target <case>": "fail" if failed else "pass", "other": "pass"}
Path(os.environ["MENTAT_ARTIFACTS_DIR"], "cases.json").write_text(json.dumps({"schema_version": 1, "cases": cases}))
print("PRIVATE_LOG_CONTENT")
sys.exit(10 if failed else 0)
'''


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def campaign(tmp_path, monkeypatch, request):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.test")
    checks = repo / ".mentat"
    checks.mkdir()
    config = {
        "version": 1, "allow_host": True, "source_mutations": "mutants.json",
        "claims": [{"id": "claim", "statement": "Candidate behavior"}],
        "formal": [{"id": "model", "claim": "claim", "engine": "z3", "source": "model.smt2", "expected": "sat"}],
        "behavior": [{"id": "native", "claim": "claim", "argv": [sys.executable, "{checks}/check.py"],
                      "violation_exit_codes": [10], "artifacts": ["cases.json"],
                      "case_report": {"artifact": "cases.json", "required_cases": ["target <case>", "other"]}}],
    }
    mutants = {"version": 1, "mutants": {
        name: {"check": "native", "path": "source.py", "before": "VALUE=GOOD", "after": f"VALUE={after}", "expected_case": "target <case>"}
        for name, after in [("kill", "BAD"), ("survive", "STILL_GOOD"), ("error", "BROKEN")]
    }}
    if getattr(request, "param", None) == "kill-only":
        mutants["mutants"] = {"kill": mutants["mutants"]["kill"]}
    if getattr(request, "param", None) == "apply-error":
        mutants["mutants"] = {"missing": {**mutants["mutants"]["kill"], "before": "ABSENT_MARKER"}}
    (checks / "contract.json").write_text(json.dumps(config))
    (checks / "mutants.json").write_text(json.dumps(mutants))
    (checks / "model.smt2").write_text("(assert false)\n" if getattr(request, "param", None) == "gate" else "(assert true)\n")
    (checks / "check.py").write_text(CHECK)
    (repo / "source.py").write_text("VALUE=GOOD\nCOMMENT=one\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "approved base")
    base = git(repo, "rev-parse", "HEAD")
    state = tmp_path / "state"
    runner = CliRunner()
    prefix = ["--state", str(state)]
    scope = ["--repo", str(repo), "--base", base, "--runner", "host"]
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)

    def invoke(*args):
        return runner.invoke(cli.app, [*prefix, *args])

    checked = invoke("spec", "check", *scope, "--json")
    assert checked.exit_code == 0, checked.output
    digest = json.loads(checked.output)["contract_digest"]
    approved = invoke("spec", "approve", digest, *scope)
    assert approved.exit_code == 0, approved.output

    def verify(revision=base):
        result = invoke("verify", "--rev", revision, *scope, "--json")
        assert result.exit_code in (0, 1, 2), (result.output, result.exception)
        payload = json.loads(result.output)
        assert "run_id" in payload, payload
        return result, payload

    return repo, checks, state, base, scope, invoke, verify


@pytest.fixture
def qualified_run(campaign):
    _, _, state, _, _, _, verify = campaign
    result, payload = verify()
    assert result.exit_code == 2, payload
    _, record = evidence.read_run(state, payload["run_id"])
    return campaign, record


def test_cli_saves_real_mutants_bindings_and_acceptance_rejects_incomplete(qualified_run):
    (repo, checks, state, base, scope, invoke, _), record = qualified_run
    assert record["status"] == "incomplete"
    declarations = {item["id"]: item for item in record["source_mutation_checks"]}
    assert set(declarations) == {"kill", "survive", "error"}
    for declaration in declarations.values():
        assert declaration == {"id": declaration["id"], "claim": "claim", "check": "native",
                               "expected_case": "target <case>", "required_cases": ["target <case>", "other"],
                               "violation_exit_codes": [10]}
    assert record["source_mutation_manifest_sha256"] == hashlib.sha256((checks / "mutants.json").read_bytes()).hexdigest()
    results = {item["id"]: item for item in evidence.source_qualifications(record)}
    assert [(results[name]["status"], results[name]["outcome"]) for name in ("kill", "survive", "error")] == [
        ("pass", "killed"), ("violation", "survived"), ("incomplete", "error")]
    assert results["kill"]["assertion_failed"] is True
    assert results["kill"]["case_results"] == {"target <case>": "fail", "other": "pass"}
    assert results["survive"]["assertion_failed"] is False
    for item in results.values():
        binding = item["bindings"]
        assert binding["run_id"] == record["run_id"]
        assert binding["revision"] == record["revision"] == base
        assert binding["snapshot_digest"] == record["snapshot_digest"]
        assert binding["base"] == base and binding["contract_digest"] == record["contract_digest"]
        assert binding["verifier_digest"] == record["comparison_inputs"]["verifier"]
        assert binding["approved_manifest_sha256"] == record["source_mutation_manifest_sha256"]
        assert binding["runtime"] == {"runner": "host", "sha256": evidence.digest(evidence.json_bytes(record["runtime"]))}
    assert (repo / "source.py").read_text() == "VALUE=GOOD\nCOMMENT=one\n"
    assert invoke("accept", record["run_id"], *scope).exit_code != 0
    assert not (state / "acceptances" / f"{record['run_id']}.json").exists()


@pytest.mark.parametrize("damage", ["candidate", "snapshot", "base", "contract", "verifier", "manifest", "runtime", "claim", "check", "case", "outcome", "outcome-forged-error", "applied", "attribution", "baseline", "baseline-command", "artifact", "wrong-type"])
def test_saved_qualification_rejects_corrupted_bindings_and_outcomes(qualified_run, damage):
    _, original = qualified_run
    value = copy.deepcopy(original)
    result = value["source_qualifications"][0]
    binding = result["bindings"]
    if damage in {"candidate", "snapshot", "base", "contract", "verifier", "manifest"}:
        key = {"candidate": "revision", "snapshot": "snapshot_digest", "base": "base", "contract": "contract_digest", "verifier": "verifier_digest", "manifest": "approved_manifest_sha256"}[damage]
        binding[key] = "f" * len(binding[key])
    elif damage == "runtime":
        binding["runtime"]["sha256"] = "f" * 64
    elif damage in {"claim", "check", "expected_case"}:
        result[damage] = "wrong"
    elif damage == "case":
        result["expected_case"] = "other"
    elif damage == "outcome":
        result["outcome"] = "survived"
    elif damage == "outcome-forged-error":
        result.update(status="incomplete", outcome="error")
    elif damage == "applied":
        result["applied_count"] = 0
    elif damage == "attribution":
        result["assertion_failed"] = False
    elif damage == "baseline":
        value["cases"][0]["checks"][0]["case_results"]["target <case>"] = "fail"
    elif damage == "baseline-command":
        value["cases"][0]["checks"][0]["command_status"] = "incomplete"
    elif damage == "artifact":
        result["check_result"]["artifacts"]["cases.json"] = "outside.json"
    else:
        result["bindings"] = []
    with pytest.raises(ValueError):
        evidence.source_qualifications(value)


def test_formal_gate_displays_declared_qualification_as_skipped(qualified_run):
    _, original = qualified_run
    value = copy.deepcopy(original)
    value["behavior_skipped"] = True
    value["cases"] = []
    value["source_qualifications"] = []
    checks = evidence.source_qualifications(value)
    assert {item["id"] for item in checks} == {"kill", "survive", "error"}
    assert all(item["status"] == "skipped" for item in checks)


def test_legacy_evidence_has_no_invented_qualifications(qualified_run):
    _, original = qualified_run
    value = copy.deepcopy(original)
    for key in ("source_mutation_checks", "source_qualifications", "source_mutation_manifest_sha256"):
        value.pop(key)
    assert evidence.source_qualifications(value) == []


def test_lean_comparator_is_incomplete_without_campaign_and_never_dispatches_legacy(monkeypatch):
    from types import SimpleNamespace

    from mentat import project_formal

    manifest = {
        "version": 1, "allow_host": True,
        "claims": [{"id": "claim", "statement": "Claim"}],
        "formal": [{"id": "cmp", "claim": "claim", "engine": "lean-comparator", "source": "proof.lean", "theorem": "Proof.ok", "allowed_imports": ["Init"], "permitted_axioms": []}],
        "behavior": [{"id": "native", "claim": "claim", "argv": [sys.executable, "{checks}/check.py"]}],
    }
    check = Contract.model_validate(manifest)
    def forbidden(*args, **kwargs):
        pytest.fail("comparator dispatched to legacy Lean")
    monkeypatch.setattr(project_formal, "_run_lean", forbidden)
    result = run_formal(SimpleNamespace(contract=check, files={"proof.lean": b""}), {})
    assert result[0]["kind"] == "lean-comparator" and result[0]["status"] == "incomplete"


def test_reports_and_compare_preserve_source_qualification_attribution(qualified_run, tmp_path):
    (repo, _, state, base, scope, invoke, verify), first = qualified_run
    report = (state / "runs" / first["run_id"] / "report.md").read_text()
    for token in ("kill", "survive", "error", "target <case>"):
        assert token in report
    assert "violation" in report and "incomplete" in report
    output = tmp_path / "report.html"
    rendered = invoke("evidence", "render", first["run_id"], "--out", str(output))
    assert rendered.exit_code == 0, rendered.output
    html = output.read_text()
    assert "kill" in html and "survive" in html and "error" in html
    assert "target &lt;case&gt;" in html and "target <case>" not in html
    assert "violation" in html.lower() and "incomplete" in html.lower()
    assert "PRIVATE_LOG_CONTENT" not in html and str(repo) not in html
    assert "source.py" not in html
    (repo / "source.py").write_text("VALUE=GOOD\nCOMMENT=two\n")
    git(repo, "add", "source.py")
    git(repo, "commit", "-qm", "new candidate")
    second_revision = git(repo, "rev-parse", "HEAD")
    _, second = verify(second_revision)
    compared = invoke("evidence", "compare", first["run_id"], second["run_id"], "--json")
    assert compared.exit_code in (0, 2), compared.output
    diff = json.loads(compared.output)
    checks = diff["claims"][0]["checks"]
    mutations = {check["id"]: check for check in checks if check["kind"] == "source-mutation"}
    assert set(mutations) == {"kill", "survive", "error"}
    assert mutations["kill"]["before"]["status"] == "pass"
    assert mutations["survive"]["after"]["status"] == "violation"
    assert mutations["error"]["after"]["status"] == "incomplete"
    assert mutations["kill"]["after"]["case_results"]["target <case>"] == "fail"
    terminal = invoke("verify", "--rev", base, *scope)
    assert terminal.exit_code == 2, terminal.output
    for token in ("kill", "survive", "error"):
        assert token in terminal.output


@pytest.mark.parametrize("campaign", ["gate"], indirect=True)
def test_real_formal_gate_records_qualification_inventory_without_running(campaign):
    _, _, state, _, _, invoke, verify = campaign
    result, payload = verify()
    assert result.exit_code != 0
    _, record = evidence.read_run(state, payload["run_id"])
    assert record["behavior_skipped"] is True and record["source_qualifications"] == []
    assert {item["id"] for item in record["source_mutation_checks"]} == {"kill", "survive", "error"}
    assert all(item["status"] == "skipped" for item in evidence.source_qualifications(record))
    report = (state / "runs" / payload["run_id"] / "report.md").read_text().lower()
    assert "skipped" in report and all(name in report for name in ("kill", "survive", "error"))
    output = state.parent / "gate.html"
    rendered = invoke("evidence", "render", payload["run_id"], "--out", str(output))
    assert rendered.exit_code == 0, rendered.output
    assert "skipped" in output.read_text().lower()


@pytest.mark.parametrize("corruption", ["cases-none", "case-none", "checks-none", "check-none", "inventory-none", "result-none", "case-value-list", "check-id-list", "formal-gate-string"])
def test_malformed_qualification_context_raises_value_error(qualified_run, corruption):
    _, original = qualified_run
    value = copy.deepcopy(original)
    if corruption == "cases-none":
        value["cases"] = None
    elif corruption == "case-none":
        value["cases"] = [None]
    elif corruption == "checks-none":
        value["cases"][0]["checks"] = None
    elif corruption == "check-none":
        value["cases"][0]["checks"] = [None]
    elif corruption == "inventory-none":
        value["source_mutation_checks"] = [None]
    elif corruption == "result-none":
        value["source_qualifications"] = [None]
    elif corruption == "check-id-list":
        value["cases"][0]["checks"][0]["id"] = []
    elif corruption == "formal-gate-string":
        value["behavior_skipped"] = "true"
        value["source_qualifications"] = []
    else:
        value["source_qualifications"][0]["case_results"] = {"target <case>": []}
    with pytest.raises(ValueError):
        evidence.source_qualifications(value)


def test_tampered_qualification_artifact_blocks_html_output(qualified_run, tmp_path):
    (_, _, state, _, _, invoke, _), record = qualified_run
    artifact = record["source_qualifications"][0]["check_result"]["artifacts"]["cases.json"]
    (state / "runs" / record["run_id"] / artifact).write_text("tampered")
    output = tmp_path / "tampered.html"
    result = invoke("evidence", "render", record["run_id"], "--out", str(output))
    assert result.exit_code == 2
    assert not output.exists()


@pytest.mark.parametrize("campaign", ["kill-only"], indirect=True)
def test_passing_killed_mutant_can_be_accepted_with_real_approval(campaign, tmp_path):
    _, _, state, base, scope, invoke, verify = campaign
    result, payload = verify()
    assert result.exit_code == 0, payload
    assert payload["status"] == "pass"
    _, record = evidence.read_run(state, payload["run_id"])
    assert [(check["status"], check["outcome"]) for check in evidence.source_qualifications(record)] == [("pass", "killed")]
    accepted = invoke("accept", record["run_id"], *scope)
    assert accepted.exit_code == 0, accepted.output
    receipt = json.loads((state / "acceptances" / f"{record['run_id']}.json").read_text())
    assert receipt["run_id"] == record["run_id"] and receipt["revision"] == base
    output = tmp_path / "accepted.html"
    rendered = invoke("evidence", "render", record["run_id"], "--out", str(output))
    assert rendered.exit_code == 0, rendered.output
    assert "recorded at" in output.read_text().lower()


@pytest.mark.parametrize("campaign", ["apply-error"], indirect=True)
def test_unapplied_mutation_is_incomplete_even_with_passing_baseline(campaign):
    _, _, state, _, scope, invoke, verify = campaign
    result, payload = verify()
    assert result.exit_code == 2 and payload["status"] == "incomplete"
    _, record = evidence.read_run(state, payload["run_id"])
    assert record["cases"][0]["status"] == "pass"
    [qualification] = evidence.source_qualifications(record)
    assert qualification["id"] == "missing"
    assert qualification["status"] == "incomplete" and qualification["outcome"] == "error"
    assert qualification["applied_count"] == 0
    assert invoke("accept", record["run_id"], *scope).exit_code != 0


@pytest.mark.parametrize("extra_report_space", [0, 1])
@pytest.mark.parametrize("campaign", ["kill-only"], indirect=True)
def test_cli_shares_output_budget_between_baseline_and_mutation(campaign, monkeypatch, extra_report_space):
    from mentat import runner

    _, _, state, _, _, _, verify = campaign
    report = json.dumps({"schema_version": 1, "cases": {"target <case>": "pass", "other": "pass"}}).encode()
    baseline_log = b"PRIVATE_LOG_CONTENT\n"
    limit = len(report) + len(baseline_log) + extra_report_space * len(report)
    monkeypatch.setattr(runner, "_MAX_RUN_OUTPUT", limit)
    result, payload = verify()
    _, record = evidence.read_run(state, payload["run_id"])
    bundle = state / "runs" / payload["run_id"]
    retained = {name: (bundle / name).read_bytes() for name in record["artifacts"] if name != "report.md"}
    assert sum(map(len, retained.values())) <= limit
    baseline = record["cases"][0]["checks"][0]
    assert baseline["status"] == "pass" and baseline["artifacts"]["cases.json"] in retained
    [mutation] = evidence.source_qualifications(record)
    if extra_report_space:
        assert result.exit_code == 0 and (mutation["status"], mutation["outcome"]) == ("pass", "killed")
        assert mutation["check_result"]["artifacts"]["cases.json"] in retained
        assert "stdout" in mutation["check_result"]["log_omissions"]
        assert "stdout" not in mutation["check_result"]
    else:
        assert result.exit_code == 2 and (mutation["status"], mutation["outcome"]) == ("incomplete", "error")
        assert "cases.json" in mutation["check_result"]["artifact_omissions"]
