import io
import json
import subprocess
from pathlib import Path

import pytest
from helpers import commit, git
from typer.testing import CliRunner

from mentat import cli, evidence
from mentat.bridge import Bridge, serve


@pytest.fixture
def quickstart(tmp_path):
    repo = tmp_path / "repo"
    script = Path(__file__).resolve().parents[1] / "examples/quickstart/create.sh"
    subprocess.run([str(script), str(repo)], check=True, capture_output=True)
    base = git(repo, "rev-parse", "mentat-demo-base")
    revision = git(repo, "rev-parse", "HEAD")
    state = tmp_path / "state"
    return repo, state, {"repo": str(repo), "base": base, "runner": "host"}, revision


def test_protocol_envelopes_and_unsupported(tmp_path):
    requests = [b'{"id":1,"method":"initialize","params":{"version":1}}\n',
                b'{"id":2,"method":"no.such.method","params":{}}\n',
                b'{"id":3,"id":4,"method":"initialize","params":{}}\n']
    out = io.BytesIO()
    serve(tmp_path / "state", io.BytesIO(b"".join(requests)), out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines[0]["result"]["version"] == 1
    assert "verification.start" in lines[0]["result"]["capabilities"]
    assert lines[1]["error"]["code"] == "unsupported"
    assert lines[2]["error"]["code"] == "invalid_request"
    proc = subprocess.run([str(Path(cli.__file__).parents[2] / ".venv/bin/mentat"),
                           "--state", str(tmp_path / "state"), "bridge", "--stdio"],
                          input=requests[0], capture_output=True, check=True)
    assert json.loads(proc.stdout)["result"]["version"] == 1
    assert proc.stderr == b""


def test_review_scope_consent_replay_and_stale_head(quickstart):
    repo, state, scope, _ = quickstart
    bridge = Bridge(state)
    assert bridge.call("project.inspect", scope)["status"] == "needs-approval"
    with pytest.raises(ValueError, match="consent"):
        bridge.call("approval.prepare", scope)
    reviewed = bridge.call("approval.prepare", {**scope, "hostConsent": True})
    assert reviewed["scope"]["digest"] == bridge.call("specification.get", scope)["digest"]
    assert bridge.call("approval.commit", {"token": reviewed["token"]}) == {
        "approved": True, "source": "cockpit_local_operator"}
    assert bridge.call("project.inspect", scope)["approvedSource"] == "cockpit_local_operator"
    with pytest.raises(ValueError, match="already used"):
        bridge.call("approval.commit", {"token": reviewed["token"]})
    stale = bridge.call("approval.prepare", {**scope, "hostConsent": True})
    (repo / "new").write_text("new")
    commit(repo)
    with pytest.raises(ValueError, match="changed"):
        bridge.call("approval.commit", {"token": stale["token"]})
    with pytest.raises(ValueError, match="already used"):
        bridge.call("approval.commit", {"token": stale["token"]})


def test_real_run_integrity_acceptance_export_and_compare(quickstart, tmp_path):
    repo, state, scope, revision = quickstart
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    args = ["--state", str(state), "verify", "--repo", str(repo), "--base", scope["base"],
            "--runner", "host", "--rev", revision, "--json"]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.output)["run_id"]
    assert bridge.call("runs.list", scope)["runs"][0]["runId"] == run_id
    loaded = bridge.call("runs.get", {"runId": run_id, "revision": revision})
    assert loaded["assurance"]["status"] == "pass"
    assert loaded["assurance"]["method"] and loaded["assurance"]["detail"]
    assert {check["id"] for check in loaded["assurance"]["claims"][0]["checks"]} == {"clamp-model", "clamp-cli"}
    assert loaded["assurance"]["eligible"] and not loaded["assurance"]["accepted"]
    no_revision = bridge.call("runs.get", {"runId": run_id})["assurance"]
    assert no_revision["freshness"] == "unknown" and not no_revision["eligible"]
    stale = bridge.call("runs.get", {"runId": run_id, "revision": scope["base"]})["assurance"]
    assert stale["freshness"] == "historical" and not stale["eligible"]
    for mismatch in ({"revision": scope["base"]}, {"base": revision}, {"runner": "docker"}):
        with pytest.raises(ValueError):
            bridge.call("acceptance.prepare", {**scope, "runId": run_id,
                        "revision": revision, "hostConsent": True, **mismatch})
    params = {**scope, "runId": run_id, "revision": revision, "hostConsent": True}
    stale_review = bridge.call("acceptance.prepare", params)
    (repo / "later-change").write_text("new commit")
    commit(repo)
    with pytest.raises(ValueError, match="changed"):
        bridge.call("acceptance.commit", {"token": stale_review["token"]})
    git(repo, "checkout", "--detach", revision)
    evidence_review = bridge.call("acceptance.prepare", params)
    receipt = state / "receipts" / run_id
    receipt_bytes = receipt.read_bytes()
    receipt.unlink()
    with pytest.raises((ValueError, OSError)):
        bridge.call("acceptance.commit", {"token": evidence_review["token"]})
    receipt.write_bytes(receipt_bytes)
    review = bridge.call("acceptance.prepare", params)
    result = bridge.call("acceptance.commit", {"token": review["token"]})
    assert result["assurance"]["accepted"]
    assert result["assurance"]["acceptanceSource"] == "cockpit_local_operator"
    assert bridge.call("evidence.compare", {"before": run_id, "after": run_id})["status"] == "pass"
    record = loaded["record"]
    ref = {"runId": run_id, "base": scope["base"], "revision": revision,
           "runner": scope["runner"], "contractDigest": record["contract_digest"]}
    projection = bridge.call("evidence.inspect", {"repo": str(repo), **ref})
    assert projection["run"]["manifestDigest"] == (state / "receipts" / run_id).read_text()
    assert projection["claims"][0]["assumptions"] == record["claims"][0].get("assumptions", [])
    assert {check["id"] for check in projection["claims"][0]["checks"]} == {"clamp-model", "clamp-cli"}
    assert str(state) not in json.dumps(projection)
    for key, value in (("base", revision), ("revision", scope["base"]),
                       ("runner", "docker"), ("contractDigest", "0" * 64)):
        with pytest.raises(ValueError, match="pinned"):
            bridge.call("evidence.inspect", {"repo": str(repo), **ref, key: value})
    foreign = tmp_path / "foreign"
    subprocess.run(["git", "init", str(foreign)], check=True, capture_output=True)
    with pytest.raises(ValueError, match="pinned"):
        bridge.call("evidence.inspect", {"repo": str(foreign), **ref})
    comparison = bridge.call("evidence.compareScoped", {"repo": str(repo), "before": ref, "after": ref})
    assert comparison["mode"] == "same-scope" and comparison["scopeDifferences"] == []
    assert str(state) not in json.dumps(comparison)
    assert "detail" not in json.dumps(comparison)
    history = bridge.call("evidence.compareScoped", {"repo": str(repo), "before": ref,
                          "after": ref, "acrossContracts": True})
    assert history["mode"] == "across-contracts"
    assert set(history["inputChanges"].values()) <= {"changed", "unchanged", "unavailable"}
    with pytest.raises(ValueError, match="pinned"):
        bridge.call("evidence.compareScoped", {"repo": str(repo), "before": ref,
                    "after": {**ref, "revision": scope["base"]}})
    out = tmp_path / "export"
    assert bridge.call("evidence.export", {"runId": run_id, "out": str(out)})["path"] == str(out)
    assert (out / "manifest.json").exists()
    manifest = state / "runs" / run_id / "manifest.json"
    assert json.loads(manifest.read_bytes())["bundle_format"] == 2
    assert not (manifest.parent / "report.md").exists()
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(ValueError, match="integrity"):
        bridge.call("evidence.inspect", {"repo": str(repo), **ref})
    with pytest.raises(ValueError, match="integrity"):
        bridge.call("runs.get", {"runId": run_id})
    assert evidence.read_json(state / "acceptances" / f"{run_id}.json")["decision_source"] == "cockpit_local_operator"


