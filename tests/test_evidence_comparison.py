import json
from pathlib import Path

import pytest
from test_qualification_evidence import campaign as qualification_campaign  # noqa: F401
from typer.testing import CliRunner

from mentat import evidence
from mentat.cli import app


@pytest.fixture
def compare(tmp_path):
    state = evidence.state_directory(tmp_path / "state")

    def run(before, after, *args):
        return CliRunner().invoke(
            app, ["--state", str(state), "evidence", "compare", before, after, *args]
        )

    return state, run


def save(state, run_id, outcome="pass", *, formal="pass", **overrides):
    model = {"id": "model", "claim": "claim", "kind": "z3", "status": formal, "detail": formal}
    behavior = {
        "id": "behavior",
        "claim": "claim",
        "status": outcome,
        "detail": outcome,
        "artifacts": {"witness.json": "example.witness.json"},
    }
    skipped = formal != "pass"
    record = {
        "schema_version": 2,
        "kind": "project-verification",
        "run_id": run_id,
        "revision": run_id + "0" * 8,
        "repo": "/repo",
        "base": "b" * 40,
        "runner": "host",
        "contract_digest": "c" * 64,
        "status": outcome,
        "claims": [{"id": "claim", "statement": "Example behavior"}],
        "model_checks": [model],
        "behavior_checks": [{"id": "behavior", "claim": "claim"}],
        "cases": []
        if skipped
        else [{"case_id": "behavior", "status": outcome, "checks": [behavior]}],
        "behavior_skipped": skipped,
        "runtime": {"runner": "host"},
        "tools": {"z3": {"version": "example"}},
        "reproduce": "mentat verify",
        **overrides,
    }
    return evidence.save_run(state, record, {} if skipped else {"example.witness.json": b"{}"})


