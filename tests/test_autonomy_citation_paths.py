"""Public projection permits only safe, revision-bound source citations."""
import subprocess

import pytest

from mentat.autonomy_projection import project
from mentat.autonomy_state import Store


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def source_view(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "source.py").write_text("pass\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    head = git(repo, "rev-parse", "HEAD")
    store = Store(tmp_path / "state.sqlite")
    session = store.new_session(str(repo), head, head, {}, 1, 1000)

    def view(citation, remote="git@github.com:example/project.git"):
        git(repo, "config", "remote.origin.url", remote)
        store.put_record(session, "requirement", {"id": "r", "area": "product", "statement": "Requirement",
                         "citations": [citation]})
        return project(store.integration_snapshot(session), store_path=store.path)["requirements"][0]
    return view, head


def test_canonical_remote_derives_immutable_link_without_trusting_href(source_view):
    view, head = source_view
    requirement = view({"path": "src/a space.py", "revision": head, "href": "https://evil.example/trap"})
    citation = requirement["citations"][0]
    assert citation["path"] == "src/a space.py"
    assert citation["href"] == f"https://github.com/example/project/blob/{head}/src/a%20space.py"
    assert "evil.example" not in str(requirement)


@pytest.mark.parametrize("path", ["/private/secret", "../secret", "src/../secret", "src//secret",
                                  "./secret", r"src\secret", ".git/config", "src/.GIT/config",
                                  "src/a:b", "src/%2e%2e/secret", "src/secret\nvalue"])
def test_unsafe_paths_are_explicitly_omitted(source_view, path):
    view, head = source_view
    requirement = view({"path": path, "revision": head})
    assert "citations" not in requirement
    assert "citations" in requirement["unavailable_fields"]
    assert path not in str(requirement)


@pytest.mark.parametrize("remote", ["https://evil.example/example/project", "http://github.com/example/project",
    "https://user:password@github.com/example/project", "https://github.com:443/example/project",
    "https://github.com/example/project?x=1", "https://github.com/example/project#frag",
    "https://github.com/example/project/extra", "git@evil.example:example/project.git"])
def test_untrusted_remote_cannot_create_link(source_view, remote):
    view, head = source_view
    requirement = view({"path": "src/example.py", "revision": head}, remote)
    assert requirement["citations"][0]["path"] == "src/example.py"
    assert "href" not in requirement["citations"][0]


@pytest.mark.parametrize("revision", ["main", "a" * 39, "a" * 40 + '" onclick="evil()'])
def test_nonimmutable_revision_does_not_create_link(source_view, revision):
    view, _ = source_view
    requirement = view({"path": "src/example.py", "revision": revision})
    assert "href" not in requirement.get("citations", [{}])[0]