def test_decision_rejects_stale_verifier_and_wrong_state(quickstart, monkeypatch, tmp_path):
    from dataclasses import replace

    import mentat.bridge as bridge_module

    _, state, scope, _ = quickstart
    bridge = Bridge(state, fixed_state=True)
    with pytest.raises(ValueError, match="conflicts"):
        bridge.call("approval.prepare", {**scope, "state": str(tmp_path / "other"), "hostConsent": True})
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    real_load = bridge_module.load_project
    monkeypatch.setattr(bridge_module, "load_project", lambda *args: replace(real_load(*args), fingerprint="changed"))
    with pytest.raises(ValueError, match="changed"):
        bridge.call("approval.commit", {"token": token})
    assert not (state / "approvals").exists()


def test_acceptance_refuses_changed_approval(quickstart):
    repo, state, scope, revision = quickstart
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    result = CliRunner().invoke(cli.app, ["--state", str(state), "verify", "--repo", str(repo),
        "--base", scope["base"], "--runner", "host", "--rev", revision, "--json"])
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.output)["run_id"]
    review = bridge.call("acceptance.prepare", {**scope, "revision": revision,
                          "runId": run_id, "hostConsent": True})
    approval = next((state / "approvals").glob("*.json"))
    record = evidence.read_json(approval)
    record["approved_at"] = "changed"
    evidence.write_record(approval, evidence.json_bytes(record))
    with pytest.raises(ValueError, match="changed"):
        bridge.call("acceptance.commit", {"token": review["token"]})
    assert not (state / "acceptances" / f"{run_id}.json").exists()
    assurance = bridge.call("runs.get", {"runId": run_id, "revision": revision})["assurance"]
    assert assurance["freshness"] == "historical" and not assurance["eligible"]