def test_compare_preserves_run_order_and_references_verified_files(compare):
    state, run = compare
    before = "a" * 32
    after = "b" * 32
    failed = save(state, before, "violation")
    save(state, after, tools={"z3": {"version": "another"}})
    result = run(before, after, "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["environment_changed"] is True
    assert report["before"]["status"] == "violation"
    assert report["after"]["status"] == "pass"
    assert Path(report["before"]["report"]) == failed / "report.md"
    assert [check["id"] for check in report["claims"][0]["checks"]] == ["model", "behavior"]
    behavior = report["claims"][0]["checks"][1]
    assert behavior["before"]["artifacts"] == {"witness.json": "example.witness.json"}
    assert (behavior["before"]["status"], behavior["after"]["status"]) == ("violation", "pass")

    human = run(before, after)
    assert human.exit_code == 0, human.output
    assert "Claim claim  Example behavior" in human.output
    assert "behavior behavior  violation -> pass" in human.output
    assert str(failed / "example.witness.json") in human.output
    assert "runner metadata differ" in human.output


@pytest.mark.parametrize(
    ("field", "value"),
    [("repo", "/other"), ("base", "a" * 40), ("runner", "docker"), ("contract_digest", "d" * 64)],
)
def test_compare_rejects_different_scope(compare, field, value):
    state, run = compare
    save(state, "a" * 32)
    save(state, "b" * 32, **{field: value})
    result = run("a" * 32, "b" * 32, "--json")
    assert result.exit_code == 2
    assert f"differs in {field}" in json.loads(result.output)["detail"]


@pytest.mark.parametrize(("outcome", "code"), [("violation", 0), ("incomplete", 2)])
def test_compare_reports_behavior_skipped_by_formal_gate(compare, outcome, code):
    state, run = compare
    save(state, "a" * 32, outcome, formal=outcome)
    save(state, "b" * 32)
    result = run("a" * 32, "b" * 32, "--json")
    assert result.exit_code == code, result.output
    report = json.loads(result.output)
    assert report["before"]["behavior_skipped"] is True
    model, behavior = report["claims"][0]["checks"]
    assert model["before"]["status"] == outcome
    assert behavior["before"]["status"] == "skipped"
    assert behavior["after"]["status"] == "pass"


def test_compare_rejects_other_run_kinds_and_tampered_artifacts(compare):
    state, run = compare
    saved = save(state, "a" * 32)
    save(state, "b" * 32, kind="other")
    result = run("a" * 32, "b" * 32, "--json")
    assert result.exit_code == 2
    assert "requires two project verification runs" in json.loads(result.output)["detail"]

    save(state, "c" * 32)
    (saved / "example.witness.json").write_text("tampered")
    result = run("a" * 32, "c" * 32, "--json")
    assert result.exit_code == 2
    assert "failed its integrity check" in json.loads(result.output)["detail"]


def test_compare_preserves_skipped_checks_when_neither_run_executed_them(compare):
    state, run = compare
    save(state, "a" * 32, "violation", formal="violation")
    save(state, "b" * 32, "violation", formal="violation")
    result = run("a" * 32, "b" * 32, "--json")
    assert result.exit_code == 0, result.output
    behavior = json.loads(result.output)["claims"][0]["checks"][1]
    assert behavior["before"]["status"] == behavior["after"]["status"] == "skipped"


def test_compare_does_not_create_missing_state(tmp_path):
    state = tmp_path / "missing" / "state"
    result = CliRunner().invoke(
        app, ["--state", str(state), "evidence", "compare", "a" * 32, "b" * 32]
    )
    assert result.exit_code == 2
    assert not state.parent.exists()


@pytest.mark.saves_invalid_run
def test_across_contracts_preserves_original_verdicts_and_side_specific_checks(compare):
    state, run = compare
    before_id, after_id = "a" * 32, "b" * 32
    shared = {"id": "claim", "statement": "Example behavior"}
    saved = save(
        state,
        before_id,
        "violation",
        claims=[shared, {"id": "removed", "statement": "Previous claim"}],
        comparison_inputs={
            "claims": {"claim": "one", "removed": "two"},
            "supporting_files": "old",
            "runner_settings": "same",
            "verifier": "same",
        },
    )
    save(
        state,
        after_id,
        "incomplete",
        base="c" * 40,
        contract_digest="d" * 64,
        claims=[shared, {"id": "added", "statement": "Current claim"}],
        comparison_inputs={
            "claims": {"claim": "changed", "added": "two"},
            "supporting_files": "new",
            "runner_settings": "same",
            "verifier": "same",
        },
        cases=[
            {
                "checks": [
                    {
                        "id": "behavior",
                        "claim": "added",
                        "status": "incomplete",
                        "detail": "Timeout",
                    }
                ]
            }
        ],
        tools={"z3": {"version": "other"}},
    )
    state_paths = sorted(state.rglob("*"))
    result = run(before_id, after_id, "--across-contracts", "--json")
    assert result.exit_code == 2, result.output
    report = json.loads(result.output)
    assert report["mode"] == "across-contracts"
    assert report["environment_changed"] is True
    assert report["before"]["status"] == "violation"
    assert report["after"]["status"] == "incomplete"
    assert report["before"]["base"] != report["after"]["base"]
    assert report["before"]["contract_digest"] != report["after"]["contract_digest"]
    assert report["input_changes"] == {
        "supporting_files": "changed",
        "runner_settings": "unchanged",
        "verifier": "unchanged",
    }
    rows = {row["id"]: row for row in report["claims"]}
    assert {key: row["definition"] for key, row in rows.items()} == {
        "added": "added",
        "claim": "changed",
        "removed": "removed",
    }
    assert rows["added"]["before"] is None and rows["removed"]["after"] is None
    assert [item["id"] for item in rows["claim"]["after"]["checks"]] == ["model"]
    assert rows["added"]["after"]["checks"][0]["status"] == "incomplete"
    assert sorted(state.rglob("*")) == state_paths

    strict = run(before_id, after_id, "--json")
    assert strict.exit_code == 2 and "requires matching scope" in strict.output

    human = run(before_id, after_id, "--across-contracts")
    assert human.exit_code == 2
    assert "Definition changed" in human.output and "after  Current claim" in human.output

    (saved / "example.witness.json").write_text("tampered")
    damaged = run(before_id, after_id, "--across-contracts", "--json")
    assert damaged.exit_code == 2 and "integrity check" in damaged.output


def test_across_contracts_labels_legacy_definitions_unavailable(compare):
    state, run = compare
    save(state, "a" * 32)
    save(state, "b" * 32, contract_digest="d" * 64, comparison_inputs={"claims": {"claim": "new"}})
    result = run("a" * 32, "b" * 32, "--across-contracts", "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["claims"][0]["definition"] == "unavailable"
    assert report["input_changes"]["verifier"] == "unavailable"


@pytest.mark.parametrize(("field", "value"), [("repo", "/other"), ("runner", "docker")])
def test_across_contracts_requires_same_recorded_repo_and_runner(compare, field, value):
    state, run = compare
    save(state, "a" * 32)
    save(state, "b" * 32, **{field: value})
    result = run("a" * 32, "b" * 32, "--across-contracts", "--json")
    assert result.exit_code == 2
    assert f"differs in {field}" in json.loads(result.output)["detail"]


def test_same_contract_comparison_escapes_controls_in_terminal_but_not_json(compare):
    state, _ = compare
    original = "before\x1b[31mred\nnext\rreturn"
    claims = [{"id": "claim", "statement": original}]
    save(state, "a" * 32, claims=claims)
    save(state, "b" * 32, claims=claims)
    args = ["--state", str(state), "evidence", "compare", "a" * 32, "b" * 32]
    human = CliRunner().invoke(app, args, color=True)
    assert human.exit_code == 0, human.output
    assert r"before\x1b[31mred\nnext\rreturn" in human.output
    assert original not in human.output
    machine = CliRunner().invoke(app, [*args, "--json"], color=True)
    assert machine.exit_code == 0, machine.output
    assert json.loads(machine.output)["claims"][0]["statement"] == original


def _state_snapshot(state):
    return {str(item.relative_to(state)): ("symlink", str(item.readlink())) if item.is_symlink() else
            ("file", item.read_bytes()) if item.is_file() else ("directory",)
            for item in state.rglob("*")}


def _receipt(state, run_id):
    _, record = evidence.read_run(state, run_id)
    path = state / "acceptances" / f"{run_id}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(evidence.json_bytes({
        "run_id": run_id, "revision": record["revision"],
        "contract_digest": record["contract_digest"],
        "manifest_digest": (state / "receipts" / run_id).read_text(),
        "accepted_at": "2026-01-02T03:04:05Z",
    }))


