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