def test_acceptance_of_pinned_candidate_when_canonical_head_is_base(quickstart):
    repo, state, scope, revision = quickstart
    git(repo, "checkout", "--detach", scope["base"])
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    result = CliRunner().invoke(cli.app, ["--state", str(state), "verify", "--repo", str(repo),
        "--base", scope["base"], "--runner", "host", "--rev", revision, "--json"])
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.output)["run_id"]
    review = bridge.call("acceptance.prepare", {**scope, "revision": revision,
                          "runId": run_id, "hostConsent": True})
    assert bridge.call("acceptance.commit", {"token": review["token"]})["accepted"]


def assert_contains(actual, expected):
    for key, value in expected.items():
        if isinstance(value, dict):
            assert_contains(actual[key], value)
        else:
            assert actual[key] == value


def wait_operation(bridge, operation_id):
    import time

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = bridge.call("operation.get", {"operationId": operation_id})
        if result["status"] not in {"running", "cancel_requested"}:
            return result
        time.sleep(0.02)
    pytest.fail("operation did not finish")


def test_async_real_verification_and_idempotence(quickstart):
    repo, state, scope, revision = quickstart
    notifications = []
    bridge = Bridge(state, notify=notifications.append)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    args = {**scope, "revision": revision, "operationId": "real-job", "hostConsent": True}
    assert bridge.call("verification.start", args) == {"operationId": "real-job"}
    assert bridge.call("verification.start", args) == {"operationId": "real-job"}
    result = wait_operation(bridge, "real-job")
    assert result["status"] == "completed"
    assert result["assurance"]["status"] == "pass"
    fixture = json.loads((Path(__file__).parent / "fixtures/cockpit-bridge-v1.json").read_text())
    example = fixture["examples"]["completedPass"]["params"]
    assert_contains({**result, "operationId": "stdio-job"}, example)
    assert result["runId"] == result["assurance"]["runId"]
    assert len(bridge.call("runs.list", scope)["runs"]) == 1
    assert bridge.call("verification.start", args) == {"operationId": "real-job"}
    git(repo, "checkout", "--detach", scope["base"])
    with pytest.raises(ValueError, match="different scope"):
        bridge.call("verification.start", {**args, "revision": scope["base"]})
    assert notifications[-1]["status"] == "completed"
    bad_args = {**args, "revision": scope["base"], "operationId": "violation-job"}
    bridge.call("verification.start", bad_args)
    failed_check = wait_operation(bridge, "violation-job")
    assert failed_check["status"] == "completed"
    assert failed_check["assurance"]["status"] == "fail"
    assert failed_check["assurance"]["freshness"] == "current"
    assert_contains(failed_check, fixture["examples"]["completedFail"]["params"])
    bridge.close()