def test_html_comparison_accepts_violation_and_incomplete_and_keeps_original_state(compare, tmp_path):
    state, run = compare
    before, after = "a" * 32, "b" * 32
    save(state, before, "violation", tools={"z3": {"version": "old"}})
    save(state, after, "incomplete", tools={"z3": {"version": "new"}})
    before_state = _state_snapshot(state)
    output = tmp_path / "comparison.html"
    result = run(before, after, "--out", str(output))
    assert result.exit_code == 0, (result.output, result.exception)
    assert f"Report written to {output}" in result.output
    html = output.read_text()
    assert "violation" in html.lower() and "incomplete" in html.lower()
    assert before in html and after in html
    assert "changed" in html.lower()
    assert _state_snapshot(state) == before_state


def test_html_comparison_shows_saved_acceptance_and_formal_gate(compare, tmp_path):
    state, run = compare
    before, after = "a" * 32, "b" * 32
    save(state, before, "pass")
    save(state, after, "violation", formal="violation")
    _receipt(state, before)
    before_state = _state_snapshot(state)
    output = tmp_path / "comparison.html"
    result = run(before, after, "--out", str(output))
    assert result.exit_code == 0, (result.output, result.exception)
    html = output.read_text().lower()
    assert "recorded at 2026-01-02" in html and "not recorded" in html
    assert "violation" in html and "skipped" in html and "behavior" in html
    assert _state_snapshot(state) == before_state


@pytest.mark.parametrize("damage", ["artifact", "manifest", "extra-file"])
def test_html_comparison_rejects_corrupted_evidence_without_output(compare, tmp_path, damage):
    state, run = compare
    before, after = "a" * 32, "b" * 32
    bundle = save(state, before)
    save(state, after)
    path = {"artifact": bundle / "example.witness.json", "manifest": bundle / "manifest.json", "extra-file": bundle / "extra"}[damage]
    path.write_text("tampered")
    before_state = _state_snapshot(state)
    output = tmp_path / "comparison.html"
    result = run(before, after, "--out", str(output))
    assert result.exit_code == 2 and not output.exists()
    assert _state_snapshot(state) == before_state


