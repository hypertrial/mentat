"""Independent reviews and optional feedback survive bounded continuation."""

import json
import threading
import time
from contextlib import closing

import pytest
from test_autonomy import Agent, GitHub, Worker, git, run
from test_autonomy import repo as repo

from mentat.autonomy import Coordinator, default_config
from mentat.autonomy_state import Store


@pytest.mark.parametrize("decisions", [
    ("revise", "revise"), ("revise", "accept"), ("accept", "revise"),
    ("revise", "defer"), ("defer", "revise"), ("revise", "failed"),
])
def test_review_retry_waits_for_both_outcomes_and_retains_findings(repo, tmp_path, decisions, monkeypatch):
    original_enqueue_reviewers = Coordinator._enqueue_reviewers

    def with_human_feedback(self, job, proposal, kind, evidence=None):
        if kind == "review_spec" and proposal.get("round", 0) == 0:
            job = {**job, "payload": {**job["payload"], "human_feedback": "Human intent"}}
        return original_enqueue_reviewers(self, job, proposal, kind, evidence)

    monkeypatch.setattr(Coordinator, "_enqueue_reviewers", with_human_feedback)
    release_peer = threading.Event()
    first_returning = threading.Event()
    lock = threading.Lock()
    reviewers = {}
    errors = []
    result = []

    class OrderedAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job["payload"]
            if payload["kind"] == "generate_spec" and payload.get("round") == 1:
                return {"status": "succeeded", "session_id": job["id"], "result": {
                    "decision": "defer", "summary": "captured", "findings": [],
                    "verification_requests": [],
                }}
            response = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if payload["kind"] == "review_spec" and payload["proposal"].get("round", 0) == 0:
                with lock:
                    if job["id"] not in reviewers:
                        reviewers[job["id"]] = len(reviewers)
                    index = reviewers[job["id"]]
                if index == 1:
                    if not release_peer.wait(20):
                        raise RuntimeError("test did not release peer")
                else:
                    first_returning.set()
                if decisions[index] == "failed":
                    return {"status": "failed", "error": "reviewer unavailable"}
                response["result"]["decision"] = decisions[index]
                response["result"]["findings"] = [{"summary": f"finding-{index}"}]
            return response

    def execute():
        try:
            result.append(run(repo, tmp_path, agent=OrderedAgent())[0])
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=execute)
    thread.start()
    try:
        assert first_returning.wait(20), "first reviewer did not start"
        db_path = tmp_path / "state/state.db"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            store = Store(db_path)
            with closing(store._connect()) as db:
                row = db.execute("SELECT id FROM sessions").fetchone()
            if row:
                snapshot = store.snapshot(row[0])
                if any(d.get("review_kind") == "review_spec" for d in snapshot["decisions"]):
                    break
            time.sleep(0.02)
        else:
            pytest.fail("first decision was not durably recorded")
        assert not [j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_spec"
                    and j["payload"].get("round") == 1]
    finally:
        release_peer.set()
        thread.join(30)
    assert not thread.is_alive(), "controller did not terminate"
    assert not errors, errors
    snapshot = result[0]
    retries = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_spec"
               and j["payload"].get("round") == 1]
    if "defer" in decisions or "failed" in decisions:
        assert not retries
    else:
        assert len(retries) == 1
        feedback = json.dumps(retries[0]["payload"], sort_keys=True)
        # All revise findings must survive; accepted peer observations must not be lost either.
        assert "finding-0" in feedback and "finding-1" in feedback
        assert retries[0]["payload"]["human_feedback"] == "Human intent"
        assert retries[0]["payload"]["round"] == 1


