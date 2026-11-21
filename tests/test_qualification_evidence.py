import copy
import hashlib
import json
import sys

import pytest
from helpers import git
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
    if getattr(request, "param", None) == "survive-only":
        mutants["mutants"] = {"survive": mutants["mutants"]["survive"]}
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
    assert result.exit_code == 0, payload
    _, record = evidence.read_run(state, payload["run_id"])
    assert (record["status"], record["source_qualification_status"]) == ("pass", "incomplete")
    return campaign, record


def test_cli_saves_real_mutants_bindings_and_acceptance_rejects_incomplete(qualified_run):
    (repo, checks, state, base, scope, invoke, _), record = qualified_run
    assert (record["status"], record["source_qualification_status"]) == ("pass", "incomplete")
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
    assert terminal.exit_code == 0, terminal.output
    assert "Product checks  pass" in terminal.output
    assert "Source qualification  incomplete" in terminal.output
    for token in ("kill", "survive", "error"):
        assert token in terminal.output


@pytest.mark.parametrize("campaign", ["gate"], indirect=True)
def test_real_formal_gate_records_qualification_inventory_without_running(campaign):
    _, _, state, _, _, invoke, verify = campaign
    result, payload = verify()
    assert result.exit_code != 0
    _, record = evidence.read_run(state, payload["run_id"])
    assert record["behavior_skipped"] is True and record["source_qualifications"] == []
    assert record["source_qualification_status"] == "skipped"
    assert {item["id"] for item in record["source_mutation_checks"]} == {"kill", "survive", "error"}
    assert all(item["status"] == "skipped" for item in evidence.source_qualifications(record))
    report = (state / "runs" / payload["run_id"] / "report.md").read_text().lower()
    assert "skipped" in report and all(name in report for name in ("kill", "survive", "error"))
    text = (state / "runs" / payload["run_id"] / "report.md").read_text()
    assert "Product checks failed; source qualification did not run. This run cannot be accepted." in text
    assert "- Source qualification · skipped" in text
    assert "source qualification failed" not in text.lower()
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
    assert record["source_qualification_status"] == "pass"
    assert [(check["status"], check["outcome"]) for check in evidence.source_qualifications(record)] == [("pass", "killed")]
    accepted = invoke("accept", record["run_id"], *scope)
    assert accepted.exit_code == 0, accepted.output
    receipt = json.loads((state / "acceptances" / f"{record['run_id']}.json").read_text())
    assert receipt["run_id"] == record["run_id"] and receipt["revision"] == base
    output = tmp_path / "accepted.html"
    rendered = invoke("evidence", "render", record["run_id"], "--out", str(output))
    assert rendered.exit_code == 0, rendered.output
    assert "recorded at" in output.read_text().lower()


@pytest.mark.parametrize("campaign", ["survive-only"], indirect=True)
def test_surviving_mutant_is_a_qualification_violation_not_a_product_defect(campaign, tmp_path):
    _, _, state, base, scope, invoke, verify = campaign
    result, payload = verify()
    assert result.exit_code == 0, payload
    assert payload["status"] == "pass"
    _, record = evidence.read_run(state, payload["run_id"])
    assert record["cases"][0]["status"] == "pass"
    assert record["source_qualification_status"] == "violation"
    assert invoke("accept", record["run_id"], *scope).exit_code == 2
    assert not (state / "acceptances" / f"{record['run_id']}.json").exists()
    verdict = "Product checks passed; source qualification failed. This run cannot be accepted."
    report = (state / "runs" / payload["run_id"] / "report.md").read_text()
    assert verdict in report and "- Source qualification · violation" in report
    output = tmp_path / "survived.html"
    assert invoke("evidence", "render", payload["run_id"], "--out", str(output)).exit_code == 0
    assert verdict in output.read_text()
    terminal = invoke("verify", "--rev", base, *scope)
    assert terminal.exit_code == 0 and verdict in terminal.output, terminal.output


@pytest.mark.parametrize("campaign", ["kill-only"], indirect=True)
def test_real_product_violation_with_qualifications_exits_violation(campaign):
    repo, _, state, _, _, _, verify = campaign
    (repo / "source.py").write_text("VALUE=BAD\nCOMMENT=one\n")
    git(repo, "add", "source.py")
    git(repo, "commit", "-qm", "violating candidate")
    result, payload = verify(git(repo, "rev-parse", "HEAD"))
    assert result.exit_code == 1, payload
    assert payload["status"] == "violation"
    _, record = evidence.read_run(state, payload["run_id"])
    assert record["status"] == "violation"
    assert record["cases"][0]["status"] == "violation"
    assert record["source_qualification_status"] == "incomplete"


