"""Real loopback tests for the headless integration contract.

The small projection below isolates HTTP and journal semantics. Production
projection and freshness are exercised separately in test_autonomy_projection.
"""
import http.client
import json
import socket
import stat
from urllib.parse import urlsplit

import pytest

from mentat.autonomy_integration import create_adapter
from mentat.autonomy_state import Store


def projection(raw, *, timeout):
    return {"session": {"id": raw["session"]["id"], "status": raw["session"]["status"]},
            "freshness": {"status": "unknown"}, **{name: list(raw.get(name, [])) for name in
            ("requirements", "claims", "jobs", "findings", "decisions", "evidence")}}


@pytest.fixture
def service(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    store = Store(tmp_path / "state.sqlite")
    session = store.new_session(str(repo), "a" * 40, "a" * 40, {}, 1, 1000)
    path = private / "connection.json"
    adapter = create_adapter(store=store, repo=repo, session_id=lambda: session,
                             project=projection, connection_file=path)
    adapter.start()
    yield adapter, store, session, path
    adapter.close()


def request(adapter, method, path, *, headers=None, body=None):
    conn = http.client.HTTPConnection(*adapter.server.server_address, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    result = response.status, response.read(), dict(response.getheaders())
    conn.close()
    return result


def auth(adapter):
    return {"Authorization": "Bearer " + adapter.token}


def get(adapter, path):
    status, body, _ = request(adapter, "GET", path, headers=auth(adapter))
    return status, json.loads(body)


def control(adapter, key, action, payload):
    headers = {**auth(adapter), "Content-Type": "application/json"}
    return request(adapter, "POST", "/v1/controls", headers=headers,
                   body=json.dumps({"request_id": key, "action": action, "payload": payload}))


def test_private_rendezvous_and_authenticated_headless_routes(service, capsys):
    adapter, _, session, path = service
    info = json.loads(path.read_text())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_nlink == 1
    assert info["session_id"] == session
    assert urlsplit(info["base_url"]).hostname == "127.0.0.1"
    assert adapter.token not in info["base_url"]
    assert request(adapter, "GET", "/v1/capabilities")[0] == 401
    assert request(adapter, "GET", "/v1/capabilities?token=" + adapter.token)[0] == 401
    status, result = get(adapter, "/v1/capabilities")
    assert status == 200 and result["api_version"] == 1
    assert result["limits"]["page_items"] == 100
    assert result["controller"]["availability"] == "unavailable"
    assert result["capabilities"]["raw_artifacts"] is False
    for route in ("/", "/api/snapshot", "/report.html"):
        assert request(adapter, "GET", route, headers=auth(adapter))[0] == 404
    assert adapter.token not in capsys.readouterr().err
    adapter.close()
    assert not path.exists()


def test_cleanup_does_not_unlink_replacement(service):
    adapter, _, _, path = service
    path.unlink()
    path.write_text("replacement")
    adapter.close()
    assert path.read_text() == "replacement"


def test_rejects_insecure_and_existing_rendezvous(service, tmp_path):
    adapter, store, session, path = service
    with pytest.raises((OSError, ValueError)):
        create_adapter(store=store, repo=adapter.repo, session_id=lambda: session,
                       project=projection, connection_file=path).start()
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    with pytest.raises((OSError, ValueError)):
        create_adapter(store=store, repo=adapter.repo, session_id=lambda: session,
                       project=projection, connection_file=unsafe / "connection").start()
    with pytest.raises((OSError, ValueError)):
        create_adapter(store=store, repo=adapter.repo, session_id=lambda: session,
                       project=projection, connection_file=adapter.repo / "connection").start()


def test_origin_host_and_body_validation(service):
    adapter, _, _, _ = service
    for headers in ({"Origin": "null"}, {"Origin": "http://evil.example"}, {"Host": "evil.example"}):
        assert request(adapter, "GET", "/v1/capabilities", headers={**auth(adapter), **headers})[0] == 403
    headers = {**auth(adapter), "Content-Type": "application/json"}
    for body in ('{', '{"request_id":"x","request_id":"y"}',
                 '{"request_id":"x","action":"feedback","payload":{"message":NaN}}',
                 json.dumps({"request_id": "x", "action": "feedback", "payload": {"message": "x" * 9000}}),
                 json.dumps({"request_id": "x", "action": "stop", "payload": {}, "extra": 1})):
        assert request(adapter, "POST", "/v1/controls", headers=headers, body=body)[0] in (400, 413)
    assert request(adapter, "POST", "/v1/controls", headers=headers, body="x" * 17000)[0] == 413
    assert request(adapter, "GET", "/v1/snapshots/x/claims?limit=101", headers=auth(adapter))[0] == 400


def test_duplicate_authorization_and_ambiguous_framing_rejected(service):
    adapter, _, _, _ = service
    host, port = adapter.server.server_address
    for extra in (f"Authorization: Bearer {adapter.token}\r\nAuthorization: Bearer {adapter.token}\r\n",
                  f"Authorization: Bearer {adapter.token}\r\nContent-Length: 0\r\nTransfer-Encoding: chunked\r\n"):
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall((f"GET /v1/capabilities HTTP/1.1\r\nHost: {host}:{port}\r\n" + extra + "\r\n").encode())
            assert b" 400 " in sock.recv(4096).split(b"\r\n", 1)[0]


def test_snapshot_pages_are_stable_and_changes_resume_past_1000(service):
    adapter, store, session, _ = service
    for i in range(3):
        store.put_record(session, "claim", {"id": f"claim-{i}"})
    status, snap = get(adapter, "/v1/snapshot")
    assert status == 200 and snap["counts"]["claims"] == 3
    store.put_record(session, "claim", {"id": "later"})
    url = f"/v1/snapshots/{snap['snapshot_id']}/claims"
    _, first = get(adapter, url + "?limit=2")
    _, second = get(adapter, url + f"?offset={first['next_offset']}&limit=2")
    assert len(first["items"] + second["items"]) == 3
    assert second["next_offset"] is None
    for i in range(1005):
        store.append_event(session, "test_notice", {"private": str(i)})
    cursor = snap["cursor"]
    seqs = []
    while True:
        status, changes = get(adapter, "/v1/changes?limit=100&cursor=" + cursor)
        assert status == 200
        seqs.extend(event["seq"] for event in changes["events"])
        cursor = changes["cursor"]
        if not changes["has_more"]:
            break
    assert len(seqs) >= 1005 and seqs == sorted(set(seqs))
    assert seqs[0] > snap["high_water"]
    assert get(adapter, "/v1/changes?cursor=foreign")[0] == 409
    assert get(adapter, "/v1/snapshots/foreign/claims")[0] == 409


def test_feedback_is_durable_idempotent_and_stop_unavailable(service):
    adapter, store, session, _ = service
    first = control(adapter, "feedback-1", "feedback", {"message": "please clarify"})
    again = control(adapter, "feedback-1", "feedback", {"message": "please clarify"})
    assert first[0] == again[0] == 202
    assert json.loads(first[1])["receipt"] == json.loads(again[1])["receipt"]
    assert control(adapter, "feedback-1", "feedback", {"message": "changed"})[0] == 409
    assert control(adapter, "stop-1", "stop", {})[0] == 409
    reopened = Store(store.path).snapshot(session)
    assert sum(e["kind"] == "human_feedback" for e in reopened["events"]) == 1
    assert b"please clarify" not in first[1]


def test_live_stop_receipt_is_not_completion_or_replayed_on_restart(service):
    old, store, session, path = service
    old.close()
    signals = []
    live = create_adapter(store=store, repo=old.repo, session_id=lambda: session,
                          project=projection, connection_file=path,
                          stop=lambda: signals.append("stop"), is_active=lambda: True)
    live.start()
    try:
        first = control(live, "stop-once", "stop", {})
        assert first[0] == 202
        receipt = json.loads(first[1])["receipt"]
        assert receipt["status"] == "requested"
        assert signals == ["stop"]
        assert control(live, "stop-once", "stop", {})[0] == 202
        assert signals == ["stop"]
    finally:
        live.close()
    restarted = create_adapter(store=store, repo=old.repo, session_id=lambda: session,
                               project=projection, connection_file=path,
                               stop=lambda: signals.append("restart"), is_active=lambda: True)
    restarted.start()
    try:
        retry = control(restarted, "stop-once", "stop", {})
        assert retry[0] == 202 and json.loads(retry[1])["receipt"] == receipt
        assert signals == ["stop"]
    finally:
        restarted.close()


def test_restart_invalidates_old_cursor_and_disconnect_does_not_request_stop(service):
    adapter, store, session, path = service
    _, snapshot = get(adapter, "/v1/snapshot")
    with socket.create_connection(adapter.server.server_address, timeout=5) as sock:
        sock.sendall(b"GET /v1/snapshot HTTP/1.1\r\n")
    assert not any(e["kind"] == "integration_stop_requested" for e in store.snapshot(session)["events"])
    adapter.close()
    restarted = create_adapter(store=store, repo=adapter.repo, session_id=lambda: session,
                               project=projection, connection_file=path)
    restarted.start()
    try:
        status, result = get(restarted, "/v1/changes?cursor=" + snapshot["cursor"])
        assert status == 409 and result["error"]["reset_required"] is True
    finally:
        restarted.close()


def test_snapshot_releases_read_transaction_before_decoding(service, monkeypatch):
    import threading

    from mentat import autonomy_state

    _, store, session, _ = service
    store.append_event(session, "decode_probe", {"marker": "pause-decoding"})
    original_loads = json.loads
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    captured = []
    failures = []

    def delayed_loads(value, *args, **kwargs):
        if isinstance(value, str) and "pause-decoding" in value:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test decoder not released")
        return original_loads(value, *args, **kwargs)

    def capture():
        try:
            captured.append(store.integration_snapshot(session))
        except Exception as exc:
            failures.append(exc)
        finally:
            finished.set()

    def write():
        try:
            store.append_event(session, "concurrent_write", {})
        except Exception as exc:
            failures.append(exc)

    monkeypatch.setattr(autonomy_state.json, "loads", delayed_loads)
    reader = threading.Thread(target=capture)
    writer = threading.Thread(target=write)
    reader.start()
    try:
        assert entered.wait(5)
        writer.start()
        writer.join(3)
        assert not writer.is_alive(), "controller write blocked on snapshot decoding"
    finally:
        release.set()
        reader.join(7)
        if writer.ident is not None:
            writer.join(7)
    assert finished.is_set() and not failures
    assert all(event["kind"] != "concurrent_write" for event in captured[0]["events"])
    assert store.snapshot(session)["revision"] > captured[0]["revision"]


def test_only_explicit_origin_receives_preflight_and_cors(service):
    old, store, session, path = service
    old.close()
    origin = "http://localhost:4321"
    adapter = create_adapter(store=store, repo=old.repo, session_id=lambda: session,
                             project=projection, connection_file=path, allowed_origins=[origin])
    adapter.start()
    try:
        status, _, headers = request(adapter, "OPTIONS", "/v1/capabilities", headers={
            "Origin": origin, "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization"})
        assert status == 200 and headers["Access-Control-Allow-Origin"] == origin
        status, _, headers = request(adapter, "GET", "/v1/capabilities", headers={**auth(adapter), "Origin": origin})
        assert status == 200 and headers["Access-Control-Allow-Origin"] == origin
        assert request(adapter, "GET", "/v1/capabilities", headers={**auth(adapter), "Origin": "http://localhost:4322"})[0] == 403
    finally:
        adapter.close()


def test_expired_snapshot_requires_reacquisition(service, monkeypatch):
    import time

    from mentat import autonomy_integration

    adapter, _, _, _ = service
    _, snapshot = get(adapter, "/v1/snapshot")
    now = time.time()
    monkeypatch.setattr(autonomy_integration.time, "time", lambda: now + 31)
    status, result = get(adapter, f"/v1/snapshots/{snapshot['snapshot_id']}/claims")
    assert status == 409 and result["error"]["reset_required"] is True


def test_shutdown_fences_inflight_feedback_admission(service):
    import time

    adapter, store, session, _ = service
    body = json.dumps({"request_id": "late", "action": "feedback", "payload": {"message": "late"}}).encode()
    host, port = adapter.server.server_address
    with socket.create_connection((host, port), timeout=5) as sock:
        headers = (f"POST /v1/controls HTTP/1.1\r\nHost: {host}:{port}\r\n"
                   f"Authorization: Bearer {adapter.token}\r\nContent-Type: application/json\r\n"
                   f"Content-Length: {len(body)}\r\n\r\n")
        sock.sendall(headers.encode())
        # The request is connected and authorized, but body delivery is delayed.
        time.sleep(0.1)
        adapter.close()
        try:
            sock.sendall(body)
            response = sock.recv(4096)
        except OSError:
            response = b""
    assert b" 202 " not in response.split(b"\r\n", 1)[0]
    assert not any(event["kind"] == "human_feedback" for event in store.snapshot(session)["events"])


def test_slow_clients_are_bounded_and_shutdown_does_not_stop_controller(service):
    import time

    adapter, store, session, _ = service
    clients = []
    try:
        for _ in range(8):
            sock = socket.create_connection(adapter.server.server_address, timeout=3)
            sock.sendall(b"GET /v1/capabilities HTTP/1.1\r\n")
            clients.append(sock)
        time.sleep(0.15)
        with socket.create_connection(adapter.server.server_address, timeout=3) as extra:
            extra.sendall(b"GET /v1/capabilities HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            try:
                rejected = extra.recv(1024)
            except ConnectionResetError:
                rejected = b""
            assert not rejected or b" 429 " in rejected.split(b"\r\n", 1)[0]
        started = time.monotonic()
        adapter.close()
        assert time.monotonic() - started < 4
        assert not any(event["kind"] == "integration_stop_requested" for event in store.snapshot(session)["events"])
    finally:
        for sock in clients:
            sock.close()
