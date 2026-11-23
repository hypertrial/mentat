"""Worker input is pinned source history and explicitly trusted provisioning data."""

import hashlib
import os
import subprocess

import pytest

from mentat.autonomy_worker import prepare_bundle, provision_script


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def test_bundle_contains_exact_revision_and_no_host_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "remote.origin.url", "ssh://private-token@example.invalid/repo")
    (repo / "source.py").write_text("x = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "first")
    rev = git(repo, "rev-parse", "HEAD")
    (repo / "source.py").write_text("x = 2\n")
    git(repo, "commit", "-qam", "second")
    bundle = prepare_bundle(repo, rev)
    assert b"private-token" not in bundle
    path = tmp_path / "source.bundle"
    path.write_bytes(bundle)
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(path), str(checkout)], check=True)
    assert git(checkout, "rev-parse", "HEAD") == rev
    assert (checkout / "source.py").read_text() == "x = 1\n"
    assert "private-token" not in (checkout / ".git/config").read_text()
    assert git(checkout, "rev-list", "--all", "--count") == "1"
    with pytest.raises(ValueError):
        prepare_bundle(repo, "HEAD")


def test_provision_script_embeds_baseline_and_rejects_links(tmp_path):
    project = b"[project]\nname = 'baseline'\n"
    lock = b'version = 1\n'
    (tmp_path / "pyproject.toml").write_bytes(project)
    (tmp_path / "uv.lock").write_bytes(lock)
    script = provision_script(tmp_path)
    assert hashlib.sha256(project).hexdigest().encode() in script
    assert hashlib.sha256(lock).hexdigest().encode() in script
    assert b"--locked" in script and b"--no-install-project" in script
    assert b"/opt/mentat-tools" in script
    (tmp_path / "uv.lock").unlink()
    os.symlink(tmp_path / "pyproject.toml", tmp_path / "uv.lock")
    with pytest.raises(ValueError, match="trusted baseline"):
        provision_script(tmp_path)


def test_bundle_size_limit_fails_closed(tmp_path, monkeypatch):
    import mentat.autonomy_worker as worker

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "source").write_bytes(os.urandom(4096))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    rev = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(worker, "MAX_BUNDLE", 128)
    with pytest.raises(ValueError, match="limit"):
        worker.prepare_bundle(repo, rev)


def test_bundled_provisioner_matches_operator_script():
    from pathlib import Path

    from mentat.autonomy_worker import PROVISION_SCRIPT

    assert PROVISION_SCRIPT == (Path(__file__).parents[1] / "scripts/provision-autonomy-worker").read_bytes()


def test_profile_identity_tracks_exact_baseline_and_provisioner(tmp_path):
    from mentat.autonomy_worker import PROVISION_SCRIPT, profile_identity

    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")
    identity = profile_identity(tmp_path)
    assert {key: identity[key] for key in ("pyproject_sha256", "uv_lock_sha256", "provisioner_sha256")} == {
        "pyproject_sha256": hashlib.sha256(b"[project]\n").hexdigest(),
        "uv_lock_sha256": hashlib.sha256(b"version = 1\n").hexdigest(),
        "provisioner_sha256": hashlib.sha256(PROVISION_SCRIPT).hexdigest(),
    }
    assert all(value.encode() in provision_script(tmp_path) for value in identity.values())
    (tmp_path / "uv.lock").write_text("version = 2\n")
    assert profile_identity(tmp_path)["uv_lock_sha256"] != identity["uv_lock_sha256"]