def _save_manifest(state, record):
    raw = evidence.json_bytes(record)
    (state / "runs" / record["run_id"] / "manifest.json").write_bytes(raw)
    (state / "receipts" / record["run_id"]).write_text(evidence.digest(raw))


@pytest.mark.parametrize(("damage", "message"), [
    ("product-verdict", "Saved verdict contradicts product checks"),
    ("qualification-verdict", "Saved qualification verdict contradicts qualification evidence"),
    ("qualification-type", "Saved qualification verdict contradicts qualification evidence"),
    ("qualification-evidence", "lacks its qualification evidence"),
])
def test_saved_split_verdict_must_match_its_evidence(qualified_run, damage, message):
    (_, _, state, _, _, _, _), original = qualified_run
    record = copy.deepcopy(original)
    if damage == "product-verdict":
        record["status"] = "violation"
    elif damage == "qualification-verdict":
        record["source_qualification_status"] = "pass"
    elif damage == "qualification-type":
        record["source_qualification_status"] = 1
    else:
        record.pop("source_mutation_checks")
        record.pop("source_qualifications")
    _save_manifest(state, record)
    with pytest.raises(ValueError, match=message):
        evidence.read_run_snapshot(state, record["run_id"])


@pytest.mark.parametrize(("record", "expected"), [
    ({"status": "pass"}, True),
    ({"status": "pass", "source_qualification_status": "pass"}, True),
    ({"status": "pass", "source_qualification_status": "skipped"}, False),
    ({"status": "pass", "source_qualification_status": "pass "}, False),
    ({"status": "pass", "source_qualification_status": 1}, False),
    ({"status": "pass", "source_qualification_status": None}, False),
    ({"status": "violation", "source_qualification_status": "pass"}, False),
])
def test_acceptable_requires_product_pass_and_a_passing_recorded_qualification(record, expected):
    assert evidence.acceptable(record) is expected


def test_legacy_combined_verdict_still_reads_without_the_split_key(qualified_run):
    (_, _, state, _, _, _, _), original = qualified_run
    record = copy.deepcopy(original)
    record.pop("source_qualification_status")
    record["status"] = "incomplete"
    _save_manifest(state, record)
    assert evidence.read_run_snapshot(state, record["run_id"])[1]["status"] == "incomplete"
    record["status"] = "pass"
    _save_manifest(state, record)
    with pytest.raises(ValueError, match="contradicts qualification evidence"):
        evidence.read_run_snapshot(state, record["run_id"])


@pytest.mark.parametrize("campaign", ["survive-only"], indirect=True)
def test_surviving_mutant_is_visible_on_every_surface(campaign, tmp_path):
    _, _, state, base, scope, invoke, verify = campaign
    _, payload = verify()
    run_id, revision = payload["run_id"], payload["revision"]
    refused = "Product checks passed; source qualification failed. This run cannot be accepted."
    exported = invoke("evidence", "export", run_id, "--json")
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.output)["source_qualification_status"] == "violation"
    text = invoke("evidence", "export", run_id)
    assert "Source qualification  violation" in text.output, text.output
    compared = invoke("evidence", "compare", run_id, run_id)
    assert compared.exit_code == 0, compared.output
    assert "  Source qualification  violation" in compared.output
    summary = invoke("evidence", "pr-summary", run_id, "--head", revision,
                     "--report-url", "https://example.test/report")
    assert summary.exit_code == 0, summary.output
    assert "- Source qualification: **violation**." in summary.output
    output = tmp_path / "surfaces.html"
    assert invoke("evidence", "render", run_id, "--out", str(output)).exit_code == 0
    html = output.read_text()
    assert "<dt>Source qualification</dt><dd class=\"\">violation</dd>" in html
    assert "<dt>Local operator acceptance</dt><dd class=\"\">Not recorded</dd>" in html
    assert refused in html


@pytest.mark.parametrize("campaign", ["kill-only"], indirect=True)
def test_accept_refuses_a_contract_with_source_mutations_stripped_of_qualification_evidence(campaign):
    _, _, state, _, scope, invoke, verify = campaign
    _, payload = verify()
    _, record = evidence.read_run(state, payload["run_id"])
    for key in ("source_mutation_checks", "source_qualifications",
                "source_mutation_manifest_sha256", "source_qualification_status"):
        record.pop(key)
    _save_manifest(state, record)
    accepted = invoke("accept", record["run_id"], *scope)
    assert accepted.exit_code == 2, accepted.output
    assert "Only a complete passing run under the current approval can be accepted" in accepted.output
    assert not (state / "acceptances" / f"{record['run_id']}.json").exists()


