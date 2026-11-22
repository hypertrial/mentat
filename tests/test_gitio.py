import json
import os
import subprocess
import time

import pytest
from helpers import cat_object, git, replaced_loose_object

from mentat import evidence, gitio, project

CONTRACT = {
    "version": 1,
    "allow_host": True,
    "claims": [{"id": "alpha", "statement": "Primary claim"}],
    "formal": [
        {"id": "proof", "claim": "alpha", "engine": "z3", "source": "model.smt2", "expected": "sat"}
    ],
    "behavior": [{"id": "native", "claim": "alpha", "argv": ["python3", "{checks}/check.py"]}],
}


def _fixed_repo(root):
    """A tiny repository whose tree bytes, modes and paths are fixed, so digests can be pinned."""
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.test")
    (root / "a.txt").write_bytes(b"alpha\n")
    (root / "dir").mkdir()
    (root / "dir/b.py").write_bytes(b"print(1)\n")
    os.chmod(root / "dir/b.py", 0o755)
    (root / ".mentat").mkdir()
    (root / ".mentat/contract.json").write_text(json.dumps(CONTRACT))
    (root / ".mentat/model.smt2").write_text("(assert true)\n")
    (root / ".mentat/check.py").write_text("pass\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    return git(root, "rev-parse", "HEAD")


def test_snapshot_and_approved_file_digests_are_pinned(tmp_path):
    revision = _fixed_repo(tmp_path)
    snapshot = project.snapshot(tmp_path, revision)
    assert snapshot.files == {"a.txt": (0o644, b"alpha\n"), "dir/b.py": (0o755, b"print(1)\n")}
    assert snapshot.digest == "dd2df11186ee11201b0356c60463461c1499211c90c697a837bded841a41c3b1"
    assert project.tree_digest({"a.txt": (0o644, b"alpha\n")}) == (
        "b7a1aedec12278ccae22e2a55eb0998a7fd0b63888822232983a7e00bf1611cb"
    )
    _, _, _, files = project.contract_inputs(tmp_path, revision)
    assert sorted(files) == ["check.py", "contract.json", "model.smt2"]
    approved = project.tree_digest({path: (0o644, data) for path, data in files.items()})
    assert approved == "10232f2f1a3e7498cf211b4216e77905a457a4e2b81fa85c888cba4eb4a78810"


def test_canonical_json_bytes_and_digest_are_pinned():
    value = {"b": [1, 2.5, None, True], "a": "é", "c": {"z": 0, "y": "x\n"}}
    raw = evidence.json_bytes(value)
    assert raw == b'{"a":"\\u00e9","b":[1,2.5,null,true],"c":{"y":"x\\n","z":0}}\n'
    assert evidence.digest(raw) == (
        "07c3469cad6bf3f7759d697bbb96dad4c06f3adb91a4b121bef36c30572814d7"
    )
    assert evidence.decode_json(raw) == value


def test_every_git_invocation_carries_both_no_replace_protections(tmp_path, monkeypatch):
    from mentat.showcases.polaris import evaluation, replay
    from mentat.showcases.polaris.cli import candidate_repo

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.test")
    files = {
        ".mentat/contract.json": json.dumps(CONTRACT),
        ".mentat/model.smt2": "(assert true)\n",
        ".mentat/check.py": "pass\n",
        f"{replay._PREFIX}command/setup.py": "setup\n",
        "client/python/tests/test_setup_command.py": "# Frozen test\n",
    }
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    revision = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(evaluation, "BEFORE", revision)

    invocations = []
    real_popen = subprocess.Popen

    def recording(argv, *args, **kwargs):
        if argv[0] == "git":
            invocations.append((argv, kwargs.get("env") or {}))
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording)
    project.snapshot(repo, revision)
    project.contract_inputs(repo, revision)
    replay.snapshot(repo, revision)
    assert evaluation._upstream_test(repo, "test_setup_command.py") == "# Frozen test\n"
    assert candidate_repo(repo, tmp_path / "state") == repo.resolve()
    assert len(invocations) >= 10
    for argv, env in invocations:
        assert argv[1] == "--no-replace-objects", argv
        assert env.get("GIT_NO_REPLACE_OBJECTS") == "1", argv
    shows = [argv for argv, _ in invocations if "show" in argv]
    assert shows and all("--no-textconv" in argv for argv in shows)