def test_human_feedback_survives_deferred_context_and_store_reopen(repo, tmp_path):
    store = Store(tmp_path / "state/state.db")
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = default_config(repo)
    head = git(repo, "rev-parse", "HEAD")
    coordinator.session_id = store.new_session(str(repo), head, head, coordinator.config, 1, 100)
    job = {"id": "human-feedback-job", "item_id": "feedback.1", "loop": "scl", "payload": {
        "kind": "generate_spec", "feedback": "Retain this human request", "accepted_spec_base": head,
    }}
    outcome = {"head": head, "context_pending": ["src.py"], "context_included": [],
               "result": {"decision": "defer", "summary": "batch", "findings": []}}
    coordinator._advance(job, outcome)
    jobs = Store(store.path).snapshot(coordinator.session_id)["jobs"]
    assert len(jobs) == 1
    assert "Retain this human request" in json.dumps(jobs[0]["payload"])
    assert jobs[0]["payload"]["targetfiles"] == ["src.py"]
    assert jobs[0]["payload"]["accepted_spec_base"] == head


def test_human_feedback_survives_capacity_recovery(repo, tmp_path):
    store = Store(tmp_path / "state/state.db")
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = default_config(repo)
    head = git(repo, "rev-parse", "HEAD")
    coordinator.session_id = store.new_session(str(repo), head, head, coordinator.config, 1, 100)
    job = {"id": "capacity-job", "item_id": "feedback.2", "loop": "scl", "payload": {
        "kind": "generate_spec", "feedback": "Original human guidance",
        "target_head": head, "review_base": head, "accepted_spec_base": head,
        "targetfiles": ["src.py"], "chunk": 0, "batch_limit": 3,
    }}
    outcome = {"spec_context_capacity": {"reason": "too large", "paths": ["src.py"]}}
    coordinator._advance(job, outcome)
    jobs = Store(store.path).snapshot(coordinator.session_id)["jobs"]
    assert len(jobs) == 1
    payload = jobs[0]["payload"]
    assert payload["human_feedback"] == "Original human guidance"
    assert "too large" in json.dumps(payload["feedback"])
    assert payload["round"] == 1 and payload["accepted_spec_base"] == head


def test_human_feedback_survives_accepted_frontier(repo, tmp_path, monkeypatch):
    original = Coordinator._request_reviews
    injected = []

    def partial_review(self, job, proposal, kind):
        if kind == "review_spec" and not injected:
            injected.append(True)
            job = {**job, "payload": {**job["payload"], "human_feedback": "Keep this intent"}}
            proposal = {**proposal, "context_pending": ["src.py"]}
        return original(self, job, proposal, kind)

    monkeypatch.setattr(Coordinator, "_request_reviews", partial_review)

    class StopAtNextBatch(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            if job["payload"]["kind"] == "generate_spec" and job["payload"].get("targetfiles"):
                return {"status": "succeeded", "session_id": job["id"], "result": {
                    "decision": "defer", "summary": "captured", "findings": [],
                    "verification_requests": [],
                }}
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, _ = run(repo, tmp_path, agent=StopAtNextBatch())
    frontier = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_spec"
                and j["payload"].get("targetfiles") == ["src.py"]]
    assert len(frontier) == 1
    assert frontier[0]["payload"]["human_feedback"] == "Keep this intent"
    assert frontier[0]["payload"]["chunk"] == 1


