import copy
import json

import pytest
from test_project import commit
from typer.testing import CliRunner

from mentat import cli
from mentat.project import load_project

pytest_plugins = ["test_project"]


def invoke(repo, *args):
    return CliRunner().invoke(cli.app, ["claims", *args, "--repo", str(repo)])


def write_contract(repo, contract):
    (repo / ".mentat/contract.json").write_text(json.dumps(contract, ensure_ascii=False))


def test_render_is_deterministic_and_check_detects_drift(project_repo):
    repo, _, manifest = project_repo
    missing = invoke(repo, "check", "--json")
    assert missing.exit_code == 1, missing.output
    json.loads(missing.output)

    first = invoke(repo, "render", "--json")
    assert first.exit_code == 0, first.output
    json.loads(first.output)
    path = repo / ".mentat/claims.md"
    original = path.read_bytes()
    assert original
    assert invoke(repo, "check").exit_code == 0
    assert invoke(repo, "render").exit_code == 0
    assert path.read_bytes() == original

    path.write_bytes(original + b"\nmanual edit\n")
    assert invoke(repo, "check", "--json").exit_code == 1
    assert invoke(repo, "render").exit_code == 0
    assert path.read_bytes() == original

    changed = copy.deepcopy(manifest)
    changed["claims"][0]["statement"] = "A changed claim"
    write_contract(repo, changed)
    assert invoke(repo, "check").exit_code == 1
    assert invoke(repo, "render").exit_code == 0
    assert path.read_bytes() != original
    assert invoke(repo, "check").exit_code == 0


def test_base_check_and_load_ignore_dirty_claims_but_reject_committed_drift(project_repo):
    repo, legacy, _ = project_repo
    # A legacy contract without a committed claims document remains loadable.
    load_project(repo, legacy, "host")
    assert invoke(repo, "check", "--base", legacy).exit_code == 1
    assert invoke(repo, "render").exit_code == 0
    synced = commit(repo, "add generated claims")
    load_project(repo, synced, "host")
    (repo / ".mentat/claims.md").write_text("dirty edit\n")
    assert invoke(repo, "check", "--base", synced).exit_code == 0
    assert invoke(repo, "check").exit_code == 1
    load_project(repo, synced, "host")

    stale = commit(repo, "commit stale claims")
    assert invoke(repo, "check", "--base", stale).exit_code == 1
    with pytest.raises(ValueError):
        load_project(repo, stale, "host")
    result = CliRunner().invoke(
        cli.app,
        ["spec", "check", "--repo", str(repo), "--base", stale, "--runner", "host", "--json"],
    )
    assert result.exit_code != 0, result.output


def test_invalid_inputs_fail_without_overwriting_claims(project_repo):
    repo, base, _ = project_repo
    assert invoke(repo, "render").exit_code == 0
    path = repo / ".mentat/claims.md"
    original = path.read_bytes()
    (repo / ".mentat/contract.json").write_text("{invalid")
    for command in ("render", "check"):
        result = invoke(repo, command, "--json")
        assert result.exit_code == 2, result.output
        json.loads(result.output)
    assert path.read_bytes() == original
    for invalid in ("HEAD", base[:12], "0" * 40):
        result = invoke(repo, "check", "--base", invalid, "--json")
        assert result.exit_code == 2, result.output
        json.loads(result.output)


def test_host_only_contract_and_docker_requirement(project_repo):
    repo, _, manifest = project_repo
    del manifest["docker_image"]
    write_contract(repo, manifest)
    host_only = commit(repo, "host only")
    load_project(repo, host_only, "host")
    with pytest.raises(ValueError):
        load_project(repo, host_only, "docker")
    assert invoke(repo, "render").exit_code == 0
    manifest["allow_host"] = False
    write_contract(repo, manifest)
    invalid = commit(repo, "no available runner")
    with pytest.raises(ValueError):
        load_project(repo, invalid, "host")
    assert invoke(repo, "render").exit_code == 2


def test_render_preserves_claim_text_assumptions_and_check_associations(project_repo):
    repo, _, manifest = project_repo
    manifest["claims"] = [
        {
            "id": "first",
            "statement": "First *literal* café\n# second [line]",
            "assumptions": ["Given `α`\nand <β>", "No _other_ input"],
        },
        {
            "id": "second",
            "statement": "Independent claim 漢字\r\n---\r\n| field | unit |\n| -- | -- |",
        },
    ]
    manifest["formal"][0]["claim"] = "first"
    manifest["formal"].append({**manifest["formal"][0], "id": "proof2"})
    manifest["behavior"][0]["claim"] = "first"
    manifest["behavior"].append({**manifest["behavior"][0], "id": "behavior2"})
    manifest["behavior"].append({**manifest["behavior"][0], "id": "other", "claim": "second"})
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    text = (repo / ".mentat/claims.md").read_text()
    assert "First \\*literal\\* café\n\\# second \\[line\\]" in text
    assert "Given \\`α\\`\n  and \\<β\\>" in text
    assert "No \\_other\\_ input" in text
    assert "Independent claim 漢字" in text
    assert "Independent claim 漢字\n\\---\n" in text
    assert r"\| field \| unit \|" in text
    first, second = text.split("## second", 1)
    assert "## first" in first
    for check in ("proof", "proof2", "behavior", "behavior2"):
        assert f"`{check}`" in first
        assert f"`{check}`" not in second
    assert "`other`" in second
    assert "`other`" not in first


def test_notion_is_only_consulted_when_requested(project_repo, monkeypatch):
    repo, _, _ = project_repo
    assert invoke(repo, "render").exit_code == 0
    base = commit(repo, "add claims")
    calls = []

    def check_notion(page, content, revision, *, publish):
        calls.append((page, content, revision, publish))
        return True

    monkeypatch.setattr(cli, "notion_claims", check_notion)
    assert invoke(repo, "check").exit_code == 0
    assert invoke(repo, "check", "--base", base).exit_code == 0
    assert calls == []
    assert invoke(repo, "check", "--notion", "page").exit_code == 2
    assert calls == []
    assert invoke(repo, "check", "--base", base, "--notion", "page").exit_code == 0
    assert len(calls) == 1
    assert calls[0] == ("page", (repo / ".mentat/claims.md").read_bytes(), base, False)


def test_render_refuses_symlinked_claims_and_contract_directory(project_repo, tmp_path):
    repo, _, _ = project_repo
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = outside / "claims.md"
    destination.write_text("outside")
    directory = repo / ".mentat"
    (directory / "claims.md").symlink_to(destination)
    assert invoke(repo, "render").exit_code == 2
    assert destination.read_text() == "outside"
    (outside / "contract.json").write_bytes((directory / "contract.json").read_bytes())
    directory.rename(repo / "old-mentat")
    directory.symlink_to(outside, target_is_directory=True)
    assert invoke(repo, "render").exit_code == 2
    assert destination.read_text() == "outside"


def test_selfcheck_does_not_fall_back_to_installed_mentat(monkeypatch, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("MENTAT_CANDIDATE_DIR", str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-I", str(root / ".mentat/selfcheck.py"), "contract.base-pinning"],
        env=os.environ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Mentat must import from the candidate" in result.stderr


def test_selfcheck_detects_broken_console_entry_point(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "src/mentat", tmp_path / "src/mentat")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\nmentat = "mentat.cli:missing"\n')
    result = subprocess.run(
        [sys.executable, "-I", str(root / ".mentat/selfcheck.py"), "verification.outcomes"],
        env={**os.environ, "MENTAT_CANDIDATE_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "mentat console command" in result.stderr
