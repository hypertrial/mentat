import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPLAY = Path(__file__).resolve().parents[1] / "examples/nemo/contract/replay.py"


def case(name="sample", grouped=False, groups=None):
    return {
        "name": name,
        "grouped": grouped,
        "groups": groups or [{"id": 0, "values": [{"type": "integer", "half_units": 2}]}],
    }


def replay(tmp_path, cases, responses):
    fixture = tmp_path / "cases.json"
    fixture.write_text(json.dumps(cases))
    fake = tmp_path / "nmo"
    fake_script = tmp_path / "fake_nmo.py"
    fake_script.write_text(
        "import os, sys\n"
        f"responses = {responses!r}\n"
        "output, code = responses[os.path.basename(os.getcwd())]\n"
        "sys.stdout.write(output)\n"
        "sys.exit(code)\n"
    )
    fake.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fake_script))} \"$@\"\n")
    fake.chmod(0o755)
    env = os.environ.copy()
    env.pop("MENTAT_ARTIFACTS_DIR", None)
    proc = subprocess.run(
        [sys.executable, str(REPLAY), "--nmo", str(fake), "--cases", str(fixture)],
        capture_output=True, text=True, env=env, check=False,
    )
    return proc.returncode, json.loads(proc.stdout)


@pytest.mark.parametrize(
    "output",
    [
        'out("1"^^<http://www.w3.org/2001/XMLSchema#double>).\n',
        "",
        "out(1).\nout(1).\n",
    ],
    ids=["wrong-type", "missing-total", "duplicate-total"],
)
def test_wrong_type_missing_or_duplicate_total_is_violation(tmp_path, output):
    code, result = replay(tmp_path, [case()], {"sample": (output, 0)})
    assert code == 1
    assert result["status"] == result["results"][0]["status"] == "violation"


def test_group_totals_match_independent_of_output_order(tmp_path):
    groups = [
        {"id": 0, "values": [{"type": "integer", "half_units": 2}]},
        {"id": 99, "values": [{"type": "integer", "half_units": 4}]},
    ]
    code, result = replay(
        tmp_path, [case(grouped=True, groups=groups)],
        {"sample": ("out(99, 2).\nout(0, 1).\n", 0)},
    )
    assert code == 0
    assert result["status"] == result["results"][0]["status"] == "pass"


@pytest.mark.parametrize(
    ("output", "exit_code"), [("out(1).\n", 1), ("out(not-a-number).\n", 0)]
)
def test_failed_child_or_malformed_output_is_incomplete(tmp_path, output, exit_code):
    code, result = replay(tmp_path, [case()], {"sample": (output, exit_code)})
    assert code == 2
    assert result["status"] == result["results"][0]["status"] == "incomplete"


def test_incomplete_takes_precedence_over_violation(tmp_path):
    code, result = replay(
        tmp_path, [case("bad-total"), case("failed-child")],
        {"bad-total": ("out(2).\n", 0), "failed-child": ("", 1)},
    )
    assert code == 2
    assert result["status"] == "incomplete"
    assert [item["status"] for item in result["results"]] == ["violation", "incomplete"]


def test_setup_commits_contract_with_global_excludes_and_accepts_own_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    example = tmp_path / "example"
    shutil.copytree(root / "examples/nemo", example)
    source = tmp_path / "source"
    source.mkdir()
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
    excludes = tmp_path / "global excludes"
    excludes.write_text(".mentat/\n")
    config = tmp_path / "gitconfig"
    config.write_text(
        f'[core]\n    excludesFile = "{excludes}"\n    hooksPath = /dev/null\n'
        "[clone]\n    defaultRemoteName = upstream\n"
        "[user]\n    name = Test\n    email = test@example.invalid\n"
        "[commit]\n    gpgSign = false\n"
    )
    env["GIT_CONFIG_GLOBAL"] = str(config)

    # Kept local: runs with this test's custom env (GIT_CONFIG_GLOBAL), which the shared helper lacks.
    def git(repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git(source, "init", "-q", "--initial-branch=original")
    (source / "Cargo.lock").write_text("synthetic lockfile\n")
    (source / "candidate").write_text("before\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "before")
    before = git(source, "rev-parse", "HEAD")
    git(source, "switch", "-c", "fixed-topic")
    (source / "candidate").write_text("fixed\n")
    git(source, "commit", "-qam", "fixed")
    fixed = git(source, "rev-parse", "HEAD")
    git(source, "switch", "original")
    script = example / "setup.sh"
    script.write_text(
        script.read_text()
        .replace("c8396e9ad20ced0252816772223a2b23c227368d", before)
        .replace("93f219c31f227f04fbde3a1ec272343a69c4c967", fixed)
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        '#!/bin/sh\n[ "$1" = "--context" ] && [ "$3" = "image" ] && [ "$4" = "inspect" ]\n'
    )
    docker.chmod(0o755)
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    env["NEMO_IMAGE"] = "example/nemo@sha256:" + "a" * 64

    def setup(name, source_repo):
        destination = tmp_path / name
        result = subprocess.run(
            ["sh", str(script), str(destination), str(source_repo)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        repo = destination / "nemo"
        assert (
            json.loads(git(repo, "show", "HEAD:.mentat/contract.json"))["docker_image"]
            == env["NEMO_IMAGE"]
        )
        assert git(repo, "rev-parse", "HEAD^1") == before
        assert git(repo, "rev-parse", "refs/heads/mentat-nemo-fixed") == fixed
        return repo

    first = setup("first", source)
    second = setup("second", first)
    git(second, "switch", "mentat-nemo-fixed")
    setup("from-fixed-branch", second)
    git(first, "branch", "-D", "mentat-nemo-fixed")
    setup("from-legacy-source", first)
