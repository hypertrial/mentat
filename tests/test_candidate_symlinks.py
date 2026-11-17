import hashlib
import io
import json
import os
import subprocess
import tarfile

import pytest

from mentat.project import load_project, snapshot
from mentat.runner import _stage, _stage_archive


def git(repo, *args, data=None):
    return subprocess.run(["git", "-C", str(repo), "-c", "core.precomposeUnicode=false", *args], input=data, capture_output=True, check=True).stdout.strip()


@pytest.fixture
def make_revision(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")

    def make(entries):
        git(repo, "read-tree", "--empty")
        for path, (mode, data) in entries.items():
            oid = git(repo, "hash-object", "-w", "--stdin", data=data).decode()
            git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
        tree = git(repo, "write-tree").decode()
        commit = git(repo, "commit-tree", tree, data=b"test\n").decode()
        return repo, commit

    return make


def expected_digest(files):
    digest = hashlib.sha256()
    for path, (mode, data) in sorted(files.items()):
        for item in (path.encode(), str(mode).encode(), data):
            digest.update(len(item).to_bytes(8, "big"))
            digest.update(item)
    return digest.hexdigest()


def test_snapshot_raw_link_digest_host_and_archive(make_revision, tmp_path):
    repo, revision = make_revision({
        "data/target": ("100644", b"original\n"),
        "data/nested/link": ("120000", b"../target"),
        ".mentat/candidate-only": ("100644", b"excluded"),
    })
    captured = snapshot(repo, revision)
    expected = {"data/target": (0o644, b"original\n"), "data/nested/link": (0o120000, b"../target")}
    assert captured.files == expected
    assert captured.digest == expected_digest(expected)
    host = tmp_path / "host"
    _stage(host, captured.files)
    assert (host / "data/nested/link").is_symlink()
    assert os.readlink(host / "data/nested/link") == "../target"
    assert (host / "data/nested/link").read_bytes() == b"original\n"
    archive = io.BytesIO()
    _stage_archive(archive, captured.files, {})
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(fileobj=archive) as tar:
        link = tar.getmember("mentat/candidate/data/nested/link")
        assert link.issym() and link.linkname == "../target"
        tar.extractall(extracted, filter="data")
    assert (extracted / "mentat/candidate/data/nested/link").read_bytes() == b"original\n"


def test_regular_file_digest_unchanged(make_revision):
    repo, revision = make_revision({"file": ("100644", b"hello")})
    captured = snapshot(repo, revision)
    assert captured.files == {"file": (0o644, b"hello")}
    assert captured.digest == expected_digest(captured.files)


@pytest.mark.parametrize("target", [b"/etc/passwd", b"../../outside", b"", b"\xff", b"foo\\bar", b"foo\nbar", b"foo\x01bar", b"missing/../target", b"missing", b"dir", b".mentat/check.py"])
def test_snapshot_rejects_unsafe_or_unresolvable_targets(make_revision, target):
    repo, revision = make_revision({"link": ("120000", target), "target": ("100644", b"ok"),
                                    "dir/file": ("100644", b"ok"), ".mentat/check.py": ("100644", b"secret")})
    with pytest.raises(ValueError):
        snapshot(repo, revision)


@pytest.mark.parametrize("links", [
    {"link": b"other", "other": b"target"},
    {"link": b"other", "other": b"link"},
    {"link": b"other/../target", "other": b"dir/file"},
])
def test_snapshot_rejects_chained_cyclic_and_traversed_links(make_revision, links):
    entries = {name: ("120000", target) for name, target in links.items()}
    entries.update({"target": ("100644", b"ok"), "dir/file": ("100644", b"ok")})
    repo, revision = make_revision(entries)
    with pytest.raises(ValueError):
        snapshot(repo, revision)


@pytest.mark.parametrize("paths", [
    {"a": (0o644, b"x"), "a/b": (0o644, b"y")},
    {"Dir/a": (0o644, b"x"), "dir/b": (0o644, b"y")},
    {"é/a": (0o644, b"x"), "e\u0301/b": (0o644, b"y")},
])
def test_staging_rejects_prefix_and_portability_aliases(tmp_path, paths):
    with pytest.raises((ValueError, OSError)):
        _stage(tmp_path / "host", paths)
    with pytest.raises((ValueError, OSError)):
        _stage_archive(io.BytesIO(), paths, {})


@pytest.mark.parametrize("entry", ["contract", "formal", "base"])
def test_approved_contract_and_base_files_still_reject_symlinks(make_revision, entry):
    manifest = {"version": 1, "claims": [{"id": "claim", "statement": "valid"}],
                "formal": [{"id": "proof", "claim": "claim", "engine": "z3", "source": "proof.smt2", "expected": "sat"}],
                "behavior": [{"id": "behavior", "claim": "claim", "argv": ["python3", "{checks}/check.py"]}],
                "base_files": ["base.txt"], "allow_host": True}
    entries = {".mentat/contract.json": ("100644", json.dumps(manifest).encode()),
               ".mentat/proof.smt2": ("100644", b"(assert true)"),
               ".mentat/check.py": ("100644", b"pass"), "base.txt": ("100644", b"base"),
               "base-target.txt": ("100644", b"base")}
    if entry == "contract":
        entries[".mentat/manifest.json"] = entries[".mentat/contract.json"]
        entries[".mentat/contract.json"] = ("120000", b"manifest.json")
    elif entry == "formal":
        entries[".mentat/proof-target.smt2"] = entries[".mentat/proof.smt2"]
        entries[".mentat/proof.smt2"] = ("120000", b"proof-target.smt2")
    else:
        entries["base.txt"] = ("120000", b"base-target.txt")
    repo, revision = make_revision(entries)
    with pytest.raises(ValueError):
        load_project(repo, revision, "host")


@pytest.mark.parametrize("paths", [
    {"Dir/a": ("100644", b"x"), "dir/b": ("100644", b"y")},
    {"é/a": ("100644", b"x"), "e\u0301/b": ("100644", b"y")},
])
def test_snapshot_with_symlink_rejects_implicit_parent_aliases(make_revision, paths):
    paths.update({"target": ("100644", b"safe"), "link": ("120000", b"target")})
    repo, revision = make_revision(paths)
    with pytest.raises(ValueError):
        snapshot(repo, revision)


@pytest.mark.parametrize("target", [b"/etc/passwd", b"../outside", b"missing/../target", b"other"])
def test_staging_rejects_unsafe_candidate_links(tmp_path, target):
    files = {"link": (0o120000, target), "target": (0o644, b"safe"), "other": (0o120000, b"target")}
    with pytest.raises(ValueError):
        _stage(tmp_path / "stage", files)
    with pytest.raises(ValueError):
        _stage_archive(io.BytesIO(), files, {})