@pytest.mark.parametrize("destination", ["existing", "symlink", "inside-state", "linked-parent", "missing-parent"])
def test_html_comparison_rejects_unsafe_destinations_without_writes(compare, tmp_path, destination):
    state, run = compare
    before, after = "a" * 32, "b" * 32
    save(state, before)
    save(state, after)
    output = tmp_path / "comparison.html"
    if destination == "existing":
        output.write_text("keep")
    elif destination == "symlink":
        target = tmp_path / "target"
        target.write_text("keep")
        output.symlink_to(target)
    elif destination == "inside-state":
        output = state / "comparison.html"
    elif destination == "linked-parent":
        link = tmp_path / "state-link"
        link.symlink_to(state, target_is_directory=True)
        output = link / "comparison.html"
    else:
        output = tmp_path / "missing" / "comparison.html"
    before_state = _state_snapshot(state)
    result = run(before, after, "--out", str(output))
    assert result.exit_code == 2
    if destination == "existing":
        assert output.read_text() == "keep"
    elif destination == "symlink":
        assert output.is_symlink() and target.read_text() == "keep"
    else:
        assert not output.exists()
    assert _state_snapshot(state) == before_state


@pytest.mark.parametrize("outcome", ["pass", "violation", "incomplete"])
def test_pr_summary_separates_ci_verification_and_operator_decision(compare, outcome):
    state, _ = compare
    run_id = "a" * 32
    save(state, run_id, outcome)
    result = CliRunner().invoke(app, ["--state", str(state), "evidence", "pr-summary", run_id,
                                    "--head", run_id + "0" * 8, "--report-url", "https://example.test/report",
                                    "--ci-status", "pass"])
    assert result.exit_code == 0, (result.output, result.exception)
    text = result.output.lower()
    assert "lightweight" in text and "host reported" in text and "pass" in text
    assert "full saved verification" in text and outcome in text
    assert "acceptance" in text and "not recorded" in text
    assert "https://example.test/report" in result.output


@pytest.mark.parametrize("url", ["http://example.test/report", "https://user:secret@example.test/report", "https://example.test/a b", "https://example.test/a\npath", "https://example.test/\x01"])
def test_pr_summary_rejects_unsafe_urls(compare, url):
    state, _ = compare
    run_id = "a" * 32
    save(state, run_id)
    result = CliRunner().invoke(app, ["--state", str(state), "evidence", "pr-summary", run_id,
                                    "--head", run_id + "0" * 8, "--report-url", url])
    assert result.exit_code == 2


def test_pr_summary_requires_exact_head_and_escapes_markdown_link(compare):
    state, _ = compare
    run_id = "a" * 32
    save(state, run_id)
    def invoke(head):
        return CliRunner().invoke(app, ["--state", str(state), "evidence", "pr-summary", run_id,
                                        "--head", head, "--report-url", "https://example.test/a(b)c"])
    assert invoke("f" * 40).exit_code == 2
    result = invoke(run_id + "0" * 8)
    assert result.exit_code == 0, result.output
    assert "https://example.test/a%28b%29c" in result.output


def _two_claim_record(status):
    claims = [{"id": "stable", "statement": "Stable requirement"},
              {"id": "changed", "statement": '<img src="https://evil.invalid" onerror="alert(1)">'}]
    model = [{"id": "stable-model", "claim": "stable", "kind": "z3", "status": "pass", "detail": "ok"},
             {"id": "changed-model", "claim": "changed", "kind": "z3", "status": "pass", "detail": "ok"}]
    checks = [
        {"id": "stable-check", "claim": "stable", "status": "pass", "detail": "ok"},
        {"id": "altered-check", "claim": "changed", "status": status,
         "detail": "PRIVATE_DIAGNOSTIC /private/hidden", "stdout": "private.log"},
    ]
    return {"claims": claims, "model_checks": model,
            "claim_review": {"titles": {"stable": "Stable title", "changed": "Changed title"},
                             "groups": [{"title": "All", "claim_ids": ["stable", "changed"]}]},
            "comparison_inputs": {"claims": {"stable": "1", "changed": "2"}},
            "behavior_checks": [{"id": check["id"], "claim": check["claim"]} for check in checks],
            "cases": [{"case_id": check["id"], "status": check["status"], "checks": [check]} for check in checks],
            "runtime": {"secret": "PRIVATE_RUNTIME"}, "host_reproduce": "PRIVATE_REPRODUCE"}


