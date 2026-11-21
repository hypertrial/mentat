import hashlib
import os
import selectors
import signal
import subprocess
from unittest.mock import MagicMock

import pytest
from helpers import git

from mentat.showcases.polaris import evaluation, replay
from mentat.showcases.polaris.cli import candidate_repo


def test_snapshot_batches_and_preserves_bytes_and_digest(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    root = tmp_path / replay._PREFIX
    (root / "command").mkdir(parents=True)
    (root / "command/setup.py").write_bytes(b"hello\x00world\n")
    (root / "other.py").write_bytes(b"\xff\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "test")
    rev = git(tmp_path, "rev-parse", "HEAD")
    expected_files = (("command/setup.py", b"hello\x00world\n"), ("other.py", b"\xff\n"))
    digest = hashlib.sha256()
    for path, content in expected_files:
        raw = (replay._PREFIX + path).encode()
        digest.update(len(raw).to_bytes(4, "big") + raw + len(content).to_bytes(8, "big") + content)
    calls = []
    original = replay._git

    def counted(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(replay, "_git", counted)
    monkeypatch.setenv("GIT_DIR", "/nonexistent")
    result = replay.snapshot(tmp_path, rev)
    assert result.files == expected_files
    assert result.digest == digest.hexdigest()
    assert calls == ["rev-parse", "ls-tree", "cat-file", "cat-file"]


def test_batch_output_rejects_truncation(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    root = tmp_path / replay._PREFIX / "command"
    root.mkdir(parents=True)
    (root / "setup.py").write_bytes(b"content")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "test")
    rev = git(tmp_path, "rev-parse", "HEAD")
    original = replay._git

    def truncated(*args, **kwargs):
        data = original(*args, **kwargs)
        return data[:-1] if args[-1] == "--batch" else data

    monkeypatch.setattr(replay, "_git", truncated)
    with pytest.raises(ValueError, match="blob size changed"):
        replay.snapshot(tmp_path, rev)


def test_batch_rejects_oversize_and_missing_objects(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    root = tmp_path / replay._PREFIX / "command"
    root.mkdir(parents=True)
    (root / "setup.py").write_bytes(b"content")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "test")
    rev = git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.setattr(replay, "MAX_CANDIDATE_FILE", 6)
    with pytest.raises(ValueError, match="snapshot size limit"):
        replay.snapshot(tmp_path, rev)
    monkeypatch.setattr(replay, "MAX_CANDIDATE_FILE", 100)
    oid = git(tmp_path, "rev-parse", f"{rev}:{replay._PREFIX}command/setup.py")
    (tmp_path / ".git/objects" / oid[:2] / oid[2:]).unlink()
    with pytest.raises(ValueError, match="invalid Git object"):
        replay.snapshot(tmp_path, rev)


def test_snapshot_bounds_tree_metadata_even_for_empty_files(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    root = tmp_path / replay._PREFIX
    (root / "command").mkdir(parents=True)
    (root / "command/setup.py").touch()
    (root / "other.py").touch()
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "test")
    rev = git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="ls-tree output exceeds size limit"):
        replay._git(tmp_path, "ls-tree", "-rz", rev, limit=8)
    monkeypatch.setattr(replay, "_MAX_FILES", 1)
    with pytest.raises(ValueError, match="too many files"):
        replay.snapshot(tmp_path, rev)


def test_git_kills_child_when_selector_registration_fails(tmp_path, monkeypatch):
    child = MagicMock(pid=4242)
    selector = MagicMock()
    selector.__enter__.return_value = selector
    selector.register.side_effect = OSError("cannot register output pipe")
    killed = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(selectors, "DefaultSelector", lambda: selector)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(OSError, match="cannot register"):
        replay._git(tmp_path, "ls-tree", "HEAD", limit=128)
    assert killed == [(4242, signal.SIGKILL)]


def test_polaris_git_reads_use_selected_checkout(tmp_path, monkeypatch):
    repo = tmp_path / "polaris"
    other = tmp_path / "other"
    for path in (repo, other):
        path.mkdir()
        git(path, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    baseline = repo / "client/python/tests/test_setup_command.py"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("# Frozen test\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "frozen tests")
    monkeypatch.setattr(evaluation, "BEFORE", git(repo, "rev-parse", "HEAD"))
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    assert candidate_repo(repo, tmp_path / "state") == repo.resolve()
    assert evaluation._upstream_test(repo, baseline.name) == "# Frozen test\n"
