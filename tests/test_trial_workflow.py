import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import commit, git
from typer.testing import CliRunner

from mentat import cli
from mentat.project import load_project, snapshot
from mentat.project_formal import LEAN_VERSION
from mentat.runner import run_behavior


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


def test_against_git_diff_uses_the_same_commit_objects_as_mentat(trial):
    repo, manifest = trial
    trusted = repo / "trusted.txt"
    manifest["base_files"] = ["trusted.txt"]
    trusted.write_text("original fixture\n")
    old = save(repo, manifest)
    trusted.write_text("changed fixture\n")
    new = commit(repo)
    git(repo, "replace", old, new)

    result = CliRunner().invoke(
        cli.app, ["spec", "check", *scope(repo, new), "--against", old, "--json"]
    )
    assert result.exit_code == 0, result.output
    delta = json.loads(result.output)["delta"]
    assert "trusted.txt" in delta["base_files"]
    diff = subprocess.check_output(shlex.split(delta["git_diff"]), text=True)
    assert "-original fixture" in diff and "+changed fixture" in diff


@pytest.mark.parametrize(("example", "other"), [("quickstart", "fixed"), ("python-src", "bad")])
def test_example_creates_tags_with_global_signing_enabled(tmp_path, example, other):
    script = Path(__file__).resolve().parents[1] / "examples" / example / "create.sh"
    repo = tmp_path / "example repo"
    subprocess.run(
        [str(script), str(repo)],
        env={
            **os.environ,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "tag.gpgSign",
            "GIT_CONFIG_VALUE_0": "true",
            "GIT_CONFIG_KEY_1": "gpg.program",
            "GIT_CONFIG_VALUE_1": str(tmp_path / "unavailable-signer"),
        },
        check=True,
    )
    tags = {"mentat-demo-base", f"mentat-demo-{other}"}
    assert set(git(repo, "tag", "--list").splitlines()) == tags
    for tag in tags:
        assert git(repo, "cat-file", "-t", f"refs/tags/{tag}") == "commit"


@pytest.mark.parametrize("example", ["quickstart", "python-src"])
def test_example_commits_contract_despite_global_git_excludes(tmp_path, example):
    excludes = tmp_path / "global-excludes"
    excludes.write_text(".mentat/\n")
    config = tmp_path / "gitconfig"
    config.write_text(f'[core]\n    excludesFile = "{excludes}"\n')
    repo = tmp_path / "example"
    script = Path(__file__).resolve().parents[1] / "examples" / example / "create.sh"
    subprocess.run(
        [str(script), str(repo)],
        check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"},
        capture_output=True,
        text=True,
    )
    assert json.loads(git(repo, "show", "mentat-demo-base:.mentat/contract.json"))["version"] == 1
    assert (
        ".mentat/clamp.smt2"
        in git(repo, "ls-tree", "-r", "--name-only", "mentat-demo-base").splitlines()
    )


@pytest.mark.parametrize("command", ["verify", "export", "compare"])
def test_cyclic_state_alias_returns_incomplete_json(trial, tmp_path, command):
    repo, manifest = trial
    base = save(repo, manifest)
    alias = tmp_path / "cyclic-state"
    alias.symlink_to(alias.name)
    arguments = {
        "verify": ["verify", *scope(repo, base), "--rev", base, "--json"],
        "export": ["evidence", "export", "a" * 32, "--out", str(tmp_path / "out"), "--json"],
        "compare": ["evidence", "compare", "a" * 32, "b" * 32, "--json"],
    }
    result = CliRunner().invoke(cli.app, ["--state", str(alias), *arguments[command]])
    assert result.exit_code == 2, (result.output, result.exception)
    assert json.loads(result.output)["status"] == "incomplete"
    assert alias.is_symlink()
    assert not (tmp_path / "out").exists()


def test_state_alias_into_candidate_does_not_create_state(trial, tmp_path):
    repo, manifest = trial
    base = save(repo, manifest)
    target = repo / "new-state"
    alias = tmp_path / "state-alias"
    alias.symlink_to(target, target_is_directory=True)
    result = CliRunner().invoke(
        cli.app,
        ["--state", str(alias), "verify", *scope(repo, base), "--rev", base, "--json"],
    )
    assert result.exit_code == 2, result.output
    assert "state must be outside the candidate repository" in json.loads(result.output)["detail"]
    assert not target.exists()


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