def _save_two_claims(state, run_id, status):
    record = _two_claim_record(status)
    record.update(schema_version=2, kind="project-verification", run_id=run_id,
                  revision=run_id + "0" * 8, repo="/private/secret-repo", base="b" * 40,
                  runner="host", contract_digest="c" * 64, status=status,
                  behavior_skipped=False, reproduce="PRIVATE_COMMAND", tools={})
    return evidence.save_run(state, record, {"private.log": b"PRIVATE_RAW_LOG"})


def test_html_comparison_puts_changed_claims_first_and_treats_untrusted_text_as_text(compare, tmp_path):
    from html.parser import HTMLParser

    class Inspector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []
            self.summaries = []
            self.summary = None
        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))
            if tag == "summary":
                self.summary = []
        def handle_endtag(self, tag):
            if tag == "summary" and self.summary is not None:
                self.summaries.append(" ".join(self.summary))
                self.summary = None
        def handle_data(self, data):
            if self.summary is not None:
                self.summary.append(data)

    state, run = compare
    before, after = "a" * 32, "b" * 32
    _save_two_claims(state, before, "pass")
    _save_two_claims(state, after, "violation")
    output = tmp_path / "comparison.html"
    result = run(before, after, "--out", str(output))
    assert result.exit_code == 0, result.output
    html = output.read_text()
    doc = Inspector()
    doc.feed(html)
    assert "Changed title" in doc.summaries[0] and "Stable title" in doc.summaries[1]
    assert not any(tag in {"img", "svg", "script", "iframe", "object"} for tag, _ in doc.tags)
    for _, attrs in doc.tags:
        for key in ("src", "href", "action"):
            assert key not in attrs or attrs[key].startswith("#")
    for secret in ("PRIVATE_DIAGNOSTIC", "PRIVATE_RUNTIME", "PRIVATE_REPRODUCE", "PRIVATE_COMMAND", "PRIVATE_RAW_LOG", "/private/secret-repo", "/private/hidden"):
        assert secret not in html
    assert "&lt;img" in html


def test_html_cross_contract_comparison_is_explicit_and_legacy_is_unavailable(compare, tmp_path):
    state, run = compare
    before, after = "a" * 32, "b" * 32
    save(state, before)
    save(state, after, base="d" * 40, contract_digest="e" * 64,
         comparison_inputs={"claims": {"claim": "different"}})
    output = tmp_path / "cross-contract.html"
    rejected = run(before, after, "--out", str(output))
    assert rejected.exit_code == 2 and not output.exists()
    accepted = run(before, after, "--out", str(output), "--across-contracts")
    assert accepted.exit_code == 0, accepted.output
    html = output.read_text().lower()
    assert "cross-contract" in html and "unavailable" in html
    assert "b" * 40 in html and "d" * 40 in html


def test_pr_summary_displays_actual_matching_recorded_acceptance(compare):
    state, _ = compare
    run_id = "a" * 32
    save(state, run_id)
    _receipt(state, run_id)
    result = CliRunner().invoke(app, ["--state", str(state), "evidence", "pr-summary", run_id,
                                    "--head", run_id + "0" * 8, "--report-url", "https://example.test/report"])
    assert result.exit_code == 0, result.output
    assert "acceptance" in result.output.lower()
    assert "recorded at 2026-01-02" in result.output.lower()
    assert "unavailable" in result.output.lower()


