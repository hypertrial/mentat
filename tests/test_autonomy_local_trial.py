import json
import subprocess

import pytest

from mentat.autonomy import Coordinator
from mentat.autonomy_state import Store


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class Adapter:
    def cancel_all(self):
        pass

    def diagnostics(self):
        return {"status": "ready"}


def setup_trial(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "app.py").write_text("print('hello')\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    store = Store(tmp_path / "state" / "state.db")
    identity = {"base": base, "request_id": "request-1", "repair_paths": ()}
    policy = {"schema_version": 2, "repo": str(repo.resolve()),
              "mandatory_local_checks": [], "mandatory_worker_checks": [],
              "mandatory_source_checks": [], "model": "gpt-6.1-terra",
              "reasoning_effort": "high", "auto_merge": False,
              "session_seconds": 3600, "concurrency": 3, "max_attempts": 3,
              "local_trial": {**identity, "repair_paths": []}}
    (store.path.parent / "policy.json").write_text(json.dumps(policy))
    monkeypatch.setattr("mentat.autonomy.current_tool_identity", lambda: {})
    calls = []
    def execute(job, deadline):
        calls.append(job["id"])
        return {"status": "failed", "error": "test failure"}
    coordinator = Coordinator(repo, store, Adapter(), Adapter(), Adapter(), local_trial=identity)
    monkeypatch.setattr(coordinator, "_execute", execute)
    return repo, store, policy, identity, coordinator, calls


def test_source_only_no_remote_and_terminal_request_is_not_replayed(tmp_path, monkeypatch):
    repo, store, _, _, coordinator, calls = setup_trial(tmp_path, monkeypatch)
    first = coordinator.run()
    assert first["session"]["base"] == git(repo, "rev-parse", "HEAD")
    assert first["session"]["status"] == "blocked"
    assert len(calls) == 3
    before = store.snapshot()
    second = coordinator.run()
    assert second["session"]["id"] == first["session"]["id"]
    assert store.snapshot() == before
    assert len(calls) == 3
    assert not (repo / ".mentat").exists()


def test_trial_rejects_mismatch_and_ordinary_policy_downgrade(tmp_path, monkeypatch):
    repo, store, policy, identity, coordinator, _ = setup_trial(tmp_path, monkeypatch)
    coordinator.run()
    wrong = Coordinator(repo, store, Adapter(), Adapter(), Adapter(), local_trial={**identity, "request_id": "other"})
    with pytest.raises(ValueError, match="authority"):
        wrong.run()
    policy.pop("local_trial")
    (store.path.parent / "policy.json").write_text(json.dumps(policy))
    ordinary = Coordinator(repo, store, Adapter(), Adapter(), Adapter())
    with pytest.raises(ValueError, match="local trial"):
        ordinary.run()


def test_publication_stops_before_guard_or_gateway(tmp_path, monkeypatch):
    _, _, _, _, coordinator, _ = setup_trial(tmp_path, monkeypatch)
    monkeypatch.setattr(coordinator, "_publication_guard", lambda *_: pytest.fail("publication guard invoked"))
    coordinator._publish({}, {})


def test_trial_rejects_dirty_or_changed_checkout(tmp_path, monkeypatch):
    repo, _, _, _, coordinator, _ = setup_trial(tmp_path, monkeypatch)
    (repo / "app.py").write_text("dirty\n")
    with pytest.raises(ValueError, match="working-tree"):
        coordinator.run()
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "different")
    with pytest.raises(ValueError, match="exact clean"):
        coordinator.run()


def test_blocked_feedback_resumes_with_original_deadline(tmp_path, monkeypatch):
    _, store, _, _, coordinator, calls = setup_trial(tmp_path, monkeypatch)
    first = coordinator.run()
    deadline = first["session"]["deadline"]
    store.integration_control(first["session"]["id"], "feedback-1", "feedback",
                              {"message": "Please clarify the scope."}, None, active=False)
    resumed = coordinator.run()
    assert resumed["session"]["id"] == first["session"]["id"]
    assert resumed["session"]["deadline"] == deadline
    assert len(calls) == 6
    assert not store.pending_feedback(first["session"]["id"])
    assert any(event["kind"] == "feedback_consumed" for event in store.snapshot()["events"])


