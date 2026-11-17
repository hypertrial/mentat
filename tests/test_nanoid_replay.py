import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mentat import cli

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "3167e108865702c0539d8e552365ed840ac9d8d9"
FIXED = "58193a1d3aab9877315c42e995eb94667e0b1a4a"


def test_nanoid_fresh_checkout_replay(tmp_path, monkeypatch):
    source = os.environ.get("MENTAT_NANOID_SOURCE")
    if source is None:
        pytest.skip("set MENTAT_NANOID_SOURCE to a local Nano ID mirror to run this test")
    assert Path(source).is_dir(), f"MENTAT_NANOID_SOURCE is not a directory: {source}"
    for tool in ("git", "node", "mentat"):
        assert shutil.which(tool), f"required tool missing from PATH: {tool}"

    exclusions = tmp_path / "global excludes"
    exclusions.write_text(".mentat/\n")
    config = tmp_path / "global git config"
    config.write_text(
        f'[core]\n    excludesFile = "{exclusions}"\n    hooksPath = /dev/null\n'
        "[commit]\n    gpgSign = false\n[user]\n    name = Test\n"
        "    email = test@example.invalid\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)
    runner = CliRunner()

    def command(*args, cwd=None, expected=0):
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
        assert proc.returncode == expected, f"{args}:\n{proc.stdout}\n{proc.stderr}"
        return proc.stdout.strip()

    def invoke(state, args, expected=0):
        result = runner.invoke(cli.app, ["--state", str(state), *args])
        assert result.exit_code == expected, result.output
        return json.loads(result.output)

    def setup(name, source_repo):
        repo = tmp_path / f"{name} repository with spaces"
        command("sh", str(ROOT / "examples/nanoid/setup.sh"), str(repo), str(source_repo))
        base = command("git", "-C", str(repo), "rev-parse", "HEAD")
        return repo, base

    def scope(repo, base):
        return ["--repo", str(repo), "--base", base, "--runner", "host"]

    def approve(repo, base, state):
        args = scope(repo, base)
        assert invoke(state, ["doctor", *args, "--json"])["status"] == "pass"
        assert (
            invoke(state, ["claims", "check", "--repo", str(repo), "--base", base, "--json"])[
                "status"
            ]
            == "pass"
        )
        checked = invoke(state, ["spec", "check", *args, "--json"])
        result = runner.invoke(
            cli.app, ["--state", str(state), "spec", "approve", checked["contract_digest"], *args]
        )
        assert result.exit_code == 0, result.output

    def verify(repo, base, state, rev, expected):
        return invoke(state, ["verify", *scope(repo, base), "--rev", rev, "--json"], expected)

    def check(record, check_id):
        return next(
            item for case in record["cases"] for item in case["checks"] if item["id"] == check_id
        )

    def witness(record):
        name = check(record, "cli-size-replay")["artifacts"]["witness.json"]
        bundle = Path(record["bundle"])
        data = (bundle / name).read_bytes()
        manifest = json.loads((bundle / "manifest.json").read_text())
        assert hashlib.sha256(data).hexdigest() == manifest["artifacts"][name]
        return name, data

    repo, base = setup("first", source)
    state = tmp_path / "first state with spaces"
    approve(repo, base, state)
    before = verify(repo, base, state, BEFORE, 1)
    after = verify(repo, base, state, FIXED, 0)
    assert (before["status"], after["status"]) == ("violation", "pass")
    for record in (before, after):
        assert {item["id"]: item["status"] for item in record["model_checks"]} == {
            "size-rule": "pass"
        }
        assert {item["id"] for item in record["behavior_checks"]} == {
            "cli-size-replay",
            "cli-size-native",
        }
        assert {item["id"] for case in record["cases"] for item in case["checks"]} == {
            "cli-size-replay",
            "cli-size-native",
        }

    def native(record):
        name = check(record, "cli-size-native")["stdout"]
        return json.loads((Path(record["bundle"]) / name).read_text())

    old_native, new_native = native(before), native(after)
    assert check(before, "cli-size-native")["exit_code"] == 10
    node_version = command("node", "--version")
    assert node_version and old_native["node"] == new_native["node"] == node_version
    assert (old_native["passed"], old_native["failed"], old_native["outcome"]) == (
        8,
        3,
        "violation",
    )
    assert (new_native["passed"], new_native["failed"], new_native["outcome"]) == (11, 0, "pass")
    comparison = invoke(state, ["evidence", "compare", before["run_id"], after["run_id"], "--json"])
    assert comparison["status"] == "pass"
    assert comparison["before"]["run_id"] == before["run_id"]
    assert comparison["after"]["run_id"] == after["run_id"]
    claims = {claim["id"]: claim for claim in comparison["claims"]}
    checks = {item["id"]: item for item in claims["cli.size"]["checks"]}
    assert set(checks) == {"size-rule", "cli-size-replay", "cli-size-native"}
    assert checks["cli-size-replay"]["before"]["status"] == "violation"
    assert checks["cli-size-replay"]["after"]["status"] == "pass"
    assert checks["cli-size-native"]["before"]["status"] == "violation"
    assert checks["cli-size-native"]["after"]["status"] == "pass"
    assert comparison["environment_changed"] is False

    exported = tmp_path / "exported evidence with spaces"
    invoke(state, ["evidence", "export", before["run_id"], "--out", str(exported), "--json"])
    artifact, original = witness(before)
    assert (exported / artifact).read_bytes() == original
    manifest = json.loads((exported / "manifest.json").read_text())
    assert hashlib.sha256(original).hexdigest() == manifest["artifacts"][artifact]
    reproduced = json.loads(
        command("/bin/sh", "-c", manifest["host_reproduce"], cwd=tmp_path, expected=1)
    )
    assert reproduced["revision"] == BEFORE
    assert reproduced["status"] == "violation"
    assert witness(reproduced)[1] == original

    second_repo, second_base = setup("second", repo)
    second_state = tmp_path / "second state with spaces"
    rejected = verify(second_repo, second_base, state, BEFORE, 2)
    assert rejected["status"] == "incomplete"
    approve(second_repo, second_base, second_state)
    second = verify(second_repo, second_base, second_state, BEFORE, 1)
    assert second["status"] == "violation"
    assert witness(second)[1] == original

    command("git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "switch", "--detach", BEFORE)
    (repo / "test/bin.test.js").write_text("// candidate substitutes its own tests\n")
    command("git", "-C", str(repo), "add", "test/bin.test.js")
    command("git", "-C", str(repo), "commit", "--no-gpg-sign", "-qm", "replace candidate tests")
    altered = command("git", "-C", str(repo), "rev-parse", "HEAD")
    tampered = verify(repo, base, state, altered, 1)
    assert (
        check(tampered, "cli-size-native")["status"] == check(before, "cli-size-native")["status"]
    )
    assert witness(tampered)[1] == witness(before)[1]
    assert native(tampered) == old_native


@pytest.mark.parametrize("test_source", [None, "throw new Error('failed to collect tests')\n"])
def test_nanoid_native_launcher_rejects_incomplete_tests(tmp_path, test_source):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    checks = tmp_path / "checks"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    if test_source is not None:
        test_file = checks / "base/test/bin.test.js"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(test_source)
    result = subprocess.run(
        [node, ROOT / "examples/nanoid/contract/check-native.mjs"],
        cwd=tmp_path,
        env={
            **os.environ,
            "MENTAT_CHECKS_DIR": str(checks),
            "MENTAT_ARTIFACTS_DIR": str(artifacts),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(result.stdout)["outcome"] == "incomplete"
    assert list(artifacts.iterdir()) == []
