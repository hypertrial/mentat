"""Public projection uses real Git observations and excludes private state."""
import json
import subprocess

from mentat.autonomy_projection import project
from mentat.autonomy_state import Store


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def test_projection_fails_closed_without_executing_repository_helpers(tmp_path):
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
    store.put_record(session, "claim", {"id": "claim", "statement": "honest project prose",
                     "status": "unattempted", "prompt": "PRIVATE_PROMPT_SENTINEL"})
    store.append_event(session, "human_feedback", {"message": "PRIVATE_FEEDBACK_SENTINEL"})
    marker = tmp_path / "helper-executed"
    git(repo, "config", "core.fsmonitor", f"touch {marker}")
    snapshot = project(store.integration_snapshot(session), store_path=store.path)
    encoded = json.dumps(snapshot)
    assert not marker.exists()
    assert snapshot["freshness"]["status"] == "unknown"
    assert snapshot["claims"][0]["freshness"] == "stale"
    assert "honest project prose" in encoded
    assert "PRIVATE_PROMPT_SENTINEL" not in encoded
    assert "PRIVATE_FEEDBACK_SENTINEL" not in encoded
    assert str(tmp_path) not in encoded


def test_real_http_snapshot_uses_production_projection(tmp_path):
    import http.client
    from urllib.parse import urlsplit

    from mentat.autonomy_integration import create_adapter

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
    store.put_record(session, "claim", {"id": "claim", "statement": "Public project statement",
                     "status": "unattempted", "prompt": "PRIVATE_PROMPT_SENTINEL"})
    store.append_event(session, "inventory", {"surfaces": [
        {"path": "source.py", "category": "application", "status": "unmapped", "revision": head}
    ], "count": 1, "status": "unexamined"})
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    adapter = create_adapter(store=store, repo=repo, session_id=lambda: session,
                             project=lambda raw, timeout: project(raw, store_path=store.path, timeout=timeout),
                             connection_file=private / "connection.json")
    adapter.start()
    try:
        info = json.loads((private / "connection.json").read_text())
        endpoint = urlsplit(info["base_url"])
        assert endpoint.scheme == "http" and endpoint.hostname == "127.0.0.1"
        assert info["session_id"] == session

        def request(method, path, *, body=None, status=200):
            conn = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=10)
            headers = {"Authorization": "Bearer " + info["capability"]}
            if body is not None:
                headers["Content-Type"] = "application/json"
                body = json.dumps(body)
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            data = response.read()
            assert response.status == status, data
            conn.close()
            return json.loads(data)

        capabilities = request("GET", "/v1/capabilities")
        assert capabilities["api_version"] == 1
        assert capabilities["session_id"] == info["session_id"]
        assert capabilities["server_id"] == info["server_id"]
        assert capabilities["capabilities"]["snapshots"] is True
        assert capabilities["capabilities"]["changes"] is True
        snap = request("GET", "/v1/snapshot")
        assert snap["session_id"] == session
        assert snap["counts"]["inventory"] == 1
        inventory = request("GET", f"/v1/snapshots/{snap['snapshot_id']}/inventory")
        assert inventory["items"] == [{"path": "source.py", "status": "unmapped",
                                       "reason": "Initial inventory; mapping has not been assessed."}]
        claim_url = f"/v1/snapshots/{snap['snapshot_id']}/claims"
        page = request("GET", claim_url)
        store.put_record(session, "claim", {"id": "later", "statement": "Later statement",
                         "status": "unattempted"})
        assert request("GET", claim_url) == page
        assert page["items"][0]["statement"] == "Public project statement"
        assert page["items"][0]["status"] == "unattempted"
        assert len(page["items"]) == 1
        receipt = request("POST", "/v1/controls", status=202,
                          body={"request_id": "smoke-feedback", "action": "feedback",
                                "payload": {"message": "PRIVATE_FEEDBACK_SENTINEL"}})
        assert receipt["receipt"]
        changes = request("GET", "/v1/changes?cursor=" + snap["cursor"])
        seqs = [event["seq"] for event in changes["events"]]
        assert len(seqs) >= 2 and seqs == sorted(set(seqs))
        assert seqs[0] > snap["high_water"]
        assert any(event["kind"] == "human_feedback" for event in changes["events"])
        public = json.dumps([capabilities, snap, page, receipt, changes])
        assert "PRIVATE_PROMPT_SENTINEL" not in public
        assert "PRIVATE_FEEDBACK_SENTINEL" not in public
        assert str(tmp_path) not in public
    finally:
        adapter.close()


