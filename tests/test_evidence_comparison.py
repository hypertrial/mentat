import json
from pathlib import Path

import pytest
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
