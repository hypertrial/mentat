import copy
import json
import subprocess

import pytest

from mentat.project import load_project, snapshot


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def project_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    checks = repo / ".mentat"
    checks.mkdir()
    manifest = {
        "version": 1,
        "claims": [{"id": "claim", "statement": "candidate is valid"}],
        "formal": [
            {
                "id": "proof",
                "claim": "claim",
                "engine": "z3",
                "source": "proof.smt2",
                "expected": "sat",
            }
        ],
        "behavior": [
            {
                "id": "behavior",
                "claim": "claim",
                "argv": ["python3", "{checks}/check.py"],
                "artifacts": ["result.txt"],
            }
        ],
        "docker_image": "example/test@sha256:" + "a" * 64,
        "allow_host": True,
    }
    (checks / "contract.json").write_text(json.dumps(manifest))
    (checks / "proof.smt2").write_text("(assert true)\n")
    (checks / "check.py").write_text("print('ok')\n")
    (repo / "candidate.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    return repo, git(repo, "rev-parse", "HEAD"), manifest


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def test_trusted_base_and_candidate_snapshots_are_committed_and_separate(project_repo):
    repo, base, _ = project_repo
    (repo / "candidate.txt").write_text("candidate\n")
    (repo / ".mentat/check.py").write_text("print('candidate override')\n")
    candidate = commit(repo, "candidate")
    (repo / "candidate.txt").write_text("dirty\n")
    (repo / ".mentat/check.py").write_text("print('dirty override')\n")
    (repo / "untracked.txt").write_text("untracked\n")
    approved = load_project(repo, base, "host")
    captured = snapshot(repo, candidate)
    assert approved.files["check.py"] == b"print('ok')\n"
    assert captured.files["candidate.txt"][1] == b"candidate\n"
    assert "untracked.txt" not in captured.files
    assert all(not name.startswith(".mentat/") for name in captured.files)
    assert load_project(repo, base, "host").fingerprint == approved.fingerprint


def test_approval_fingerprint_binds_repo_base_and_runner(project_repo, tmp_path):
    repo, base, _ = project_repo
    host = load_project(repo, base, "host")
    assert load_project(repo, base, "docker").fingerprint != host.fingerprint
    (repo / "candidate.txt").write_text("next\n")
    other_base = commit(repo, "next base")
    assert load_project(repo, other_base, "host").fingerprint != host.fingerprint
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    assert load_project(clone, base, "host").fingerprint != host.fingerprint
    with pytest.raises(ValueError):
        snapshot(repo, base[:12])
    for invalid in (base[:12], "HEAD", base.upper()):
        with pytest.raises(ValueError):
            load_project(repo, invalid, "host")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(version=2),
        lambda m: m.update(docker_image="example/test:latest"),
        lambda m: m.update(claims=[]),
        lambda m: m["behavior"][0].update(claim="unknown"),
        lambda m: m["behavior"][0].update(id="proof"),
        lambda m: m["formal"][0].update(source="../escape"),
        lambda m: m["formal"][0].update(source="missing.smt2"),
        lambda m: m["formal"][0].update(expected="unsat"),
        lambda m: m["behavior"][0].update(argv=[""]),
        lambda m: m["behavior"][0].update(argv=["{checks}/../escape"]),
        lambda m: m["behavior"][0].update(timeout_seconds=0),
        lambda m: m["behavior"][0].update(violation_exit_codes=[0]),
        lambda m: m["behavior"][0].update(artifacts=["../output"]),
    ],
)
def test_rejects_malformed_contract(project_repo, mutation):
    repo, _, manifest = project_repo
    manifest = copy.deepcopy(manifest)
    mutation(manifest)
    (repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    bad = commit(repo, "bad manifest")
    with pytest.raises(ValueError):
        load_project(repo, bad, "host")


def test_duplicate_json_keys_and_host_without_permission_rejected(project_repo):
    repo, _, manifest = project_repo
    path = repo / ".mentat/contract.json"
    path.write_text('{"version": 1, "version": 1}')
    bad = commit(repo, "duplicate")
    with pytest.raises(ValueError):
        load_project(repo, bad, "docker")
    manifest["allow_host"] = False
    path.write_text(json.dumps(manifest))
    restricted = commit(repo, "no host")
    with pytest.raises(ValueError):
        load_project(repo, restricted, "host")


def test_snapshot_rejects_unsafe_git_symlink(project_repo):
    repo, _, _ = project_repo
    (repo / "link").symlink_to("/etc/passwd")
    revision = commit(repo, "symlink")
    with pytest.raises(ValueError):
        snapshot(repo, revision)


@pytest.mark.parametrize(
    ("source", "status"),
    [
        ("raise SystemExit(0)", "pass"),
        ("raise SystemExit(1)", "violation"),
        ("raise SystemExit(2)", "incomplete"),
        ("import time; time.sleep(3)", "incomplete"),
    ],
)
def test_host_exit_and_timeout_semantics(project_repo, source, status):
    from dataclasses import replace

    from mentat.runner import run_behavior

    repo, base, _ = project_repo
    project = load_project(repo, base, "host")
    check = project.contract.behavior[0].model_copy(update={"timeout_seconds": 1})
    contract = project.contract.model_copy(update={"behavior": [check]})
    project = replace(
        project, contract=contract, files={**project.files, "check.py": source.encode()}
    )
    checks, _ = run_behavior(project, snapshot(repo, base), None, {"runner": "host"}, "run")
    assert checks[0]["status"] == status


def test_host_artifacts_and_logs_are_bounded_and_symlinks_rejected(project_repo):
    from dataclasses import replace

    from mentat.runner import run_behavior

    repo, base, _ = project_repo
    project = load_project(repo, base, "host")
    source = (
        "import os\nfrom pathlib import Path\n"
        "assert (Path(os.environ['MENTAT_CANDIDATE_DIR']) / 'candidate.txt').read_text() == 'base\\n'\n"
        "print('log data')\n"
        "(Path(os.environ['MENTAT_ARTIFACTS_DIR']) / 'result.txt').write_text('result')\n"
    )
    project = replace(project, files={**project.files, "check.py": source.encode()})
    checks, artifacts = run_behavior(project, snapshot(repo, base), None, {"runner": "host"}, "run")
    assert checks[0]["status"] == "pass"
    assert artifacts[checks[0]["stdout"]] == b"log data\n"
    assert artifacts[checks[0]["artifacts"]["result.txt"]] == b"result"
    source = (
        "import os\nfrom pathlib import Path\n"
        "(Path(os.environ['MENTAT_ARTIFACTS_DIR']) / 'result.txt').symlink_to('/etc/passwd')\n"
    )
    project = replace(project, files={**project.files, "check.py": source.encode()})
    checks, artifacts = run_behavior(project, snapshot(repo, base), None, {"runner": "host"}, "run")
    assert checks[0]["status"] == "pass"
    assert checks[0]["artifact_omissions"] == ["result.txt"]
    assert not artifacts


def test_host_excessive_output_does_not_pass(project_repo):
    from dataclasses import replace

    from mentat.runner import run_behavior

    repo, base, _ = project_repo
    project = load_project(repo, base, "host")
    project = replace(project, files={**project.files, "check.py": b"print('x' * 1100000)"})
    checks, _ = run_behavior(project, snapshot(repo, base), None, {"runner": "host"}, "run")
    assert checks[0]["status"] == "incomplete"


@pytest.mark.parametrize(
    ("script", "arguments"),
    [
        ("check.py", ["", "{checks}"]),
        ("tools{checks}/check.py", ["value"]),
    ],
)
def test_host_preserves_literals_and_expands_only_the_checks_prefix(
    project_repo, script, arguments
):
    from mentat.runner import run_behavior

    repo, _, manifest = project_repo
    check = repo / ".mentat" / script
    check.parent.mkdir(exist_ok=True)
    check.write_text(f"import sys\nassert sys.argv[1:] == {arguments!r}\n")
    manifest["behavior"][0]["argv"] = ["python3", "{checks}/" + script, *arguments]
    (repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    base = commit(repo, "literal arguments and approved script path")
    project = load_project(repo, base, "host")
    results, _ = run_behavior(project, snapshot(repo, base), None, {}, "run")
    assert results[0]["status"] == "pass"


def test_host_candidate_cannot_shadow_command_through_relative_path(
    project_repo, tmp_path, monkeypatch
):
    import os

    from mentat.runner import run_behavior

    repo, _, manifest = project_repo
    executable = "mentat-test-launcher"
    trusted = tmp_path / "trusted-tools"
    trusted.mkdir()
    for path, code in ((trusted / executable, 1), (repo / executable, 0)):
        path.write_text(f"#!/bin/sh\nexit {code}\n")
        path.chmod(0o755)
    manifest["behavior"][0]["argv"] = [executable]
    (repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    base = commit(repo, "candidate and trusted launcher use the same name")
    monkeypatch.setenv("PATH", os.pathsep.join((".", str(trusted), os.environ["PATH"])))
    project = load_project(repo, base, "host")
    results, _ = run_behavior(project, snapshot(repo, base), None, {}, "run")
    assert results[0]["status"] == "violation", results


@pytest.mark.parametrize(
    ("base_source", "counterexample", "expected", "status"),
    [
        ("(assert true)", None, "sat", "pass"),
        ("(assert false)", None, "sat", "violation"),
        ("(declare-const x Int)\n(assert (> x 0))", "(assert (<= x 0))", "unsat", "pass"),
        ("(declare-const x Int)\n(assert (> x 0))", "(assert (> x 1))", "unsat", "violation"),
        ("(assert false)", "(assert false)", "unsat", "incomplete"),
        (
            "(declare-datatype Color ((red) (blue)))\n(declare-const c Color)\n(assert (= c red))",
            "(assert (= c blue))",
            "unsat",
            "pass",
        ),
        (
            "(declare-datatypes ((Color 0)) (((red) (blue))))\n(declare-const c Color)\n(assert (= c red))",
            "(assert (= c blue))",
            "unsat",
            "pass",
        ),
        ("(invalid command)", None, "sat", "incomplete"),
        ("(assert! false)", None, "sat", "incomplete"),
        ("(assert.foo false)", None, "sat", "incomplete"),
        ("(; model\nassert(= 1 1))", None, "sat", "pass"),
    ],
)
def test_z3_vacuity_and_expected_result(
    project_repo, base_source, counterexample, expected, status
):
    from dataclasses import replace

    from mentat.project_formal import run_formal, tool_info

    repo, base, _ = project_repo
    project = load_project(repo, base, "host")
    check = project.contract.formal[0].model_copy(
        update={
            "expected": expected,
            "counterexample": "counter.smt2" if counterexample else None,
        }
    )
    project = replace(
        project,
        contract=project.contract.model_copy(update={"formal": [check]}),
        files={
            **project.files,
            "proof.smt2": base_source.encode(),
            "counter.smt2": (counterexample or "").encode(),
        },
    )
    assert run_formal(project, tool_info(project))[0]["status"] == status


@pytest.mark.parametrize(
    ("source", "theorem", "status"),
    [
        ("theorem verified : True := trivial", "verified", "pass"),
        ("theorem verified : True := trivial", "missing", "incomplete"),
        ("axiom bad : False\ntheorem verified : False := bad", "verified", "incomplete"),
        ("def verified : True := trivial", "verified", "incomplete"),
        ("def unrelated := 0", "Nat.add_zero", "incomplete"),
    ],
)
def test_lean_named_theorem_and_axiom_audit(project_repo, source, theorem, status):
    from dataclasses import replace

    from mentat.project_formal import run_formal, tool_info

    repo, base, _ = project_repo
    project = load_project(repo, base, "host")
    check = project.contract.formal[0].model_copy(
        update={
            "engine": "lean",
            "source": "Model.lean",
            "theorem": theorem,
            "expected": None,
            "counterexample": None,
        }
    )
    project = replace(
        project,
        contract=project.contract.model_copy(update={"formal": [check]}),
        files={**project.files, "Model.lean": source.encode()},
    )
    try:
        tools = tool_info(project)
    except ValueError as exc:
        pytest.skip(str(exc))
    assert run_formal(project, tools)[0]["status"] == status


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../../escape", "a\\b", "a//b"])
def test_materialization_rejects_unsafe_paths(tmp_path, name):
    from mentat.runner import _stage

    with pytest.raises(ValueError):
        _stage(tmp_path / "stage", {name: (0o644, b"unsafe")})
    assert not (tmp_path / "escape").exists()


def test_cli_approval_verification_acceptance_and_scope(project_repo, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from mentat import cli

    repo, base, _ = project_repo
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)
    runner = CliRunner()
    scope = ["--repo", str(repo), "--base", base, "--runner", "host"]
    prefix = ["--state", str(state)]
    checked = runner.invoke(cli.app, prefix + ["spec", "check"] + scope + ["--json"])
    assert checked.exit_code == 0, checked.output
    digest = json.loads(checked.output)["contract_digest"]
    approved = runner.invoke(cli.app, prefix + ["spec", "approve", digest] + scope)
    assert approved.exit_code == 0, approved.output
    verified = runner.invoke(cli.app, prefix + ["verify", "--rev", base] + scope + ["--json"])
    assert verified.exit_code == 0, verified.output
    result = json.loads(verified.output)
    assert result["schema_version"] == 2
    assert result["kind"] == "project-verification"
    assert result["status"] == "pass"
    accepted = runner.invoke(cli.app, prefix + ["accept", result["run_id"]] + scope)
    assert accepted.exit_code == 0, accepted.output
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    clone_scope = ["--repo", str(clone), "--base", base, "--runner", "host"]
    unapproved_clone = runner.invoke(
        cli.app, prefix + ["verify", "--rev", base] + clone_scope + ["--json"]
    )
    assert unapproved_clone.exit_code != 0
    other_scope = ["--repo", str(repo), "--base", base, "--runner", "docker"]
    wrong_mode = runner.invoke(
        cli.app, prefix + ["verify", "--rev", base] + other_scope + ["--json"]
    )
    assert wrong_mode.exit_code != 0
    (repo / "candidate.txt").write_text("new base\n")
    newer = commit(repo, "new base")
    wrong_base = ["--repo", str(repo), "--base", newer, "--runner", "host"]
    rejected = runner.invoke(cli.app, prefix + ["accept", result["run_id"]] + wrong_base)
    assert rejected.exit_code != 0
    bundle = state / "runs" / result["run_id"]
    (bundle / "report.md").write_text("tampered")
    tampered = runner.invoke(cli.app, prefix + ["accept", result["run_id"]] + scope)
    assert tampered.exit_code != 0


def test_cli_violation_emits_one_json_record_and_exits_one(project_repo, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from mentat import cli

    repo, _, _ = project_repo
    (repo / ".mentat/check.py").write_text("raise SystemExit(1)\n")
    base = commit(repo, "approve a check that finds a violation")
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)
    scope = ["--repo", str(repo), "--base", base, "--runner", "host"]
    prefix = ["--state", str(tmp_path / "state")]
    runner = CliRunner()
    checked = runner.invoke(cli.app, prefix + ["spec", "check", "--json", *scope])
    digest = json.loads(checked.output)["contract_digest"]
    approved = runner.invoke(cli.app, prefix + ["spec", "approve", digest, *scope])
    assert approved.exit_code == 0, approved.output
    result = runner.invoke(cli.app, prefix + ["verify", "--rev", base, "--json", *scope])
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["status"] == "violation"


def test_candidate_mentat_unsafe_entries_cannot_override_approved_checks(project_repo):
    repo, base, _ = project_repo
    check = repo / ".mentat/check.py"
    check.unlink()
    check.symlink_to("/etc/passwd")
    (repo / ".mentat/contract.json").write_text("invalid candidate contract")
    candidate = commit(repo, "untrusted checks")
    assert load_project(repo, base, "host").files["check.py"] == b"print('ok')\n"
    assert not any(path.startswith(".mentat/") for path in snapshot(repo, candidate).files)


def test_duplicate_paths_in_git_tree_are_rejected(project_repo):
    repo, base, _ = project_repo
    blob = git(repo, "rev-parse", f"{base}:candidate.txt")
    entry = f"100644 blob {blob}\tcandidate.txt\n"
    tree = subprocess.run(
        ["git", "-C", str(repo), "mktree"],
        input=entry * 2,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    rev = git(repo, "commit-tree", tree, "-m", "ambiguous candidate")
    with pytest.raises(ValueError, match="duplicate Git tree path"):
        snapshot(repo, rev)


def test_lean_approval_discloses_host_execution_even_for_docker(
    project_repo, tmp_path, monkeypatch
):
    from typer.testing import CliRunner

    from mentat import cli

    repo, _, manifest = project_repo
    manifest["formal"] = [
        {
            "id": "proof",
            "claim": "claim",
            "engine": "lean",
            "source": "Model.lean",
            "theorem": "verified",
        }
    ]
    (repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    (repo / ".mentat/Model.lean").write_text("theorem verified : True := trivial\n")
    base = commit(repo, "lean contract")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    decisions = []
    monkeypatch.setattr(cli, "operator_confirm", decisions.append)
    runner = CliRunner()
    scope = ["--repo", str(repo), "--base", base, "--runner", "docker"]
    checked = runner.invoke(cli.app, ["spec", "check", *scope, "--json"])
    assert checked.exit_code == 0, checked.output
    summary = json.loads(checked.output)
    assert summary["lean_runs_on_host"] is True
    approved = runner.invoke(
        cli.app, ["--state", str(state), "spec", "approve", summary["contract_digest"], *scope]
    )
    assert approved.exit_code == 0, approved.output
    assert len(decisions) == 1
    assert "docker" in decisions[0].lower()
    assert "lean" in decisions[0].lower() and "host" in decisions[0].lower()


@pytest.mark.parametrize("excluded", [True, False])
def test_snapshot_large_git_listing_respects_selection(project_repo, excluded):
    repo, _, _ = project_repo

    def plumbing(command, data):
        return subprocess.run(
            ["git", "-C", str(repo), command],
            input=data,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input="x",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    count = 100 if excluded else 22
    leaf = plumbing(
        "mktree", "".join(f"100644 blob {blob}\tf{i:03d}{'x' * 235}\n" for i in range(count))
    )
    dirs = 100 if excluded else 185
    subtree = plumbing(
        "mktree",
        "".join(
            f"040000 tree {leaf}\td{i:03d}{'y' * (0 if excluded else 245)}\n" for i in range(dirs)
        ),
    )
    tree = (
        plumbing("mktree", f"040000 tree {subtree}\t.mentat\n100644 blob {blob}\tselected.txt\n")
        if excluded
        else subtree
    )
    rev = git(repo, "commit-tree", tree, "-m", "large listing")
    captured = snapshot(repo, rev)
    if excluded:
        assert captured.files == {"selected.txt": (0o644, b"x")}
    else:
        assert len(captured.files) == 4070
        assert all(value[1] == b"x" for value in captured.files.values())


def test_snapshot_excludes_only_exact_root_mentat_scope(project_repo):
    repo, base, _ = project_repo
    blob = git(repo, "rev-parse", f"{base}:candidate.txt")
    approved_tree = git(repo, "rev-parse", f"{base}:.mentat")
    leaf = subprocess.run(
        ["git", "-C", str(repo), "mktree"],
        input=f"100644 blob {blob}\tfile.txt\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    nested = subprocess.run(
        ["git", "-C", str(repo), "mktree"],
        input=f"040000 tree {leaf}\t.mentat\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", str(repo), "mktree"],
        input=(
            f"040000 tree {approved_tree}\t.mentat\n"
            f"040000 tree {leaf}\t.Mentat\n"
            f"040000 tree {nested}\tnested\n"
            f"100644 blob {blob}\t:(literal)*\n"
        ),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    rev = git(repo, "commit-tree", tree, "-m", "case-sensitive scope")
    assert set(snapshot(repo, rev).files) == {
        ".Mentat/file.txt",
        "nested/.mentat/file.txt",
        ":(literal)*",
    }
    assert load_project(repo, rev, "host").files["check.py"] == b"print('ok')\n"


def test_snapshot_does_not_descend_into_missing_candidate_checks(project_repo):
    repo, base, _ = project_repo
    blob = git(repo, "rev-parse", f"{base}:candidate.txt")
    tree = subprocess.run(
        ["git", "-C", str(repo), "mktree", "--missing"],
        input=f"040000 tree {'1' * len(blob)}\t.mentat\n100644 blob {blob}\tcandidate.txt\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    rev = git(repo, "commit-tree", tree, "-m", "missing candidate checks")
    assert snapshot(repo, rev).files == snapshot(repo, base).files
    with pytest.raises(ValueError, match="ls-tree"):
        load_project(repo, rev, "host")


def test_snapshot_preserves_committed_unicode_path_spelling(project_repo):
    repo, base, _ = project_repo
    git(repo, "config", "core.precomposeUnicode", "true")
    blob = git(repo, "rev-parse", f"{base}:candidate.txt").encode()
    paths = ("Café", "Cafe\u0301")
    tree = (
        subprocess.check_output(
            ["git", "-C", str(repo), "mktree", "-z"],
            input=b"".join(
                b"100644 blob " + blob + b"\t" + path.encode() + b"\0" for path in paths
            ),
        )
        .decode()
        .strip()
    )
    rev = git(repo, "commit-tree", tree, "-m", "stored Unicode names")
    assert set(snapshot(repo, rev).files) == set(paths)


def test_verify_prioritizes_incomplete_over_violation(project_repo, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from mentat import cli

    repo, _, manifest = project_repo
    second = {**manifest["formal"][0], "id": "second"}
    manifest["formal"].append(second)
    (repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    base = commit(repo, "two formal checks")
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)
    monkeypatch.setattr(
        cli,
        "run_formal",
        lambda *args: [
            {"status": "violation", "kind": "z3", "detail": "failed"},
            {"status": "incomplete", "kind": "z3", "detail": "unavailable"},
        ],
    )
    scope = ["--repo", str(repo), "--base", base, "--runner", "host"]
    prefix = ["--state", str(tmp_path / "state")]
    runner = CliRunner()
    checked = runner.invoke(cli.app, prefix + ["spec", "check", "--json", *scope])
    assert checked.exit_code == 0, checked.output
    digest = json.loads(checked.output)["contract_digest"]
    approved = runner.invoke(cli.app, prefix + ["spec", "approve", digest, *scope])
    assert approved.exit_code == 0, approved.output
    result = runner.invoke(cli.app, prefix + ["verify", "--rev", base, "--json", *scope])
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["status"] == "incomplete"