def test_trial_policy_creator_is_atomic_and_binds_request(tmp_path, monkeypatch):
    from mentat.autonomy_policy import pin_local_trial_policy
    from mentat.autonomy_worker import packaged_profile_identity, profile_identity

    repo, store, _, identity, coordinator, _ = setup_trial(tmp_path, monkeypatch)
    (store.path.parent / "policy.json").unlink()
    policy = pin_local_trial_policy(store, repo, coordinator.local_trial)
    assert policy["local_trial"]["base"] == identity["base"]
    assert policy["worker_profile"] == packaged_profile_identity()
    assert policy == pin_local_trial_policy(store, repo, coordinator.local_trial)
    with pytest.raises(ValueError, match="differs"):
        pin_local_trial_policy(store, repo, {**identity, "request_id": "other"})
    assert packaged_profile_identity() == profile_identity(__import__("pathlib").Path(__file__).resolve().parents[1])


def test_expired_feedback_never_resets_budget(tmp_path, monkeypatch):
    _, store, _, _, coordinator, calls = setup_trial(tmp_path, monkeypatch)
    first = coordinator.run()
    store.integration_control(first["session"]["id"], "feedback-1", "feedback",
                              {"message": "Clarify."}, None, active=False)
    coordinator.clock = lambda: first["session"]["deadline"]
    before = store.snapshot()
    coordinator.run()
    assert store.snapshot() == before
    assert len(calls) == 3


def test_no_repair_authority_does_not_invoke_agent(tmp_path, monkeypatch):
    _, _, _, _, coordinator, _ = setup_trial(tmp_path, monkeypatch)
    first = coordinator.run()
    coordinator.stop.clear()
    result = Coordinator._execute(coordinator, {"payload": {"kind": "investigate_defect"}},
                                  first["session"]["deadline"])
    assert result["status"] == "succeeded"
    assert "does not authorize" in result["repair_checkpoint_gap"]


def test_repair_proposal_cannot_exceed_trusted_paths(tmp_path, monkeypatch):
    _, _, _, _, coordinator, _ = setup_trial(tmp_path, monkeypatch)
    first = coordinator.run()
    coordinator.stop.clear()
    coordinator.local_trial["repair_paths"] = ["app.py"]
    monkeypatch.setattr(coordinator, "_repair_checkpoint", lambda _job, head: head)
    monkeypatch.setattr(coordinator, "_source_context", lambda *_: ("", set(), set(), []))
    monkeypatch.setattr(coordinator, "_prompt", lambda *_: "")
    coordinator.agents.run = lambda *_args, **_kwargs: {
        "status": "succeeded", "session_id": "agent",
        "result": {"decision": "accept", "changes": [{"path": "other.py", "operation": "write", "content": "pass\n"}]},
    }
    job = {"id": "repair", "attempt": 1, "payload": {"kind": "investigate_defect"}}
    with pytest.raises(ValueError, match="exceeds authorized paths"):
        Coordinator._execute(coordinator, job, first["session"]["deadline"])


def test_expired_live_lease_records_incomplete_without_claiming_worker_stopped(tmp_path, monkeypatch):
    _, store, _, _, coordinator, _ = setup_trial(tmp_path, monkeypatch)
    first = coordinator.run()
    session_id = first["session"]["id"]
    deadline = first["session"]["deadline"]
    start = first["session"]["started_at"]
    store.resume_session(session_id, now=start + 1, deadline=deadline)
    store.enqueue_job(session_id, "scl", "implementer", "interrupted", "unique", {"kind": "generate_spec"})
    claimed = store.claim_job(session_id, "lost-worker", now=start + 2, lease_seconds=7200)
    coordinator.clock = lambda: start + 3
    with pytest.raises(ValueError, match="live job leases"):
        coordinator.run()
    assert store.session(session_id)["status"] == "running"
    coordinator.clock = lambda: deadline
    expired = coordinator.run()
    assert expired["session"]["status"] == "blocked"
    assert any(f["summary"].startswith("Local trial expired") for f in expired["findings"])
    assert next(job for job in expired["jobs"] if job["id"] == claimed["id"])["status"] == "running"
    before = store.snapshot()
    coordinator.run()
    assert store.snapshot() == before


def test_policy_drift_rejects_without_mutating_terminal_state(tmp_path, monkeypatch):
    _, store, policy, _, coordinator, _ = setup_trial(tmp_path, monkeypatch)
    coordinator.run()
    before = store.snapshot()
    policy["trust"] = "changed"
    (store.path.parent / "policy.json").write_text(json.dumps(policy))
    with pytest.raises(ValueError, match="authority changed"):
        coordinator.run()
    assert store.snapshot() == before