@pytest.fixture
def fake_git(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def _fake_git(bin_dir, body):
    script = bin_dir / "git"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(0o755)


def _assert_reaped(pidfile):
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_ls_tree_kills_child_when_a_record_path_exceeds_the_cap(tmp_path, fake_git):
    pidfile = tmp_path / "pid"
    # Output with no NUL separator grows past the path cap; exec keeps the child's pid.
    body = f'echo $$ > "{pidfile}"\nhead -c 5000 /dev/zero | tr "\\0" a\nexec sleep 30'
    _fake_git(fake_git, body)
    started = time.monotonic()
    with pytest.raises(ValueError, match="^Git tree path exceeds size limit$"):
        list(gitio.ls_tree(tmp_path, "HEAD", "-z", [], time.monotonic() + 10))
    # Without the kill the call would only return when the 30 second sleep ends.
    assert time.monotonic() - started < 15
    _assert_reaped(pidfile)


def test_ls_tree_kills_child_at_the_deadline(tmp_path, fake_git):
    pidfile = tmp_path / "pid"
    _fake_git(fake_git, f'echo $$ > "{pidfile}"\nexec sleep 30')
    started = time.monotonic()
    with pytest.raises(ValueError, match="^git command timed out: ls-tree$"):
        list(gitio.ls_tree(tmp_path, "HEAD", "-z", [], time.monotonic() + 1))
    # Without the kill the call would only return when the 30 second sleep ends.
    assert time.monotonic() - started < 15
    _assert_reaped(pidfile)


@pytest.fixture(scope="module")
def object_repos(tmp_path_factory):
    repos = {}
    for object_format in ("sha1", "sha256"):
        root = tmp_path_factory.mktemp(object_format)
        args = ["--object-format=sha256"] if object_format == "sha256" else []
        git(root, "init", "-q", *args)
        git(root, "config", "user.name", "Test")
        git(root, "config", "user.email", "test@example.test")
        (root / "a.txt").write_bytes(b"alpha\n")
        (root / "dir").mkdir()
        (root / "dir/b.py").write_bytes(b"print(1)\n")
        git(root, "add", ".")
        git(root, "commit", "-qm", "base")
        repos[object_format] = (root, git(root, "rev-parse", "HEAD"))
    return repos


def _substituted_object(root, revision, target):
    """Return (object ID, object type, same-length content) for a substituted copy of one object."""
    if target == "commit":
        raw = cat_object(root, "commit", revision)
        return revision, "commit", raw.replace(b"\n\nbase\n", b"\n\nchanged\n", 1)
    if target == "tree":
        oid = git(root, "rev-parse", f"{revision}:dir")
        return oid, "tree", cat_object(root, "tree", oid).replace(b"b.py", b"c.py", 1)
    return git(root, "rev-parse", f"{revision}:a.txt"), "blob", b"omega\n"


@pytest.mark.parametrize(
    ("object_format", "target"),
    [("sha1", "blob"), ("sha1", "tree"), ("sha1", "commit"), ("sha256", "blob")],
)
def test_snapshot_refuses_loose_object_whose_content_does_not_match_its_id(
    object_repos, object_format, target
):
    root, revision = object_repos[object_format]
    oid, kind, substituted = _substituted_object(root, revision, target)
    with replaced_loose_object(root, oid, kind, substituted):
        with pytest.raises(ValueError, match="integrity"):
            project.snapshot(root, revision)


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_unmodified_snapshot_matches_committed_bytes(object_repos, object_format):
    root, revision = object_repos[object_format]
    snapshot = project.snapshot(root, revision)
    assert snapshot.files == (
        {"a.txt": (0o644, b"alpha\n"), "dir/b.py": (0o644, b"print(1)\n")}
    )