@pytest.mark.parametrize("campaign", ["kill-only"], indirect=True)
def test_legacy_pre_split_pass_with_qualifications_stays_acceptable(campaign):
    _, _, state, _, scope, invoke, verify = campaign
    _, payload = verify()
    _, record = evidence.read_run(state, payload["run_id"])
    record.pop("source_qualification_status")
    _save_manifest(state, record)
    accepted = invoke("accept", record["run_id"], *scope)
    assert accepted.exit_code == 0, accepted.output


@pytest.mark.parametrize("campaign", ["gate"], indirect=True)
def test_legacy_refusal_keeps_the_existing_message(campaign):
    _, _, state, _, scope, invoke, verify = campaign
    _, payload = verify()
    _, record = evidence.read_run(state, payload["run_id"])
    record.pop("source_qualification_status")
    _save_manifest(state, record)
    refused = invoke("accept", record["run_id"], *scope)
    assert refused.exit_code == 2
    assert "detail  Only a complete passing run under the current approval can be accepted\n" in refused.output
    assert "source qualification" not in refused.output


@pytest.mark.parametrize("campaign", ["kill-only"], indirect=True)
def test_acceptance_rechecks_the_run_read_after_confirmation(campaign, monkeypatch):
    _, _, state, _, scope, invoke, verify = campaign
    _, payload = verify()
    original = evidence.read_run
    reads = []

    def qualification_fails_after_confirmation(state_path, run_id):
        bundle, record = original(state_path, run_id)
        reads.append(run_id)
        if len(reads) > 1:
            record = {**record, "source_qualification_status": "violation"}
        return bundle, record

    monkeypatch.setattr(evidence, "read_run", qualification_fails_after_confirmation)
    refused = invoke("accept", payload["run_id"], *scope)
    assert len(reads) == 2
    assert refused.exit_code == 2 and "any source qualification must also pass" in refused.output
    assert not (state / "acceptances" / f"{payload['run_id']}.json").exists()


def test_split_report_leads_with_product_counterexample_across_claims(monkeypatch):
    from mentat import evidence_report

    def case(check_id, claim, status, outcome):
        check = {"id": check_id, "claim": claim, "status": status, "detail": f"{check_id} {status}",
                 "case_results": {"target": outcome}, "required_cases": ["target"],
                 "command_status": status, "exit_code": 0 if status == "pass" else 10}
        return {"case_id": check_id, "status": status, "checks": [check]}

    mutant = {"id": "mutant", "claim": "first", "check": "first-check", "expected_case": "target",
              "outcome": "survived", "status": "violation", "detail": "Mutation survived",
              "required_cases": ["target"], "case_results": {"target": "pass"}}
    record = {
        "kind": "project-verification", "base": "a" * 40, "runner": "host", "run_id": "b" * 32,
        "status": "violation", "source_qualification_status": "violation", "behavior_skipped": False,
        "claims": [{"id": "first", "statement": "First"}, {"id": "second", "statement": "Second"}],
        "model_checks": [],
        "cases": [case("first-check", "first", "pass", "pass"),
                  case("second-check", "second", "violation", "fail")],
    }
    monkeypatch.setattr(evidence_report, "source_qualifications", lambda _: [mutant])
    text = "\n".join(evidence_report.project_report(record))
    assert text.index("## Counterexample\n\nClaim `second`") < text.index("## Qualification failure")
    assert "Qualification failure" in text and "Claim `first`" in text
    assert "Product checks failed; source qualification failed. This run cannot be accepted." in text
    assert text.count("- Source qualification ·") == 1
    assert "- Source qualification · violation" in text


@pytest.mark.parametrize("campaign", ["apply-error"], indirect=True)
def test_unapplied_mutation_is_incomplete_qualification_with_passing_product(campaign):
    _, _, state, _, scope, invoke, verify = campaign
    result, payload = verify()
    assert result.exit_code == 0 and payload["status"] == "pass"
    _, record = evidence.read_run(state, payload["run_id"])
    assert record["cases"][0]["status"] == "pass"
    assert record["source_qualification_status"] == "incomplete"
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
        assert result.exit_code == 0 and (mutation["status"], mutation["outcome"]) == ("incomplete", "error")
        assert "cases.json" in mutation["check_result"]["artifact_omissions"]