def test_cancel_waits_for_unstoppable_worker_and_does_not_replay(quickstart, monkeypatch):
    import threading

    from mentat import bridge as module

    _, state, scope, revision = quickstart
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocking_verify(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(10)
        raise ValueError("worker eventually returned")

    monkeypatch.setattr(module.verification, "verify", blocking_verify)
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    args = {**scope, "revision": revision, "operationId": "cancel-job", "hostConsent": True}
    bridge.call("verification.start", args)
    assert entered.wait(5)
    assert bridge.call("verification.cancel", {"operationId": "cancel-job"})["status"] == "cancel_requested"
    assert bridge.call("operation.get", {"operationId": "cancel-job"})["status"] == "cancel_requested"
    assert Bridge(state).call("operation.get", {"operationId": "cancel-job"})["status"] == "cancel_requested"
    release.set()
    result = wait_operation(bridge, "cancel-job")
    assert result["status"] == "incomplete" and "runId" not in result
    assert bridge.call("verification.start", args) == {"operationId": "cancel-job"}
    assert len(calls) == 1
    bridge.close()


def test_recovery_marks_orphan_interrupted_without_replay(quickstart, monkeypatch):
    import threading

    from mentat import bridge as module

    _, state, scope, revision = quickstart
    entered, release = threading.Event(), threading.Event()

    def blocking_verify(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        raise ValueError("worker finished")

    monkeypatch.setattr(module.verification, "verify", blocking_verify)
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    args = {**scope, "revision": revision, "operationId": "orphan-job", "hostConsent": True}
    bridge.call("verification.start", args)
    assert entered.wait(5)
    bridge.close()
    assert Bridge(state).call("operation.get", {"operationId": "orphan-job"})["status"] == "incomplete"
    release.set()
    assert Bridge(state).call("verification.start", args) == {"operationId": "orphan-job"}
    assert Bridge(state).call("operation.get", {"operationId": "orphan-job"})["status"] == "incomplete"


def test_stdio_verification_acknowledged_before_notification(quickstart):
    import select

    repo, state, scope, revision = quickstart
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    executable = Path(cli.__file__).parents[2] / ".venv/bin/mentat"
    proc = subprocess.Popen([str(executable), "--state", str(state), "bridge", "--stdio"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            bufsize=0)
    try:
        request = {"id": 17, "method": "verification.start", "params": {
            **scope, "revision": revision, "operationId": "stdio-job", "hostConsent": True}}
        proc.stdin.write((json.dumps(request) + "\n").encode())
        proc.stdin.flush()
        assert select.select([proc.stdout], [], [], 15)[0], "missing start response"
        first = json.loads(proc.stdout.readline())
        assert select.select([proc.stdout], [], [], 15)[0], "missing operation notification"
        second = json.loads(proc.stdout.readline())
        fixture = json.loads((Path(__file__).parent / "fixtures/cockpit-bridge-v1.json").read_text())
        assert first == fixture["examples"]["startResponse"]
        assert second["method"] == "operation.updated"
        assert second["params"]["status"] == "completed"
        assert second["params"]["assurance"]["status"] == "pass"
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=10) == 0
        assert proc.stderr.read() == b""


def test_worker_launch_failure_reserves_id_and_close_is_safe(quickstart, monkeypatch):
    import threading

    _, state, scope, revision = quickstart
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    args = {**scope, "revision": revision, "operationId": "launch-failed", "hostConsent": True}

    def fail_start(self):
        raise RuntimeError("no thread")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="no thread"):
        bridge.call("verification.start", args)
    assert bridge.call("operation.get", {"operationId": "launch-failed"})["status"] == "failed"
    assert bridge.call("verification.start", args) == {"operationId": "launch-failed"}
    bridge.close()


def test_approval_review_contains_all_exact_committed_files(quickstart):
    repo, state, scope, _ = quickstart
    contract = json.loads(git(repo, "show", f"{scope['base']}:.mentat/contract.json"))
    contract["base_files"] = ["review-input.txt"]
    (repo / "review-input.txt").write_text("review this base file\n")
    (repo / ".mentat/contract.json").write_text(json.dumps(contract))
    base = commit(repo)
    scope = {**scope, "base": base}
    bridge = Bridge(state)
    review = bridge.call("approval.prepare", {**scope, "hostConsent": True})
    artifacts = {item["path"]: item["content"] for item in review["artifacts"]}
    assert artifacts[".mentat/contract.json"] == git(repo, "show", f"{base}:.mentat/contract.json")
    assert artifacts[".mentat/check.sh"] == git(repo, "show", f"{base}:.mentat/check.sh") + "\n"
    assert artifacts["review-input.txt"] == "review this base file\n"
    summary = json.loads(artifacts["Execution summary (derived)"])
    assert summary["runner"] == "host"
    assert summary["claims_and_checks"][0]["behavior"][0]["effective_argv"]


@pytest.mark.parametrize("contents,reason", [(b"\xff", "UTF-8"), (b"\x00", "visible"), (b"a" * 910_000, "size limit")])
def test_approval_refuses_unreviewable_contract_contents(quickstart, contents, reason):
    repo, state, scope, _ = quickstart
    (repo / ".mentat/extra.txt").write_bytes(contents)
    scope = {**scope, "base": commit(repo)}
    bridge = Bridge(state)
    with pytest.raises(ValueError, match=reason):
        bridge.call("approval.prepare", {**scope, "hostConsent": True})
    assert not bridge.tokens


def test_abandoned_reservation_is_interrupted_and_never_replayed(quickstart, monkeypatch):
    from mentat import bridge as module

    _, state, scope, revision = quickstart
    bridge = Bridge(state)
    state.mkdir(mode=0o700)
    reservation = state / "bridge-operations" / "reserved"
    reservation.mkdir(parents=True)
    fixture = json.loads((Path(__file__).parent / "fixtures/cockpit-bridge-v1.json").read_text())
    snapshot = bridge.call("operation.get", {"operationId": "reserved"})
    assert_contains({**snapshot, "operationId": "orphan-job"}, fixture["examples"]["interrupted"])
    monkeypatch.setattr(module.verification, "verify", lambda *a, **k: pytest.fail("must not replay"))
    with pytest.raises(ValueError, match="unknown"):
        bridge.call("verification.start", {**scope, "revision": revision,
                    "operationId": "reserved", "hostConsent": True})
    assert bridge.call("operation.get", {"operationId": "reserved"})["status"] == "interrupted"


def test_live_reservation_creator_is_not_replayed(quickstart, monkeypatch):
    import threading

    from mentat import bridge as module

    _, state, scope, revision = quickstart
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    reservation = state / "bridge-operations" / "paused"
    entered, release = threading.Event(), threading.Event()
    original_mkdir = Path.mkdir
    calls, replies = [], []
    args = {**scope, "revision": revision, "operationId": "paused", "hostConsent": True}

    def pause_after_mkdir(path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        if path == reservation and not kwargs.get("exist_ok", False):
            entered.set()
            assert release.wait(10)
        return result

    def verify(*args, **kwargs):
        calls.append(True)
        raise ValueError("test worker failed")

    monkeypatch.setattr(Path, "mkdir", pause_after_mkdir)
    monkeypatch.setattr(module.verification, "verify", verify)
    creator = threading.Thread(target=lambda: replies.append(bridge.call("verification.start", args)))
    creator.start()
    assert entered.wait(5)
    other = Bridge(state)
    reader = threading.Thread(target=lambda: replies.append(other.call("verification.start", args)))
    reader.start()
    release.set()
    creator.join(timeout=10)
    reader.join(timeout=10)
    assert not creator.is_alive() and not reader.is_alive()
    assert replies == [{"operationId": "paused"}, {"operationId": "paused"}]
    assert wait_operation(bridge, "paused")["status"] == "failed"
    assert len(calls) == 1
    bridge.close()
    other.close()


def test_review_keeps_mentat_base_directory_path(quickstart):
    repo, state, scope, _ = quickstart
    (repo / ".mentat/base").mkdir()
    (repo / ".mentat/base/notes.txt").write_text("committed notes\n")
    scope = {**scope, "base": commit(repo)}
    review = Bridge(state).call("approval.prepare", {**scope, "hostConsent": True})
    artifacts = {item["path"]: item["content"] for item in review["artifacts"]}
    assert artifacts[".mentat/base/notes.txt"] == "committed notes\n"


def test_freshness_tracks_verifier_scope_and_preserves_historical_acceptance(quickstart, monkeypatch):
    from dataclasses import replace

    from mentat import bridge as module

    repo, state, scope, revision = quickstart
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    result = CliRunner().invoke(cli.app, ["--state", str(state), "verify", "--repo", str(repo),
        "--base", scope["base"], "--runner", "host", "--rev", revision, "--json"])
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.output)["run_id"]
    review = bridge.call("acceptance.prepare", {**scope, "revision": revision,
                          "runId": run_id, "hostConsent": True})
    bridge.call("acceptance.commit", {"token": review["token"]})
    current = bridge.call("runs.get", {"runId": run_id, "revision": revision})["assurance"]
    assert current["freshness"] == "current" and current["accepted"] and current["eligible"]
    real_load = module.load_project
    monkeypatch.setattr(module, "load_project", lambda *a: replace(real_load(*a), fingerprint="changed"))
    historical = bridge.call("runs.get", {"runId": run_id, "revision": revision})["assurance"]
    assert historical["freshness"] == "historical"
    assert historical["accepted"] and not historical["eligible"]
    assert historical["acceptanceSource"] == "cockpit_local_operator"


def test_wire_fixture_and_invalid_frames_do_not_dispatch(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/cockpit-bridge-v1.json").read_text())
    requests = [fixture["initializeRequest"],
                {"id": 2, "method": "initialize", "params": {"version": 2}},
                {"id": 3, "method": "initialize", "params": {"version": True}}]
    raw = b"".join(json.dumps(request).encode() + b"\n" for request in requests)
    raw += b'{"id":4,"id":5,"method":"initialize","params":{"version":1}}\n'
    raw += b'{"id":6,"method":"initialize","params":{"version":1}}\n'
    output = io.BytesIO()
    serve(tmp_path / "state", io.BytesIO(raw), output)
    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert lines[0]["id"] == fixture["initializeRequest"]["id"]
    assert lines[0]["result"]["version"] == 1
    assert set(lines[0]["result"]["capabilities"]) == set(fixture["methods"])
    assert isinstance(lines[0]["result"]["instanceId"], str)
    assert all(line["error"]["code"] == "invalid_request" for line in lines[1:4])
    assert lines[4]["id"] == 6 and lines[4]["result"]["version"] == 1


def test_decisions_expire_are_instance_scoped_and_consumed(quickstart, monkeypatch):
    from mentat import bridge as module

    _, state, scope, _ = quickstart
    bridge = Bridge(state)
    review = bridge.call("approval.prepare", {**scope, "hostConsent": True})
    token = review["token"]
    with pytest.raises(ValueError, match="invalid|expired|used"):
        Bridge(state).call("approval.commit", {"token": token})
    deadline = bridge.tokens[token][0]
    monkeypatch.setattr(module.time, "monotonic", lambda: deadline)
    for _ in range(2):
        with pytest.raises(ValueError, match="invalid|expired|used"):
            bridge.call("approval.commit", {"token": token})
    assert not (state / "approvals").exists()


def test_legacy_checked_evidence_loads_and_detects_tampering(tmp_path):
    import os
    import shutil

    source = Path(__file__).parent / "fixtures/records/v0.1.2/state"
    state = tmp_path / "state"
    shutil.copytree(source, state)
    os.chmod(state, 0o700)
    run_id = "70a7486a406a41a18746115299ac3c64"
    bridge = Bridge(state)
    loaded = bridge.call("runs.get", {"runId": run_id})
    assert loaded["assurance"]["status"] == "pass"
    assert loaded["assurance"]["freshness"] == "unknown"
    assert not loaded["assurance"]["eligible"]
    assert "bundle_format" not in loaded["record"]
    out = tmp_path / "legacy-export"
    bridge.call("evidence.export", {"runId": run_id, "out": str(out)})
    assert (out / "report.md").is_file()
    (state / "runs" / run_id / "report.md").write_text("tampered")
    with pytest.raises(ValueError):
        bridge.call("runs.get", {"runId": run_id})


def test_completed_operation_revalidates_assurance_after_restart(quickstart):
    _, state, scope, revision = quickstart
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    params = {**scope, "revision": revision, "operationId": "durable", "hostConsent": True}
    bridge.call("verification.start", params)
    finished = wait_operation(bridge, "durable")
    assert finished["status"] == "completed"
    assert finished["assurance"]["freshness"] == "current"
    assert finished["assurance"]["eligible"]
    bridge.close()

    restarted = Bridge(state)
    current = restarted.call("operation.get", {"operationId": "durable"})
    assert current["runId"] == finished["runId"]
    assert current["assurance"]["freshness"] == "current"
    assert current["assurance"]["eligible"]

    approval = next((state / "approvals").glob("*.json"))
    original_approval = approval.read_bytes()
    record = evidence.read_json(approval)
    record["approved_at"] = "changed"
    evidence.write_record(approval, evidence.json_bytes(record))
    changed = restarted.call("operation.get", {"operationId": "durable"})
    assert changed["status"] == "completed"
    assert changed["assurance"]["freshness"] != "current"
    assert not changed["assurance"]["eligible"]

    evidence.write_record(approval, original_approval)
    restored = restarted.call("operation.get", {"operationId": "durable"})
    assert restored["assurance"]["freshness"] == "current"
    assert restored["assurance"]["eligible"]
    manifest = state / "runs" / finished["runId"] / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    try:
        tampered = restarted.call("operation.get", {"operationId": "durable"})
    except (ValueError, OSError):
        pass  # Rejecting an unverifiable record also preserves the invariant.
    else:
        assert not tampered.get("assurance", {}).get("eligible", False)
        assert tampered.get("assurance", {}).get("freshness") != "current"
    restarted.close()


def test_runs_list_skips_non_project_records_in_shared_state(quickstart):
    repo, state, scope, revision = quickstart
    bridge = Bridge(state)
    token = bridge.call("approval.prepare", {**scope, "hostConsent": True})["token"]
    bridge.call("approval.commit", {"token": token})
    result = CliRunner().invoke(cli.app, ["--state", str(state), "verify", "--repo", str(repo),
        "--base", scope["base"], "--runner", "host", "--rev", revision, "--json"])
    assert result.exit_code == 0, result.output
    project_run = json.loads(result.output)["run_id"]
    for run_id, kind in (("a" * 32, "verification"), ("b" * 32, "evaluation")):
        evidence.save_run(state, {"run_id": run_id, "kind": kind, "status": "pass"}, {})
    for params in ({}, scope):
        runs = bridge.call("runs.list", params)["runs"]
        assert {run["runId"] for run in runs} == {project_run}
        assert runs[0]["revision"] == revision


def test_evidence_projection_historical_scope_and_legacy_bundle(quickstart):
    from test_evidence_comparison import save

    repo, state, scope, revision = quickstart
    state = evidence.state_directory(state)
    first, second = "a" * 32, "b" * 32
    old = save(state, first, repo=str(repo), revision=revision, base=scope["base"],
               claims=[{"id": "claim", "statement": "Example behavior", "assumptions": ["flat condition"]}])
    save(state, second, repo=str(repo), revision=revision, base="f" * 40, contract_digest="e" * 64)
    # Legacy bundles retained a report artifact and lacked the format marker.
    record = json.loads((old / "manifest.json").read_bytes())
    record.pop("bundle_format")
    record["claims"][0]["provenance"] = {"origin": "declared", "citations": [{
        "commit": revision, "blob": "d" * 40, "path": "src/example.py", "start_line": 1, "end_line": 2}]}
    record["claims"][0]["assurance"] = {"bounds": [{"id": "condition", "statement": "Input is valid",
        "kind": "assumed", "assumption": "flat condition"}]}
    record["model_checks"][0]["artifact_omissions"] = ["missing-proof.txt"]
    record["model_checks"][0]["log_omissions"] = ["stderr"]
    report = b"historical report"
    (old / "report.md").write_bytes(report)
    record["artifacts"]["report.md"] = evidence.digest(report)
    raw = evidence.json_bytes(record)
    (old / "manifest.json").write_bytes(raw)
    (state / "receipts" / first).write_text(evidence.digest(raw))
    before = {"runId": first, "base": scope["base"], "revision": revision,
              "runner": "host", "contractDigest": "c" * 64}
    after = {**before, "runId": second, "base": "f" * 40, "contractDigest": "e" * 64}
    bridge = Bridge(state)
    result = bridge.call("evidence.inspect", {"repo": str(repo), **before})
    claim = result["claims"][0]
    assert claim["assumptions"] == ["flat condition"]
    assert claim["assurance"]["bounds"][0]["assumption"] == "flat condition"
    assert claim["locations"][0]["path"] == "src/example.py"
    formal = next(check for check in claim["checks"] if check["method"] == "formal")
    assert formal["engine"] == "z3" and formal["artifactOmissions"] == ["missing-proof.txt"]
    assert formal["logOmissions"] == ["stderr"]
    behavior = next(check for check in claim["checks"] if check["method"] == "behavior")
    assert behavior["artifacts"]["witness.json"] == {
        "name": "example.witness.json", "sha256": evidence.digest(b"{}")}
    old_claim = bridge.call("evidence.inspect", {"repo": str(repo), **after})["claims"][0]
    assert old_claim["assumptions"] == [] and "locations" not in old_claim
    assert "assurance" not in old_claim
    params = {"repo": str(repo), "before": before, "after": after}
    with pytest.raises(ValueError, match="matching scope"):
        bridge.call("evidence.compareScoped", params)
    result = bridge.call("evidence.compareScoped", {**params, "acrossContracts": True})
    assert result["scopeDifferences"] == ["base", "contract_digest"]
    assert result["claims"][0]["definition"] == "unavailable"
    assert "unavailable" in result["inputChanges"].values()
    assert str(state) not in json.dumps(result)
