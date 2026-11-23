import http.client
import json
import threading
from urllib.parse import urlsplit

import pytest

from mentat.autonomy_ui import create_server, render_report


@pytest.fixture
def viewer():
    actions = []
    data = {
        "revision": 4,
        "session": {"id": "s1", "status": "running", "secret": "private"},
        "requirements": [{"id": "r1", "area": "product", "statement": "<script>bad()</script>"}],
        "events": [{"seq": 2, "summary": "checked", "output": "secret log"}],
    }
    server = create_server(lambda: data, lambda action, args: actions.append((action, args)) or {"ok": True})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, data, actions
    server.shutdown()
    server.server_close()
    thread.join()


def request(server, method, path, *, headers=None, body=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = response.status, response.read(), dict(response.getheaders())
    connection.close()
    return result


def test_capability_host_origin_and_snapshot_projection(viewer, capsys):
    server, _, _ = viewer
    auth = {"Authorization": f"Bearer {server.token}"}
    assert urlsplit(server.url).fragment == server.token
    assert request(server, "GET", "/api/snapshot")[0] == 401
    assert request(server, "GET", "/api/snapshot", headers={**auth, "Host": "evil.example"})[0] == 403
    assert request(server, "GET", "/api/snapshot", headers={**auth, "Origin": "http://evil.example"})[0] == 403
    status, body, headers = request(server, "GET", "/api/snapshot", headers=auth)
    assert status == 200
    assert b"secret" not in body
    assert json.loads(body)["requirements"][0]["id"] == "r1"
    assert headers["Cache-Control"] == "no-store"
    page = request(server, "GET", "/")[1].decode()
    assert server.token not in page
    assert "textContent" in page and "innerHTML" not in page
    assert server.token not in capsys.readouterr().err


def test_control_requires_authorization_origin_and_valid_body(viewer):
    server, _, actions = viewer
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    headers = {"Authorization": f"Bearer {server.token}", "Origin": origin, "Content-Type": "application/json"}
    body = json.dumps({"action": "feedback", "message": "  Check this  "})
    assert request(server, "POST", "/api/control", headers={"Content-Type": "application/json"}, body=body)[0] == 403
    assert request(server, "POST", "/api/control", headers=headers, body=json.dumps({"action": "run"}))[0] == 400
    assert request(server, "POST", "/api/control", headers=headers, body=json.dumps({"action": "stop", "extra": 1}))[0] == 400
    assert not actions
    assert request(server, "POST", "/api/control", headers=headers, body=body)[0] == 200
    assert request(server, "POST", "/api/control", headers=headers, body='{"action":"stop"}')[0] == 200
    assert actions == [("feedback", {"message": "Check this"}), ("stop", {})]
    assert request(server, "PUT", "/api/control", headers=headers)[0] == 501


def test_events_and_report_are_bounded_and_escaped(viewer):
    server, data, _ = viewer
    auth = {"Authorization": f"Bearer {server.token}"}
    assert json.loads(request(server, "GET", "/api/events?after=1", headers=auth)[1])["events"][0]["seq"] == 2
    assert json.loads(request(server, "GET", "/api/events?after=2", headers=auth)[1])["events"] == []
    assert request(server, "GET", "/api/events?after=no", headers=auth)[0] == 400
    report = render_report(data)
    assert "&lt;script&gt;bad()&lt;/script&gt;" in report
    assert "secret log" not in report and "private" not in report
    assert request(server, "GET", "/unknown", headers=auth)[0] == 404


def test_refuses_non_loopback():
    with pytest.raises(ValueError):
        create_server(lambda: {}, lambda action, args: {}, host="0.0.0.0")


def test_invalidated_store_status_is_historical(tmp_path):
    from mentat.autonomy_state import Store

    store = Store(tmp_path / "state.sqlite")
    session = store.new_session(str(tmp_path / "repo"), "base", "head", {}, 1, 1000)
    store.put_record(session, "claim", {"id": "c", "status": "proved"})
    store.put_record(session, "evidence", {"id": "e", "status": "pass"})
    store.invalidate(session, {"head": "changed"})
    snapshot = store.snapshot(session)
    report = render_report(snapshot)
    assert "Session invalidated" in report
    assert "Historical status" in report and "proved" in report
    assert report.count('class="badge warn">Stale') == 2
    server = create_server(lambda: store.snapshot(session), lambda action, args: {})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        headers = {"Authorization": f"Bearer {server.token}"}
        data = json.loads(request(server, "GET", "/api/snapshot", headers=headers)[1])
        assert data["session"]["status"] == "invalidated"
        assert data["claims"][0]["freshness"] == "stale"
        assert data["claims"][0]["status"] == "proved"
        assert data["evidence"][0]["freshness"] == "stale"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_recent_events_are_shown_and_truncation_reported(viewer):
    server, data, _ = viewer
    data["events"] = [{"seq": n, "kind": "updated"} for n in range(1200)]
    data["requirements"] = [{"id": str(n), "area": "product"} for n in range(1200)]
    headers = {"Authorization": f"Bearer {server.token}"}
    result = json.loads(request(server, "GET", "/api/snapshot", headers=headers)[1])
    assert len(result["requirements"]) == 1200
    assert len(result["events"]) == 1000
    assert result["events"][0]["seq"] == 200 and result["events"][-1]["seq"] == 1199
    assert result["event_total"] == 1200 and result["events_truncated"]
    assert "Showing 1000 of 1200 events" in render_report(data)


def test_source_proof_scope_is_present_and_sensitive_fields_excluded(viewer):
    server, data, _ = viewer
    data["claims"] = [{
        "id": "c1", "statement": "Clamp is nonnegative", "status": "established-within-scope",
        "declared_status": "proved", "formal_meaning": "The returned integer is nonnegative",
        "proof_scope": {"method": "source-proof", "revision": "a" * 40,
                        "assumptions": ["Exact integer inputs"], "qualification": "Conditional"},
    }]
    data["evidence"] = [{
        "id": "e1", "claim_id": "c1", "status": "proved", "method": "source-proof",
        "assumptions": ["No callers are proved", "<script>bad()</script>"],
        "source_identity": {"path": "src/clamp.py", "function": "clamp", "revision": "a" * 40,
                            "source_sha256": "b" * 64, "token": "hidden-token"},
        "contract": {"inputs": {"x": "int", "auth_token": "int"}, "requires": "True", "ensures": "result >= 0"},
        "precondition": "sat", "violation": "unsat", "solver_version": "4.16.0",
        "python_version": "3.12.0", "counterexample": {"x": -2, "valid": True, "token": 19, "raw": "secret"},
        "output": "hidden-log", "environment": {"API_KEY": "hidden-env"},
    }]
    headers = {"Authorization": f"Bearer {server.token}"}
    response = request(server, "GET", "/api/snapshot", headers=headers)[1]
    projection = json.loads(response)
    claim, evidence = projection["claims"][0], projection["evidence"][0]
    assert claim["proof_scope"]["method"] == evidence["method"] == "source-proof"
    assert claim["declared_status"] == "proved" and claim["status"] == "established-within-scope"
    assert evidence["source_identity"]["path"] == "src/clamp.py"
    assert evidence["contract"]["inputs"] == {"x": "int"}
    assert evidence["counterexample"] == {"x": -2, "valid": True}
    assert b"hidden-token" not in response and b"hidden-log" not in response and b"hidden-env" not in response
    report = render_report(data)
    assert "source-proof" in report and "No callers are proved" in report
    assert "&lt;script&gt;bad()&lt;/script&gt;" in report
    assert "hidden-token" not in report and "hidden-log" not in report
    page = request(server, "GET", "/")[1].decode()
    assert "Proof scope" in page and "Source identity" in page and "textContent" in page
    data["evidence"][0]["source_identity"]["path"] = "/private/secret"
    assert "private/secret" not in render_report(data)