def test_html_comparison_puts_changed_checks_before_unchanged_checks(compare, tmp_path):
    state, run = compare
    before, after = "a" * 32, "b" * 32
    inventory = [{"id": "stable-check", "claim": "claim"}, {"id": "altered-check", "claim": "claim"}]
    def cases(changed_status):
        return [{"case_id": name, "status": status, "checks": [{"id": name, "claim": "claim", "status": status, "detail": "ok"}]}
                for name, status in [("stable-check", "pass"), ("altered-check", changed_status)]]
    save(state, before, behavior_checks=inventory, cases=cases("pass"), comparison_inputs={"claims": {"claim": "same"}})
    save(state, after, "violation", behavior_checks=inventory, cases=cases("violation"), comparison_inputs={"claims": {"claim": "same"}})
    output = tmp_path / "comparison.html"
    result = run(before, after, "--out", str(output))
    assert result.exit_code == 0, result.output
    html = output.read_text()
    assert html.index("altered-check") < html.index("stable-check")


def test_html_comparison_json_preserves_verdicts_without_changing_legacy_exit(compare, tmp_path):
    state, run = compare
    before, after = "a" * 32, "b" * 32
    save(state, before, "violation")
    save(state, after, "incomplete")
    legacy = run(before, after, "--json")
    assert legacy.exit_code == 2
    assert json.loads(legacy.output)["status"] == "incomplete"
    output = tmp_path / "comparison.html"
    rendered = run(before, after, "--out", str(output), "--json")
    assert rendered.exit_code == 0, rendered.output
    result = json.loads(rendered.output)
    assert result["status"] == "pass" and result["comparison_status"] == "incomplete"
    assert result["before"]["status"] == "violation" and result["after"]["status"] == "incomplete"
    assert output.is_file()


def test_html_comparison_keeps_model_correspondence_witnesses_and_qualification_distinct(qualification_campaign, tmp_path):  # noqa: F811
    _, _, state, _, _, invoke, verify = qualification_campaign
    _, first = verify()
    _, second = verify()
    for payload in (first, second):
        bundle = state / "runs" / payload["run_id"]
        manifest = bundle / "manifest.json"
        record = json.loads(manifest.read_text())
        record["claims"][0]["assurance"] = {
            "implementation_checks": [{"check_id": "native", "case_ids": ["target <case>"]}],
            "witness_checks": [{"check_id": "model"}], "bounds": [], "exclusions": [], "deviations": [],
        }
        raw = evidence.json_bytes(record)
        manifest.write_bytes(raw)
        (state / "receipts" / payload["run_id"]).write_text(evidence.digest(raw))
    before_state = _state_snapshot(state)
    output = tmp_path / "qualified-comparison.html"
    result = invoke("evidence", "compare", first["run_id"], second["run_id"], "--out", str(output))
    assert result.exit_code == 0, (result.output, result.exception)
    html = output.read_text().lower()
    assert "formal" in html and "model" in html
    assert "tested implementation correspondence" in html and "native" in html
    assert "reachability witnesses" in html
    assert "qualification" in html and "survive" in html and "error" in html
    assert "target &lt;case&gt;" in html
    assert "private_log_content" not in html
    assert _state_snapshot(state) == before_state


@pytest.mark.parametrize("field", ["statement", "assumptions"])
def test_legacy_changed_claim_content_sorts_first_without_inventing_definition_hash(compare, tmp_path, field):
    state, run = compare
    before, after = "a" * 32, "b" * 32
    for run_id in (before, after):
        record = _two_claim_record("pass")
        record.pop("comparison_inputs")
        if run_id == after:
            record["claims"][1][field] = "Revised legacy requirement" if field == "statement" else ["Revised assumption"]
        record.update(schema_version=2, kind="project-verification", run_id=run_id,
                      revision=run_id + "0" * 8, repo="/private/secret-repo",
                      base=("b" if run_id == before else "d") * 40,
                      runner="host", contract_digest=("c" if run_id == before else "e") * 64,
                      status="pass", behavior_skipped=False, reproduce="PRIVATE_COMMAND", tools={})
        evidence.save_run(state, record, {"private.log": b"PRIVATE_RAW_LOG"})
    before_state = _state_snapshot(state)
    output = tmp_path / "legacy-compare.html"
    result = run(before, after, "--across-contracts", "--out", str(output))
    assert result.exit_code == 0, result.output
    html = output.read_text()
    assert html.index("Changed title") < html.index("Stable title")
    assert "Changed title · changed · Changed" in html
    assert "Stable title · stable · Definition unavailable" in html
    assert "Claim definition: unavailable" in html
    assert _state_snapshot(state) == before_state