def test_human_feedback_survives_source_correction_handoff(repo, tmp_path):
    from test_autonomy_source_corrections import (
        _review_result,
        _source_job_payload,
        _source_outcome_and_decision,
    )

    head = git(repo, "rev-parse", "HEAD")
    store = Store(tmp_path / "cycle/state.db")
    config = default_config(repo)
    session = store.new_session(str(repo), head, head, config, 1, 1000)
    store.register_source_pass(session, "cycle", "final-item", head, head,
                               "plan", "check", ["batch"], continuing=False)
    for index, role in enumerate(("code-reviewer", "data-reviewer")):
        source_payload = {**_source_job_payload(head, "plan", "check"),
                          "human_feedback": "Preserve original user intent"}
        store.enqueue_job(session, "ivl", role, f"source-{index}", f"input-{index}",
                          source_payload)
    jobs = [store.claim_job(session, f"worker-{index}", now=2) for index in range(2)]
    feedback = []
    for index, source_job in enumerate(jobs):
        assert source_job
        result = _review_result() if index == 0 else _review_result(decision="accept", corrections=[])
        outcome, decision = _source_outcome_and_decision(
            source_job["id"], head, "check", f"reviewer-{index}", f"decision-{index}", result
        )
        store.finish_job(source_job["id"], source_job["lease_token"], outcome, now=3)
        store.record_source_decision(session, "cycle", decision)
        store.finish_transition(source_job["id"])
        feedback.append({"job_id": source_job["id"], "decision_id": decision["id"], "result": result})
    payload = {"kind": "generate_spec", "target_head": head, "accepted_spec_base": head,
               "review_base": head, "source_cycle_id": "cycle", "source_cycle_round": 1,
               "feedback": feedback, "human_feedback": "Preserve original user intent",
               "targetfiles": ["src.py", "other.py"], "batch_limit": 3}
    correction_id = store.finalize_source_correction(
        session, "cycle", head, feedback, "correction", "input", payload,
        actionable=True, max_attempts=3,
    )
    correction = store.claim_job(session, "corrector", now=4)
    assert correction and correction["id"] == correction_id
    outcome = {"status": "succeeded", "head": head, "context_pending": ["src.py"],
               "context_included": ["other.py"], "context_unavailable": [],
               "result": {"decision": "defer", "summary": "Smaller frontier", "findings": []}}
    store.finish_job(correction_id, correction["lease_token"], outcome, now=5)
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config, coordinator.session_id = config, session
    coordinator._advance(correction, outcome)
    reopened = Store(store.path)
    successors = [j for j in reopened.snapshot(session)["jobs"]
                  if j["payload"].get("kind") == "generate_spec" and j["id"] != correction_id]
    assert len(successors) == 1
    successor = successors[0]
    assert successor["payload"]["human_feedback"] == "Preserve original user intent"
    assert successor["payload"]["feedback"] == feedback
    assert successor["payload"]["targetfiles"] == ["src.py"]
    assert successor["payload"]["source_cycle_id"] == "cycle"
    assert successor["payload"]["source_cycle_round"] == 1
    assert successor["payload"]["accepted_spec_base"] == head
    cycle = reopened.source_cycles(session)[0]
    assert cycle["correction_job_id"] == successor["id"] and cycle["failed_passes"] == 1
    assert reopened.claim_job(session, "next-corrector", now=6)["id"] == successor["id"]


def test_human_feedback_reaches_first_durable_source_correction(repo, tmp_path, monkeypatch):
    """Drive a human SCL draft through IVL and independent final source review."""
    from test_autonomy import _valid_publication_spec
    from test_autonomy_source_corrections import _review_result

    config_path = repo / ".mentat/workspace.json"
    config = json.loads(config_path.read_text())
    config["concurrency"] = 1
    config_path.write_text(json.dumps(config))
    _valid_publication_spec(repo)
    message = "Please consider the source mapping intent"
    original_consume = Coordinator._consume_feedback
    injected = []

    def deliver_feedback(self):
        if not injected:
            injected.append(True)
            self.control("feedback", {"message": message})
        return original_consume(self)

    monkeypatch.setattr(Coordinator, "_consume_feedback", deliver_feedback)
    saw_source_revision = []

    class FeedbackPipelineAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job["payload"]
            kind = payload["kind"]
            if kind == "generate_spec" and job["item_id"] == "cas.inventory":
                return {"status": "succeeded", "session_id": job["id"], "result": {
                    "decision": "defer", "summary": "await focused feedback", "findings": [],
                    "verification_requests": [],
                }}
            if kind == "generate_spec" and payload.get("source_cycle_id"):
                return {"status": "succeeded", "session_id": job["id"], "result": {
                    "decision": "defer", "summary": "retain correction for inspection", "findings": [],
                    "verification_requests": [],
                }}
            if kind == "generate_spec" and job["item_id"].startswith("feedback."):
                path = repo / ".mentat/fas.json"
                fas = json.loads(path.read_text())
                row = next(row for row in fas["surfaces"] if row["path"] == "src.py")
                row.update(status="mapped", requirement_ids=[fas["requirements"][0]["id"]],
                           note="Source observation")
                path.write_text(json.dumps(fas))
                return {"status": "succeeded", "session_id": job["id"], "result": {
                    "decision": "accept", "summary": "record source mapping", "findings": [],
                    "verification_requests": [],
                }}
            if kind == "review_final_source":
                scope = payload["source_batch"]
                has_source = any(row["path"] == "src.py" for row in scope["obligations"])
                if has_source and not saw_source_revision:
                    saw_source_revision.append(True)
                    return {"status": "succeeded", "session_id": job["id"], "result": _review_result()}
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=FeedbackPipelineAgent(), concurrency=1)
    store = Store(tmp_path / "state/state.db")
    snapshot = store.snapshot(snapshot["session"]["id"])
    original = [j for j in snapshot["jobs"] if j["item_id"].startswith("feedback.")
                and j["payload"]["kind"] == "generate_spec" and not j["payload"].get("source_cycle_id")]
    assert len(original) == 1 and original[0]["payload"]["human_feedback"] == message
    assert any(j["payload"]["kind"] == "generate_verification" and j["status"] == "succeeded"
               for j in snapshot["jobs"])
    corrections = [j for j in snapshot["jobs"] if j["payload"].get("source_cycle_id")
                   and j["payload"]["kind"] == "generate_spec"]
    assert len(corrections) == 1, snapshot
    correction = corrections[0]["payload"]
    assert correction["human_feedback"] == message
    assert "Narrow the observation" in json.dumps(correction["feedback"])
    assert correction["source_cycle_round"] == 1
    assert store.source_cycles(snapshot["session"]["id"])[0]["id"] == correction["source_cycle_id"]
    assert not github.published


