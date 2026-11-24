"""Citation display retains safe source identity without exposing unsafe paths."""

import json
import threading

import pytest
from test_autonomy import repo as repo
from test_autonomy_ui import request

from mentat.autonomy_ui import create_server, render_report


@pytest.fixture
def citation_viewer():
    data = {
        "requirements": [{
            "id": "r1", "area": "product", "statement": "Requirement",
            "citations": [
                {"path": "src/example.py", "revision": "a" * 40, "title": "Source"},
                "docs/legacy.md",
            ],
        }],
    }
    server = create_server(lambda: data, lambda action, args: {"ok": True})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, data
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_structured_source_path_in_live_snapshot_and_export(citation_viewer):
    server, data = citation_viewer
    headers = {"Authorization": f"Bearer {server.token}"}
    status, body, _ = request(server, "GET", "/api/snapshot", headers=headers)
    assert status == 200
    citations = json.loads(body)["requirements"][0]["citations"]
    assert citations[0]["path"] == "src/example.py"
    assert citations[0]["revision"] == "a" * 40
    assert citations[1] == "docs/legacy.md"
    report = render_report(data)
    assert "src/example.py" in report and "docs/legacy.md" in report
    assert "a" * 40 in report


@pytest.mark.parametrize("path", [
    "/private/secret", "../secret", "src/../secret", "src//secret", "./secret",
    r"src\secret", ".git/config", "src/.GIT/config", "src/a:b", "src/%2e%2e/secret",
    "src/secret\nvalue",
])
def test_unsafe_structured_paths_not_exposed(citation_viewer, path):
    server, data = citation_viewer
    data["requirements"][0]["citations"] = [{"path": path, "title": "Citation"}]
    headers = {"Authorization": f"Bearer {server.token}"}
    status, body, _ = request(server, "GET", "/api/snapshot", headers=headers)
    assert status == 200
    citation = json.loads(body)["requirements"][0]["citations"][0]
    assert "path" not in citation
    assert path not in render_report(data)


def test_export_links_only_canonical_revision_bound_source():
    data = {"requirements": [{"id": "r", "area": "product", "statement": "s",
        "citations": [{"path": "src/a space.py", "revision": "A" * 40,
                       "href": "javascript:alert(1)"}]}]}
    report = render_report(data, source_base="https://github.com/example/project")
    assert 'href="https://github.com/example/project/blob/' + "a" * 40 + '/src/a%20space.py"' in report
    assert "javascript:alert" not in report
    assert "noopener" in report and "noreferrer" in report


@pytest.mark.parametrize("source_base", [
    "https://evil.example/example/project", "http://github.com/example/project",
    "https://user:password@github.com/example/project", "https://github.com:443/example/project",
    "https://github.com/example/project?x=1", "https://github.com/example/project#frag",
    "https://github.com/example/project/extra", "https://github.com/example/../project",
])
def test_untrusted_base_never_makes_source_link(source_base):
    data = {"requirements": [{"id": "r", "area": "product", "statement": "s",
            "citations": [{"path": "src/example.py", "revision": "a" * 40}]}]}
    report = render_report(data, source_base=source_base)
    assert "src/example.py" in report
    assert "<a " not in report


@pytest.mark.parametrize("citation", [
    {"path": "../escape.py", "revision": "a" * 40},
    {"path": "src/example.py", "revision": "main"},
    {"path": "src/example.py", "revision": "a" * 39},
    {"path": "src/example.py", "revision": "a" * 40 + '\" onclick=\"evil()'},
    {"path": 'src/<script>alert(1)</script>', "revision": "a" * 40},
])
def test_invalid_citation_cannot_inject_export_html(citation):
    data = {"requirements": [{"id": "r", "area": "product", "statement": "s",
                              "citations": [citation]}]}
    report = render_report(data, source_base="https://github.com/example/project")
    assert '<script>alert(1)</script>' not in report
    assert 'onclick="evil()' not in report
    assert 'href="javascript:' not in report
    if citation["revision"] != "a" * 40 or citation["path"] == "../escape.py":
        assert "<a " not in report


def test_live_snapshot_uses_trusted_base_and_ignores_supplied_href():
    data = {"requirements": [{"id": "r", "area": "product", "statement": "s",
        "citations": [{"path": "src/a space.py", "revision": "A" * 40,
                       "href": "https://evil.example/trap"}]}]}
    server = create_server(lambda: data, lambda action, args: {"ok": True},
                           source_base="https://github.com/example/project")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        headers = {"Authorization": f"Bearer {server.token}"}
        status, body, _ = request(server, "GET", "/api/snapshot", headers=headers)
        assert status == 200
        citation = json.loads(body)["requirements"][0]["citations"][0]
        assert citation["href"] == (
            "https://github.com/example/project/blob/" + "a" * 40 + "/src/a%20space.py"
        )
        page = request(server, "GET", "/")[1].decode()
        assert "noopener" in page and "noreferrer" in page
        assert "evil.example" not in body.decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(("remote", "linked"), [
    ("git@github.com:example/project.git", True),
    ("ssh://git@github.com/example/project.git", True),
    ("https://github.com/example/project.git", True),
    ("https://token@github.com/example/project.git", False),
    ("https://github.com:443/example/project.git", False),
    ("https://github.com/example/project.git?secret=1", False),
    ("git@evil.example:example/project.git", False),
])
def test_cli_derives_link_only_from_trusted_origin(repo, tmp_path, monkeypatch, remote, linked):
    from test_autonomy import git
    from typer.testing import CliRunner

    from mentat import autonomy_cli
    from mentat.cli import app

    git(repo, "config", "remote.origin.url", remote)

    class StubCoordinator:
        def __init__(self, path, store, agent, worker, github):
            self.store = store
            self.session_id = None

        def run(self, *, resume):
            head = git(repo, "rev-parse", "HEAD")
            self.session_id = self.store.new_session(str(repo), head, head, {}, 1, 100)
            self.store.put_record(self.session_id, "requirement", {
                "id": "r", "area": "product", "statement": "Source", "citations": [
                    {"path": "src.py", "revision": head},
                ],
            })
            return self.store.snapshot(self.session_id)

    monkeypatch.setattr(autonomy_cli, "Coordinator", StubCoordinator)
    result = CliRunner().invoke(app, ["--state", str(tmp_path / "state"), "run",
                                     "--repo", str(repo), "--no-ui"])
    assert result.exit_code == 0, result.output
    from pathlib import Path

    report = Path(json.loads(result.stdout)["report"]).read_text(encoding="utf-8")
    assert ("https://github.com/example/project/blob/" in report) is linked
    assert "token@" not in report and "secret=1" not in report