def _script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _lean_project(trial):
    repo, manifest = trial
    manifest["formal"] = [
        {"id": "proof", "claim": "claim", "engine": "lean", "source": "Model.lean", "theorem": "t"}
    ]
    (repo / ".mentat/Model.lean").write_text("theorem t : True := trivial\n")
    return load_project(repo, save(repo, manifest), "host")


def _toolchain(prefix, version=LEAN_VERSION):
    (prefix / "lib/lean").mkdir(parents=True)
    _script(
        prefix / "bin/lean",
        f'case "$1" in --version) echo "Lean (version {version})";; '
        f'--print-libdir) echo "{prefix}/lib/lean";; esac\n',
    )
    return prefix / "bin/lean"


def _launcher(tmp_path, monkeypatch, prefix_output):
    # Like elan, this launcher finds its toolchains only through the operator's HOME.
    home = tmp_path / "home"
    home.mkdir()
    (home / "toolchain-prefix").write_text(prefix_output)
    _script(
        tmp_path / "launcher/lean",
        'test -f "$HOME/toolchain-prefix" || { echo "no default toolchain" >&2; exit 1; }\n'
        'test "$1" = --print-prefix || { echo "Lean (version 9.9.9)"; exit 0; }\n'
        'ls -A > "$HOME/listing"\nenv > "$HOME/env"\ncat "$HOME/toolchain-prefix"\n',
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{tmp_path / 'launcher'}{os.pathsep}{os.environ['PATH']}")
    return home


def test_lean_launcher_resolves_to_installed_toolchain_binary(trial, tmp_path, monkeypatch):
    from mentat import project_formal

    project = _lean_project(trial)
    prefix = tmp_path / "toolchain"
    lean = _toolchain(prefix)
    home = _launcher(tmp_path, monkeypatch, f"{prefix}\n")
    monkeypatch.setenv("ELAN_TOOLCHAIN", f"leanprover/lean4:v{LEAN_VERSION}")
    monkeypatch.setenv("UNDECLARED_SECRET", "sentinel")
    monkeypatch.chdir(project.root)
    tools = project_formal.tool_info(project)
    assert tools["lean"] == {
        "version": LEAN_VERSION,
        "executable": str(lean),
        "libdir": str(prefix / "lib/lean"),
    }
    # The launcher ran in an empty directory, away from any project toolchain file.
    assert (home / "listing").read_text() == ""
    # It received the toolchain selection but not the rest of the operator's environment.
    launcher_env = (home / "env").read_text().splitlines()
    assert f"ELAN_TOOLCHAIN=leanprover/lean4:v{LEAN_VERSION}" in launcher_env
    assert not any(line.startswith("UNDECLARED_SECRET=") for line in launcher_env)
    # Formal checks then run the resolved binary, not the launcher.
    calls = []

    def record(args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(project_formal, "_run", record)
    assert project_formal.run_formal(project, tools)[0]["status"] == "incomplete"
    assert calls and set(calls) == {str(lean)}


@pytest.mark.parametrize(
    "case", ["empty", "relative", "two-lines", "no-binary", "wrong-version", "launcher-fails"]
)
def test_lean_launcher_that_cannot_select_the_pinned_toolchain_is_rejected(
    trial, tmp_path, monkeypatch, case
):
    from mentat import project_formal

    project = _lean_project(trial)
    prefix = tmp_path / "toolchain"
    if case != "no-binary":
        _toolchain(prefix, "4.36.0" if case == "wrong-version" else LEAN_VERSION)
    else:
        prefix.mkdir()
    output = {"empty": "", "relative": "toolchain\n", "two-lines": f"{prefix}\n{prefix}\n"}.get(
        case, f"{prefix}\n"
    )
    home = _launcher(tmp_path, monkeypatch, output)
    if case == "launcher-fails":
        (home / "toolchain-prefix").unlink()
    if case == "relative":
        # The relative prefix names a real toolchain from here; only its form rejects it.
        monkeypatch.chdir(tmp_path)
        assert Path("toolchain/bin/lean").is_file()
    with pytest.raises(ValueError, match="ELAN_TOOLCHAIN"):
        project_formal.tool_info(project)


@pytest.mark.parametrize("version", [LEAN_VERSION, "4.36.0"])
def test_direct_lean_install_is_not_resolved_through_its_prefix(
    trial, tmp_path, monkeypatch, version
):
    from mentat import project_formal

    project = _lean_project(trial)
    prefix = tmp_path / "direct"
    log = tmp_path / "calls"
    (prefix / "lib/lean").mkdir(parents=True)
    _script(
        prefix / "bin/lean",
        f'echo "$1" >> "{log}"\n'
        f'case "$1" in --version) echo "Lean (version {version})";; '
        f'--print-libdir) echo "{prefix}/lib/lean";; --print-prefix) echo "{prefix}";; esac\n',
    )
    monkeypatch.setenv("PATH", f"{prefix / 'bin'}{os.pathsep}{os.environ['PATH']}")
    if version == LEAN_VERSION:
        selected = project_formal.tool_info(project)["lean"]
        assert selected["executable"] == str(prefix / "bin/lean")
        assert "--print-prefix" not in log.read_text()
    else:
        with pytest.raises(ValueError, match=f"Lean {LEAN_VERSION} is required"):
            project_formal.tool_info(project)
        # It ran, so it is not a launcher and is never asked for a prefix.
        assert log.read_text().split() == ["--version"]


def test_lean_launcher_on_a_relative_path_entry_is_not_resolved(trial, tmp_path, monkeypatch):
    from mentat import project_formal

    project = _lean_project(trial)
    _toolchain(tmp_path / "toolchain")
    home = _launcher(tmp_path, monkeypatch, f"{tmp_path / 'toolchain'}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "launcher" + os.pathsep + os.environ["PATH"].split(os.pathsep, 1)[1])
    with pytest.raises(ValueError, match=f"Lean {LEAN_VERSION} is required"):
        project_formal.tool_info(project)
    assert not (home / "listing").exists()


def test_missing_host_prerequisites_skip_all_behavior_before_execution(
    trial, tmp_path, monkeypatch
):
    from mentat import runner

    repo, manifest = trial
    marker = tmp_path / "executed"
    (repo / ".mentat/check.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    )
    manifest["host_requirements"] = {
        "executables": ["mentat-missing-executable-one", "mentat-missing-executable-two"],
        "python_distributions": ["mentat-definitely-not-a-real-distribution-123456"],
        "loopback": True,
    }

    def denied(*args):
        raise PermissionError("loopback denied")

    monkeypatch.setattr(runner.socket, "socket", denied)
    manifest["behavior"].append({**manifest["behavior"][0], "id": "second"})
    base = save(repo, manifest)
    project = load_project(repo, base, "host")
    checks, _ = run_behavior(project, snapshot(repo, base), None, {"runner": "host"}, "run")
    assert [check["status"] for check in checks] == ["incomplete", "incomplete"]
    for check in checks:
        for missing in (
            "mentat-missing-executable-one",
            "mentat-missing-executable-two",
            "mentat-definitely-not-a-real-distribution-123456",
            "loopback",
        ):
            assert missing in check["detail"]
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


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js is required for the Nano ID probe"
)
@pytest.mark.parametrize("candidate", ["fixed", "historical", "broken"])
def test_nanoid_probe_classifies_candidate_results(tmp_path, candidate):
    repo = tmp_path / "candidate"
    binary = repo / "bin" / "nanoid.js"
    binary.parent.mkdir(parents=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    behaviors = {
        "fixed": (
            "process.stderr.write('Size must be positive integer\\n'); process.exitCode = 1;",
            0,
        ),
        "historical": (
            "process.stdout.write(process.argv[3] === '1.5' ? 'x\\n' : '\\n');",
            1,
        ),
        "broken": ("throw new Error('Failed to initialize');", 2),
    }
    invalid, expected_status = behaviors[candidate]
    binary.write_text(
        "if (process.argv[3] === '7') { process.stdout.write('abc-d_E\\n'); }"
        f" else {{ {invalid} }}\n"
    )
    script = Path(__file__).resolve().parents[1] / "examples/nanoid/contract/check-cli.mjs"
    outcome = subprocess.run(
        ["node", str(script)],
        cwd=repo,
        env={**os.environ, "MENTAT_ARTIFACTS_DIR": str(artifacts)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert outcome.returncode == expected_status, outcome.stderr
    witness = (artifacts / "witness.json").read_text()
    records = json.loads(witness)
    assert [record["input"] for record in records] == ["0.5", "1.5", "Infinity", "7"]
    assert records[-1] == {"input": "7", "status": 0, "stderr": "", "stdout_length": 8}
    if candidate != "broken":
        assert "abc-d_E" not in witness
    if candidate == "historical":
        assert [record["stdout_length"] for record in records[:-1]] == [1, 2, 1]


@pytest.mark.parametrize(
    "names",
    [
        ["PATH"],
        ["home"],
        ["Mentat_secret"],
        ["9BAD"],
        ["a-b"],
        [""],
        ["REPEAT", "REPEAT"],
        [f"VAR_{i}" for i in range(129)],
    ],
)
def test_host_environment_rejects_invalid_names(trial, names):
    repo, manifest = trial
    manifest["behavior"][0]["host_environment"] = names
    base = save(repo, manifest)
    with pytest.raises(ValueError):
        load_project(repo, base, "host")


def test_host_environment_rejects_docker_and_requires_allow_host(trial):
    repo, manifest = trial
    manifest["docker_image"] = "example/test@sha256:" + "a" * 64
    manifest["behavior"][0]["host_environment"] = ["PRIVATE_CONFIG"]
    base = save(repo, manifest)
    with pytest.raises(ValueError, match="docker"):
        load_project(repo, base, "docker")
    manifest["allow_host"] = False
    manifest["behavior"][0]["host_environment"] = []
    base = save(repo, manifest)
    with pytest.raises(ValueError, match="allow_host"):
        load_project(repo, base, "docker")


def test_empty_host_environment_is_omitted_from_serialized_checks(trial):
    repo, manifest = trial
    project = load_project(repo, save(repo, manifest), "host")
    assert "host_environment" not in project.contract.behavior[0].model_dump(exclude_none=True)
    manifest["behavior"][0]["host_environment"] = ["PRIVATE_CONFIG"]
    project = load_project(repo, save(repo, manifest), "host")
    assert project.contract.behavior[0].model_dump(exclude_none=True)["host_environment"] == [
        "PRIVATE_CONFIG"
    ]


def test_host_environment_is_per_check_and_reports_missing(trial, monkeypatch):
    repo, manifest = trial
    monkeypatch.setenv("SCOPED_CONFIG", "sensitive-value")
    monkeypatch.setenv("UNDECLARED_CONFIG", "hidden-value")
    monkeypatch.setenv("EMPTY_CONFIG", "")
    manifest["behavior"][0]["host_environment"] = ["SCOPED_CONFIG"]
    manifest["behavior"].append(
        {
            "id": "missing",
            "claim": "claim",
            "argv": [sys.executable, "{checks}/never.py"],
            "host_environment": ["EMPTY_CONFIG", "ABSENT_CONFIG"],
        }
    )
    (repo / ".mentat/check.py").write_text(
        "import os\nassert os.environ['SCOPED_CONFIG'] == 'sensitive-' + 'value'\n"
        "assert 'UNDECLARED_CONFIG' not in os.environ and 'EMPTY_CONFIG' not in os.environ\n"
    )
    (repo / ".mentat/never.py").write_text("raise Exception('must not run')\n")
    base = save(repo, manifest)
    project = load_project(repo, base, "host")
    runtime = {"runner": "host", "path": os.environ["PATH"], "executables": {}}
    checks, artifacts = run_behavior(project, snapshot(repo, base), None, runtime, "run")
    assert [check["status"] for check in checks] == ["pass", "incomplete"]
    assert "EMPTY_CONFIG" in checks[1]["detail"] and "ABSENT_CONFIG" in checks[1]["detail"]
    assert runtime["prerequisites"]["host_environment"]["SCOPED_CONFIG"]["status"] == "available"
    assert "sensitive-value" not in json.dumps([runtime, checks])
    assert all(b"sensitive-value" not in item for item in artifacts.values())
    doctor = CliRunner().invoke(cli.app, ["doctor", *scope(repo, base), "--json"])
    assert doctor.exit_code == 2
    assert "SCOPED_CONFIG" in doctor.output and "ABSENT_CONFIG" in doctor.output
    assert "sensitive-value" not in doctor.output


def test_host_environment_snapshot_includes_negative_control_and_later_checks(trial, monkeypatch):
    from mentat import runner

    repo, manifest = trial
    monkeypatch.setenv("SNAPSHOT_CONFIG", "first")
    manifest["behavior"][0].update(
        host_environment=["SNAPSHOT_CONFIG"],
        violation_exit_codes=[10],
        negative_control={"args": ["--bad"], "violation_exit_code": 10},
    )
    manifest["behavior"].append(
        {
            "id": "second",
            "claim": "claim",
            "argv": [sys.executable, "{checks}/check.py"],
            "host_environment": ["SNAPSHOT_CONFIG"],
        }
    )
    (repo / ".mentat/check.py").write_text(
        "import os, sys\nassert os.environ['SNAPSHOT_CONFIG'] == 'first'\nsys.exit(10 if len(sys.argv) > 1 else 0)\n"
    )
    base = save(repo, manifest)
    project = load_project(repo, base, "host")
    original = runner._process
    calls = 0

    def mutate(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        monkeypatch.setenv("SNAPSHOT_CONFIG", "changed")
        return result

    monkeypatch.setattr(runner, "_process", mutate)
    results, _ = run_behavior(
        project, snapshot(repo, base), None, {"path": os.environ["PATH"], "executables": {}}, "run"
    )
    assert calls == 3
    assert [result["status"] for result in results] == ["pass", "pass"]
    assert results[0]["negative_control"]["status"] == "violation"


def test_host_environment_approval_claims_and_evidence_show_names_only(
    trial, tmp_path, monkeypatch
):
    from mentat.claims import render_claims

    repo, manifest = trial
    old = save(repo, manifest)
    manifest["behavior"][0]["host_environment"] = ["PRIVATE_CONFIG"]
    new = save(repo, manifest)
    monkeypatch.setenv("PRIVATE_CONFIG", "secret-unique-value")
    project = load_project(repo, new, "host")
    assert project.fingerprint != load_project(repo, old, "host").fingerprint
    claims = render_claims(project.contract).decode()
    assert "PRIVATE_CONFIG" in claims and "secret-unique-value" not in claims
    runner = CliRunner()
    prefix = ["--state", str(tmp_path / "state")]
    args = scope(repo, new)
    checked = runner.invoke(cli.app, prefix + ["spec", "check", *args, "--against", old, "--json"])
    assert checked.exit_code == 0 and "PRIVATE_CONFIG" in checked.output
    assert "secret-unique-value" not in checked.output
    digest = json.loads(checked.output)["contract_digest"]
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)
    old_args = scope(repo, old)
    old_check = runner.invoke(cli.app, prefix + ["spec", "check", *old_args, "--json"])
    old_digest = json.loads(old_check.output)["contract_digest"]
    assert (
        runner.invoke(cli.app, prefix + ["spec", "approve", old_digest, *old_args]).exit_code == 0
    )
    not_approved = runner.invoke(cli.app, prefix + ["verify", *args, "--rev", new, "--json"])
    assert not_approved.exit_code == 2
    assert runner.invoke(cli.app, prefix + ["spec", "approve", digest, *args]).exit_code == 0
    verified = runner.invoke(cli.app, prefix + ["verify", *args, "--rev", new, "--json"])
    assert verified.exit_code == 0, verified.output
    assert "PRIVATE_CONFIG" in verified.output and "secret-unique-value" not in verified.output
    output = tmp_path / "evidence"
    exported = runner.invoke(
        cli.app,
        prefix
        + ["evidence", "export", json.loads(verified.output)["run_id"], "--out", str(output)],
    )
    assert exported.exit_code == 0
    assert all(
        b"secret-unique-value" not in path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize("grouped", [False, True])
def test_host_environment_appears_in_review_claims(trial, grouped):
    from mentat.claims import render_claims

    repo, manifest = trial
    manifest["claims"][0]["title"] = "Example"
    manifest["review"] = {"overview": "Overview", "scope_limits": ["Scope"]}
    if grouped:
        manifest["review"]["groups"] = [{"title": "Group", "claim_ids": ["claim"]}]
    manifest["behavior"][0]["host_environment"] = ["SCOPED_CONFIG"]
    base = save(repo, manifest)
    assert "SCOPED_CONFIG" in render_claims(load_project(repo, base, "host").contract).decode()