def test_projection_deadline_never_returns_success():
    import time

    import pytest

    started = time.monotonic()
    with pytest.raises((ValueError, TimeoutError)):
        project({"session": {"repo": "/missing"}}, timeout=0.000001)
    assert time.monotonic() - started < 3


def test_projection_supervisor_kills_timed_out_child_process_group(tmp_path):
    import sys
    import time

    import pytest

    from mentat import process

    marker = tmp_path / "descendant-survived"
    descendant = f"import pathlib,time; time.sleep(1); pathlib.Path({str(marker)!r}).write_text('alive')"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{descendant!r}]); time.sleep(20)"
    with pytest.raises((TimeoutError, subprocess.TimeoutExpired)):
        process.run([sys.executable, "-c", parent], output_limit=4096, timeout=0.3)
    time.sleep(1.2)
    assert not marker.exists()


def test_inventory_projection_preserves_unmapped_surfaces(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    session = store.new_session(str(tmp_path / "missing"), "a" * 40, "a" * 40, {}, 1, 1000)
    store.append_event(session, "inventory", {"surfaces": [
        {"path": "src/app.py", "category": "application", "status": "unmapped", "revision": "a" * 40},
        {"path": "tests/test_app.py", "category": "verification-infrastructure", "status": "unmapped", "revision": "a" * 40},
    ], "count": 2, "status": "unexamined"})
    result = project(store.integration_snapshot(session), store_path=store.path)
    assert result["inventory"] == [
        {"path": "src/app.py", "status": "unmapped", "reason": "Initial inventory; mapping has not been assessed."},
        {"path": "tests/test_app.py", "status": "unmapped", "reason": "Initial inventory; mapping has not been assessed."},
    ]


def test_inventory_mapping_requires_current_reviewed_requirement_at_base(tmp_path, monkeypatch):
    from mentat.autonomy import digest
    from mentat.autonomy_projection import _project_inner

    head = "a" * 40
    store = Store(tmp_path / "state.sqlite")
    session = store.new_session(str(tmp_path / "repo"), head, head, {}, 1, 1000)
    store.append_event(session, "authority_pinned", {"digest": "policy"})
    store.append_event(session, "toolchain_pinned", {"identity": {}})
    store.append_event(session, "inventory", {"surfaces": [
        {"path": "app.py", "status": "unmapped"}, {"path": "other.py", "status": "unmapped"}
    ], "count": 2, "status": "unexamined"})
    requirement = {"id": "req", "area": "functional", "origin": "observed", "statement": "Behavior",
                   "citations": [{"path": "app.py", "revision": head}]}
    store.put_record(session, "requirement", requirement)
    store.append_event(session, "fas_reviewed", {"head": "b" * 40, "claims": {},
                                                  "requirements": {"req": digest(requirement)}})
    monkeypatch.setattr("mentat.autonomy_projection._check_git_config", lambda *_: None)
    monkeypatch.setattr("mentat.autonomy_projection._git", lambda *_: "")
    monkeypatch.setattr("mentat.autonomy_projection._observe", lambda *_: (head, False, "policy", {}))
    raw = store.integration_snapshot(session)
    assert [item["status"] for item in _project_inner(raw)["inventory"]] == ["mapped", "unmapped"]
    raw["requirements"][0]["statement"] = "Changed after review"
    assert [item["status"] for item in _project_inner(raw)["inventory"]] == ["unmapped", "unmapped"]