def test_distinct_human_inputs_do_not_share_same_head_verification(repo, tmp_path, monkeypatch):
    from test_autonomy import _valid_publication_spec

    config_path = repo / ".mentat/workspace.json"
    config = json.loads(config_path.read_text())
    config["concurrency"] = 1
    config_path.write_text(json.dumps(config))
    _valid_publication_spec(repo)
    fas_path = repo / ".mentat/fas.json"
    fas = json.loads(fas_path.read_text())
    row = next(row for row in fas["surfaces"] if row["path"] == "src.py")
    row.update(status="mapped", requirement_ids=[fas["requirements"][0]["id"]], note="Source")
    fas_path.write_text(json.dumps(fas))
    git(repo, "commit", "-qam", "map source")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    spec = repo / "openspec/specs/app/spec.md"
    spec.write_text(spec.read_text() + "\nReview source correspondence.\n")
    git(repo, "commit", "-qam", "change specification")
    head = git(repo, "rev-parse", "HEAD")
    messages = ["Consider input one", "Consider input two"]
    original_consume = Coordinator._consume_feedback
    injected = []

    def deliver(self):
        if not injected:
            injected.append(True)
            for message in messages:
                self.control("feedback", {"message": message})
        return original_consume(self)

    monkeypatch.setattr(Coordinator, "_consume_feedback", deliver)

    class NoOpReviewAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            kind = job["payload"]["kind"]
            if kind == "generate_spec" and job["item_id"] == "cas.inventory":
                decision = "defer"
            elif kind in {"generate_spec", "generate_verification"}:
                decision = "accept"
            elif kind == "review_final_source":
                decision = "defer"
            else:
                return super().run(repo, job, output, timeout_seconds=timeout_seconds)
            return {"status": "succeeded", "session_id": job["id"], "result": {
                "decision": decision, "summary": "no changes", "findings": [],
                "verification_requests": [], "requirements": [], "claims": [],
                "defects": [], "changes": [],
            }}

    snapshot, _, _ = run(repo, tmp_path, agent=NoOpReviewAgent(), concurrency=1)
    verifications = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_verification"]
    assert len(verifications) == 2, snapshot
    assert {j["payload"]["human_feedback"] for j in verifications} == set(messages)
    assert {j["payload"]["target_head"] for j in verifications} == {head}
    assert len({j["item_id"] for j in verifications}) == 2
    sources = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "review_final_source"]
    assert {j["payload"]["human_feedback"] for j in sources} == set(messages)
