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
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    adapter = create_adapter(store=store, repo=repo, session_id=lambda: session,
                             project=lambda raw, timeout: project(raw, store_path=store.path, timeout=timeout),
                             connection_file=private / "connection.json")
    adapter.start()
    try:
        def get(path):
            conn = http.client.HTTPConnection(*adapter.server.server_address, timeout=10)
            conn.request("GET", path, headers={"Authorization": "Bearer " + adapter.token})
            response = conn.getresponse()
            body = response.read()
            assert response.status == 200, body
            conn.close()
            return json.loads(body)
        snap = get("/v1/snapshot")
        page = get(f"/v1/snapshots/{snap['snapshot_id']}/claims")
        assert page["items"][0]["statement"] == "Public project statement"
        assert page["items"][0]["status"] == "unattempted"
        assert "PRIVATE_PROMPT_SENTINEL" not in json.dumps(page)
        assert str(tmp_path) not in json.dumps(snap) + json.dumps(page)
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
