import json
import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from mentat import cli
from mentat.project import load_project, snapshot
from mentat.runner import run_behavior


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "fixture")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def trial(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / ".mentat").mkdir()
    manifest = {
        "version": 1,
        "claims": [{"id": "claim", "statement": "Original claim"}],
        "formal": [
            {"id": "proof", "claim": "claim", "engine": "z3", "source": "p.smt2", "expected": "sat"}
        ],
        "behavior": [
            {"id": "behavior", "claim": "claim", "argv": [sys.executable, "{checks}/check.py"]}
        ],
        "allow_host": True,
    }
    (repo / ".mentat/p.smt2").write_text("(assert true)\n")
    (repo / ".mentat/check.py").write_text("pass\n")
    (repo / "candidate.txt").write_text("base\n")
    return repo, manifest


def save(repo, manifest):
    (repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    return commit(repo)


def scope(repo, base):
    return ["--repo", str(repo), "--base", base, "--runner", "host"]


def test_base_files_and_selectors_use_approved_literals_not_candidate(trial):
    repo, manifest = trial
    trusted = repo / "tests" / "test_[literal].py"
    trusted.parent.mkdir()
    trusted.write_text("approved test\n")
    fixture = repo / "tests" / "fixture data.txt"
    fixture.write_text("approved fixture\n")
    manifest["base_files"] = ["tests/test_[literal].py", "tests/fixture data.txt"]
    manifest["behavior"][0]["selectors"] = ["tests/test_[literal].py::test_one", "space value"]
    manifest["behavior"][0]["argv"] += ["{selectors}"]
    (repo / ".mentat/check.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "root = Path(__file__).parent / 'base' / 'tests'\n"
        "assert (root / 'test_[literal].py').read_text() == 'approved test\\n'\n"
        "assert (root / 'fixture data.txt').read_text() == 'approved fixture\\n'\n"
        "assert sys.argv[1:] == ['tests/test_[literal].py::test_one', 'space value']\n"
    )
    base = save(repo, manifest)
    trusted.write_text("candidate test\n")
    fixture.write_text("candidate fixture\n")
    (repo / ".mentat/check.py").write_text("raise SystemExit(1)\n")
    candidate = commit(repo)
    trusted.write_text("dirty test\n")
    fixture.write_text("dirty fixture\n")
    project = load_project(repo, base, "host")
    checks, _ = run_behavior(project, snapshot(repo, candidate), None, {"runner": "host"}, "run")
    assert checks[0]["status"] == "pass"
    assert load_project(repo, candidate, "host").fingerprint != project.fingerprint


@pytest.mark.parametrize(
    "path", ["missing.py", "tests", "../escape", "/absolute", ".mentat/check.py", "tests/*.py"]
)
def test_base_files_reject_nonliteral_missing_or_directory(trial, path):
    repo, manifest = trial
    (repo / "tests").mkdir()
    (repo / "tests/test_a.py").write_text("pass\n")
    manifest["base_files"] = [path]
    base = save(repo, manifest)
    with pytest.raises(ValueError):
        load_project(repo, base, "host")


def test_base_files_reject_symlinks(trial):
    repo, manifest = trial
    (repo / "linked.py").symlink_to("candidate.txt")
    manifest["base_files"] = ["linked.py"]
    base = save(repo, manifest)
    with pytest.raises(ValueError):
        load_project(repo, base, "host")


def test_contract_without_new_options_keeps_existing_behavior(trial):
    repo, manifest = trial
    manifest["behavior"][0]["argv"] += ["{selectors}", "literal{selectors}"]
    (repo / ".mentat/check.py").write_text(
        "import sys\nassert sys.argv[1:] == ['{selectors}', 'literal{selectors}']\n"
    )
    base = save(repo, manifest)
    project = load_project(repo, base, "host")
    checks, _ = run_behavior(project, snapshot(repo, base), None, {"runner": "host"}, "run")
    assert checks[0]["status"] == "pass"


def test_doctor_reports_behavior_and_formal_prerequisites_together(trial, monkeypatch):
    repo, manifest = trial
    manifest["host_requirements"] = {
        "executables": ["mentat-no-such-declared-command"],
        "python_distributions": [],
        "loopback": False,
    }
    manifest["behavior"][0]["argv"] = ["mentat-no-such-direct-command"]
    base = save(repo, manifest)

    def missing_formal_tool(project):
        raise ValueError("Install Lean 4.31.0")

    monkeypatch.setattr(cli, "tool_info", missing_formal_tool)
    result = CliRunner().invoke(cli.app, ["doctor", *scope(repo, base), "--json"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["status"] == "incomplete"
    assert "mentat-no-such-declared-command" in result.output
    assert "mentat-no-such-direct-command" in result.output
    assert "Install Lean 4.31.0" in result.output


def test_doctor_reports_denied_loopback_without_launching_project_code(trial, monkeypatch):
    repo, manifest = trial
    from mentat import runner

    manifest["host_requirements"] = {"loopback": True}
    base = save(repo, manifest)

    def denied(*args):
        raise PermissionError("loopback denied")

    def unexpected(*args, **kwargs):
        pytest.fail("doctor executed a project command")

    monkeypatch.setattr(runner.socket, "socket", denied)
    monkeypatch.setattr(runner, "_process", unexpected)
    result = CliRunner().invoke(cli.app, ["doctor", *scope(repo, base), "--json"])
    assert result.exit_code == 2, result.output
    record = json.loads(result.output)
    assert record["runtime"]["prerequisites"]["loopback"]["status"] == "missing"
    assert "loopback" in record["detail"]


def test_missing_direct_command_only_invalidates_its_own_check(trial):
    repo, manifest = trial
    manifest["behavior"].append(
        {"id": "missing", "claim": "claim", "argv": ["mentat-no-such-direct-command"]}
    )
    base = save(repo, manifest)
    project = load_project(repo, base, "host")
    checks, _ = run_behavior(project, snapshot(repo, base), None, {"runner": "host"}, "run")
    assert [check["status"] for check in checks] == ["pass", "incomplete"]


def test_against_requires_full_commit_and_review_is_shown_at_approval(trial, tmp_path, monkeypatch):
    repo, manifest = trial
    old = save(repo, manifest)
    manifest["claims"][0]["statement"] = "Changed claim"
    new = save(repo, manifest)
    runner = CliRunner()
    prefix = ["--state", str(tmp_path / "state")]
    args = scope(repo, new)
    invalid = runner.invoke(
        cli.app, prefix + ["spec", "check", *args, "--against", old[:12], "--json"]
    )
    assert invalid.exit_code != 0
    checked = runner.invoke(cli.app, prefix + ["spec", "check", *args, "--against", old, "--json"])
    assert checked.exit_code == 0, checked.output
    assert "Original claim" in checked.output and "Changed claim" in checked.output
    digest = json.loads(checked.output)["contract_digest"]
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)
    approved = runner.invoke(cli.app, prefix + ["spec", "approve", digest, *args, "--against", old])
    assert approved.exit_code == 0, approved.output
    assert "Original claim" in approved.output and "Changed claim" in approved.output
    invalid_approval = runner.invoke(
        cli.app, prefix + ["spec", "approve", digest, *args, "--against", "HEAD"]
    )
    assert invalid_approval.exit_code != 0


def test_host_reproduce_survives_changed_outer_path(trial, tmp_path, monkeypatch):
    repo, manifest = trial
    base = save(repo, manifest)
    state = tmp_path / "state"
    invocation = tmp_path / "invocation with spaces"
    invocation.mkdir()
    monkeypatch.chdir(invocation)
    original_path = os.environ["PATH"]
    monkeypatch.setenv("PATH", "relative tools:" + original_path)
    runner = CliRunner()
    prefix = ["--state", str(state)]
    args = scope(repo, base)
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)
    checked = runner.invoke(cli.app, prefix + ["spec", "check", *args, "--json"])
    assert checked.exit_code == 0, checked.output
    digest = json.loads(checked.output)["contract_digest"]
    approved = runner.invoke(cli.app, prefix + ["spec", "approve", digest, *args])
    assert approved.exit_code == 0, approved.output
    verified = runner.invoke(cli.app, prefix + ["verify", *args, "--rev", base, "--json"])
    assert verified.exit_code == 0, verified.output
    record = json.loads(verified.output)
    exported = tmp_path / "exported"
    result = runner.invoke(
        cli.app, prefix + ["evidence", "export", record["run_id"], "--out", str(exported), "--json"]
    )
    assert result.exit_code == 0, result.output
    saved = json.loads((exported / "manifest.json").read_text())
    assert saved["runtime"]["invocation_path"] == "relative tools:" + original_path
    assert not saved["runtime"]["path"].startswith("relative tools:")
    command = saved["host_reproduce"]
    completed = subprocess.run(
        ["/bin/sh", "-c", command],
        env={**os.environ, "PATH": "/usr/bin:/bin"},
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert json.loads(completed.stdout)["status"] == "pass"


def test_lean_resolution_survives_formal_staging_cwd(trial, tmp_path, monkeypatch):
    from mentat import project_formal

    repo, manifest = trial
    manifest["formal"] = [
        {"id": "proof", "claim": "claim", "engine": "lean", "source": "Model.lean", "theorem": "t"}
    ]
    (repo / ".mentat/Model.lean").write_text("theorem t : True := trivial\n")
    project = load_project(repo, save(repo, manifest), "host")
    monkeypatch.chdir(tmp_path)
    original_path = os.environ["PATH"]
    tools = tmp_path / "relative tools"
    tools.mkdir()
    lean = tools / "lean"
    lean.write_text(
        "#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'Lean (version 4.31.0)'; else pwd; fi\n"
    )
    lean.chmod(0o755)
    monkeypatch.setenv("PATH", "relative tools:" + original_path)
    selected = project_formal.tool_info(project)["lean"]
    assert selected["executable"] == str(lean)
    stage = tmp_path / "formal stage"
    stage.mkdir()
    assert (
        subprocess.run([selected["executable"], "--version"], cwd=stage, check=True).returncode == 0
    )


def test_missing_host_distribution_skips_all_behavior_before_execution(trial, tmp_path):
    repo, manifest = trial
    marker = tmp_path / "executed"
    (repo / ".mentat/check.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    )
    manifest["host_requirements"] = {
        "executables": [],
        "python_distributions": ["mentat-definitely-not-a-real-distribution-123456"],
        "loopback": False,
    }
    manifest["behavior"].append({**manifest["behavior"][0], "id": "second"})
    base = save(repo, manifest)
    project = load_project(repo, base, "host")
    checks, _ = run_behavior(project, snapshot(repo, base), None, {"runner": "host"}, "run")
    assert [check["status"] for check in checks] == ["incomplete", "incomplete"]
    assert not marker.exists()


def test_empty_selectors_expand_to_no_arguments(trial):
    repo, manifest = trial
    manifest["behavior"][0]["selectors"] = []
    manifest["behavior"][0]["argv"] += ["before", "{selectors}", "after"]
    (repo / ".mentat/check.py").write_text(
        "import sys\nassert sys.argv[1:] == ['before', 'after']\n"
    )
    base = save(repo, manifest)
    project = load_project(repo, base, "host")
    checks, _ = run_behavior(project, snapshot(repo, base), None, {"runner": "host"}, "run")
    assert checks[0]["status"] == "pass"


def test_new_base_file_requires_new_approval(trial, tmp_path, monkeypatch):
    repo, manifest = trial
    (repo / "trusted.txt").write_text("original\n")
    manifest["base_files"] = ["trusted.txt"]
    base = save(repo, manifest)
    runner = CliRunner()
    prefix = ["--state", str(tmp_path / "state")]
    old_scope = scope(repo, base)
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)
    checked = runner.invoke(cli.app, prefix + ["spec", "check", *old_scope, "--json"])
    assert checked.exit_code == 0, checked.output
    digest = json.loads(checked.output)["contract_digest"]
    approved = runner.invoke(cli.app, prefix + ["spec", "approve", digest, *old_scope])
    assert approved.exit_code == 0, approved.output
    (repo / "trusted.txt").write_text("changed\n")
    new_base = commit(repo)
    new_scope = scope(repo, new_base)
    rejected = runner.invoke(cli.app, prefix + ["verify", *new_scope, "--rev", new_base, "--json"])
    assert rejected.exit_code != 0
    assert json.loads(rejected.output)["status"] == "incomplete"
