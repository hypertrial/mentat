import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mentat.autonomy_state import Store


def make_store(tmp_path: Path) -> tuple[Store, str]:
    store = Store(tmp_path / "state.sqlite")
    session = store.new_session(str(tmp_path / "repo"), "base", "head", {}, 1, 1000)
    return store, session


def enqueue(store: Store, session: str, digest: str = "hash") -> str:
    return store.enqueue_job(session, "scl", "explorer", "item", digest, {"task": "check"})


def test_concurrent_claims_and_stale_lease(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    job_id = enqueue(store, session)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda n: store.claim_job(session, str(n), now=2, lease_seconds=2), range(8)))
    claims = [claim for claim in claims if claim is not None]
    assert len(claims) == 1
    old = claims[0]
    assert store.recover(session, 4) == 1
    new = store.claim_job(session, "new", now=4)
    assert new is not None and new["attempt"] == 2
    with pytest.raises(ValueError, match="lease"):
        store.finish_job(job_id, old["lease_token"], {"status": "succeeded"}, now=5)
    store.finish_job(job_id, new["lease_token"], {"status": "succeeded"}, now=5)
    snapshot = store.snapshot(session)
    assert snapshot["jobs"][0]["status"] == "succeeded"
    assert "lease_token" not in snapshot["jobs"][0]
    assert snapshot["revision"] == snapshot["events"][-1]["seq"]


def test_retries_persist_and_input_pin_changes(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    first = enqueue(store, session)
    for attempt in range(1, 4):
        store = Store(store.path)
        assert enqueue(store, session) == first
        job = store.claim_job(session, "worker", now=attempt + 1)
        assert job is not None and job["attempt"] == attempt
        store.finish_job(first, job["lease_token"], {"status": "failed"}, now=attempt + 1)
    assert store.claim_job(session, "worker", now=6) is None
    assert store.snapshot(session)["jobs"][0]["status"] == "failed"
    assert enqueue(store, session, "new-hash") != first
    with pytest.raises(ValueError, match="conflicts"):
        store.enqueue_job(session, "scl", "explorer", "item", "hash", {"task": "changed"})


def test_expired_attempts_are_bounded_across_restarts(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    enqueue(store, session)
    for now in (2, 4, 6):
        store = Store(store.path)
        assert store.claim_job(session, "worker", now=now, lease_seconds=1) is not None
        assert store.recover(session, now + 1) == 1
    assert store.claim_job(session, "worker", now=8) is None
    assert store.snapshot(session)["jobs"][0]["status"] == "failed"


def test_records_immutable_and_requirements_separate_from_claims(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    requirement = {"id": "r", "area": "acceptance", "text": "works"}
    evidence = {"id": "e", "content": "trace"}
    store.put_record(session, "requirement", requirement)
    store.put_record(session, "evidence", evidence)
    store.put_record(session, "evidence", evidence)
    with pytest.raises(ValueError, match="immutable"):
        store.put_record(session, "evidence", {"id": "e", "content": "changed"})
    with pytest.raises(ValueError, match="area"):
        store.put_record(session, "requirement", {"area": "unknown"})
    snap = store.snapshot(session)
    assert snap["requirements"] == [requirement]
    assert snap["claims"] == [] and snap["evidence"] == [evidence]
    assert len([e for e in snap["events"] if e["kind"] == "record_added"]) == 2


def test_invalidated_session_fences_worker(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    job_id = enqueue(store, session)
    job = store.claim_job(session, "worker", now=2)
    assert job is not None
    store.invalidate(session, {"head": "changed"})
    with pytest.raises(ValueError, match="lease"):
        store.finish_job(job_id, job["lease_token"], {"status": "succeeded"}, now=3)
    assert store.snapshot(session)["session"]["status"] == "invalidated"


def test_unknown_schema_and_repository_path_rejected(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=42")
    with pytest.raises(ValueError, match="unsupported"):
        Store(path)
    store = Store(tmp_path / "repo" / "state.sqlite")
    with pytest.raises(ValueError, match="outside"):
        store.new_session(str(tmp_path / "repo"), "base", "head", {}, 1, 2)


def test_resume_preserves_attempts_and_validates_deadline(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    enqueue(store, session)
    job = store.claim_job(session, "worker", now=2, lease_seconds=100)
    assert job is not None
    store.session_status(session, "paused")
    with pytest.raises(ValueError, match="two hours"):
        store.resume_session(session, now=4, deadline=7205)
    with pytest.raises(ValueError, match="live job leases"):
        store.resume_session(session, now=4, deadline=7204)
    store.resume_session(session, now=102, deadline=7302)
    next_job = store.claim_job(session, "worker", now=103)
    assert next_job is not None and next_job["attempt"] == 2
    with pytest.raises(ValueError, match="lease"):
        store.finish_job(job["id"], job["lease_token"], {"status": "succeeded"}, now=103)
    store.invalidate(session, {"head": "changed"})
    with pytest.raises(ValueError, match="invalidated"):
        store.resume_session(session, now=104, deadline=105)


def test_revision_compare_and_swap_and_history(tmp_path: Path) -> None:
    from mentat.autonomy_state import record_digest

    store, session = make_store(tmp_path)
    old = {"id": "r", "area": "product", "text": "old"}
    new = {**old, "text": "new"}
    store.put_record(session, "requirement", old)
    digest = store.revise_record(session, "requirement", new, expected_digest=record_digest(old))
    assert digest == record_digest(new)
    with pytest.raises(ValueError, match="stale"):
        store.revise_record(session, "requirement", old, expected_digest=record_digest(old))
    with pytest.raises(ValueError, match="cannot be revised"):
        store.revise_record(session, "evidence", {"id": "e"}, expected_digest=digest)
    assert store.snapshot(session)["requirements"] == [new]
    revision = next(e for e in store.events(session) if e["kind"] == "record_revised")
    assert revision["payload"]["previous"] == old
    assert revision["payload"]["record"] == new


def test_event_pagination_and_stale_projection(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    store.put_record(session, "claim", {"id": "c", "status": "proved"})
    store.put_record(session, "evidence", {"id": "e", "status": "pass"})
    store.invalidate(session, {"head": "different"})
    first = store.events(session, limit=2)
    remaining = store.events(session, after=first[-1]["seq"])
    assert first + remaining == store.snapshot(session)["events"]
    assert len(first) == 2
    with pytest.raises(ValueError, match="limit"):
        store.events(session, limit=1001)
    snap = store.snapshot(session)
    assert snap["claims"][0] == {"id": "c", "status": "proved", "freshness": "stale"}
    assert snap["evidence"][0] == {"id": "e", "status": "pass", "freshness": "stale"}
    assert store.events(session)[2]["payload"]["record"]["status"] == "pass"


def test_deadline_concurrency_and_resume_gate(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    for n in range(5):
        store.enqueue_job(session, "scl", "explorer", str(n), "digest", {})
    with ThreadPoolExecutor(max_workers=5) as pool:
        claims = list(pool.map(lambda n: store.claim_job(session, str(n), now=999, lease_seconds=100), range(5)))
    claims = [claim for claim in claims if claim is not None]
    assert len(claims) == 3
    assert all(claim["lease_until"] == 1000 for claim in claims)
    with pytest.raises(ValueError, match="lease"):
        store.finish_job(claims[0]["id"], claims[0]["lease_token"], {"status": "succeeded"}, now=1000)
    store.session_status(session, "stopped")
    with pytest.raises(ValueError, match="resume_session"):
        store.session_status(session, "running")


def test_role_changes_do_not_reset_retry_budget(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    for n, role in enumerate(("explorer", "debugger", "implementer")):
        job_id = store.enqueue_job(session, "scl", role, "item", "same", {})
        job = store.claim_job(session, "worker", now=2 + n)
        assert job is not None
        store.finish_job(job["id"], job["lease_token"], {"status": "failed"}, now=2 + n)
        # Exhaust this role by choosing another only after its prior job is completed.
        if n < 2:
            store.session_status(session, "paused")
            store.resume_session(session, now=3 + n, deadline=1000)
        assert job_id
    store.enqueue_job(session, "scl", "verifier", "item", "same", {})
    assert store.claim_job(session, "worker", now=6) is None
    store.enqueue_job(session, "scl", "verifier", "item", "new-input", {})
    assert store.claim_job(session, "worker", now=7) is not None


def test_revision_fences_evidence_and_live_jobs(tmp_path: Path) -> None:
    from mentat.autonomy_state import record_digest

    store, session = make_store(tmp_path)
    claim = {"id": "c", "status": "proved"}
    store.put_record(session, "claim", claim)
    store.put_record(session, "evidence", {"id": "e", "status": "pass"})
    job_id = enqueue(store, session)
    job = store.claim_job(session, "worker", now=2)
    assert job is not None
    store.revise_record(session, "claim", {**claim, "status": "changed"}, expected_digest=record_digest(claim))
    snap = store.snapshot(session)
    assert snap["session"]["status"] == "running"
    assert snap["evidence"][0]["freshness"] == "stale"
    with pytest.raises(ValueError, match="lease"):
        store.finish_job(job_id, job["lease_token"], {"status": "succeeded"}, now=3)


def test_success_transition_survives_restart_and_ack_is_idempotent(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    job_id = enqueue(store, session)
    job = store.claim_job(session, "worker", now=2)
    assert job is not None
    store.finish_job(job_id, job["lease_token"], {"status": "succeeded", "data": {"x": 1}}, now=3)
    restarted = Store(store.path)
    pending = restarted.pending_transitions(session)
    assert len(pending) == 1 and pending[0]["id"] == job_id
    assert pending[0]["payload"] == {"task": "check"}
    assert pending[0]["outcome"]["data"] == {"x": 1}
    assert "lease_token" not in pending[0]
    # A crash while applying idempotent effects leaves the receipt available.
    assert Store(store.path).pending_transitions(session) == pending
    restarted.finish_transition(job_id)
    restarted.finish_transition(job_id)
    assert Store(store.path).pending_transitions(session) == []
    assert len([event for event in restarted.events(session) if event["kind"] == "transition_finished"]) == 1
    with pytest.raises(ValueError, match="unknown"):
        restarted.finish_transition("missing")


def test_failed_job_never_creates_transition(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    job_id = enqueue(store, session)
    job = store.claim_job(session, "worker", now=2)
    assert job is not None
    store.finish_job(job_id, job["lease_token"], {"status": "failed"}, now=3)
    assert store.pending_transitions(session) == []


def test_revision_stales_old_dependencies_and_fences_pending_jobs(tmp_path: Path) -> None:
    from mentat.autonomy_state import record_digest

    store, session = make_store(tmp_path)
    req = {"id": "r", "area": "product", "text": "old"}
    store.put_record(session, "requirement", req)
    store.put_record(session, "claim", {"id": "c", "status": "proved"})
    store.put_record(session, "evidence", {"id": "e", "status": "pass"})
    job_id = enqueue(store, session)
    job = store.claim_job(session, "worker", now=2)
    assert job is not None
    store.revise_record(session, "requirement", {**req, "text": "new"}, expected_digest=record_digest(req))
    snap = store.snapshot(session)
    assert snap["session"]["status"] == "running"
    assert snap["claims"][0]["freshness"] == "stale"
    assert snap["evidence"][0]["freshness"] == "stale"
    assert snap["jobs"][0]["status"] == "stale"
    with pytest.raises(ValueError, match="lease"):
        store.finish_job(job_id, job["lease_token"], {"status": "succeeded"}, now=3)
    new_req = {**req, "text": "new"}
    with pytest.raises(ValueError, match="stale dependencies"):
        store.revise_record(session, "requirement", req, expected_digest=record_digest(new_req), invalidate_dependents=False)
    # New evidence created after the revision is not automatically stale.
    store.put_record(session, "evidence", {"id": "new", "status": "pass"})
    assert "freshness" not in store.snapshot(session)["evidence"][1]


def test_only_matching_unproved_claim_can_be_refreshed(tmp_path: Path) -> None:
    from mentat.autonomy_state import record_digest

    store, session = make_store(tmp_path)
    requirement = {"id": "r", "area": "product"}
    claim = {"id": "c", "status": "unattempted"}
    store.put_record(session, "requirement", requirement)
    store.put_record(session, "claim", claim)
    store.put_record(session, "claim", {"id": "proved", "status": "proved"})
    store.put_record(session, "evidence", {"id": "e", "status": "pass"})
    store.revise_record(session, "requirement", {**requirement, "text": "new"}, expected_digest=record_digest(requirement))
    with pytest.raises(ValueError, match="stale record"):
        store.refresh_record(session, "claim", "c", expected_digest="bad")
    with pytest.raises(ValueError, match="proof"):
        store.refresh_record(session, "claim", "proved", expected_digest=record_digest({"id": "proved", "status": "proved"}))
    store.refresh_record(session, "claim", "c", expected_digest=record_digest(claim))
    snapshot = store.snapshot(session)
    assert "freshness" not in snapshot["claims"][0]
    assert snapshot["claims"][1]["freshness"] == "stale"
    assert snapshot["evidence"][0]["freshness"] == "stale"


def test_existing_store_conservatively_recovers_unreceipted_success(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    job_id = enqueue(store, session)
    job = store.claim_job(session, "worker", now=2)
    assert job is not None
    store.finish_job(job_id, job["lease_token"], {"status": "succeeded"}, now=3)
    # Simulate a database created before transition receipts existed.
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE transitions")
    recovered = Store(store.path)
    assert [job["id"] for job in recovered.pending_transitions(session)] == [job_id]


def test_revision_stales_other_receipts_but_keeps_owner_for_replay(tmp_path: Path) -> None:
    from mentat.autonomy_state import record_digest

    store, session = make_store(tmp_path)
    req = {"id": "r", "area": "product", "text": "old"}
    store.put_record(session, "requirement", req)
    owner = enqueue(store, session)
    other = store.enqueue_job(session, "ivl", "verifier", "other", "digest", {})
    for job_id in (owner, other):
        job = store.claim_job(session, "worker", now=2)
        assert job is not None and job["id"] == job_id
        store.finish_job(job_id, job["lease_token"], {"status": "succeeded"}, now=3)
    revised = {**req, "text": "new"}
    store.revise_record(session, "requirement", revised, expected_digest=record_digest(req), owner_job_id=owner)
    restarted = Store(store.path)
    assert restarted.transition_status(owner) == "pending"
    assert restarted.transition_status(other) == "stale"
    assert [job["id"] for job in restarted.pending_transitions(session)] == [owner]
    with pytest.raises(ValueError, match="stale"):
        restarted.finish_transition(other)
    restarted.finish_transition(owner)
    assert restarted.transition_status(owner) == "done"
    with pytest.raises(ValueError, match="owner"):
        restarted.revise_record(session, "requirement", req, expected_digest=record_digest(revised), owner_job_id=owner)


def test_ledger_migration_preserves_pending_feedback_and_is_idempotent(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    first = store.append_event(session, "human_feedback", {"message": "first"})
    second = store.append_event(session, "human_feedback", {"message": "second"})
    store.append_event(session, "feedback_consumed", {"seq": first})
    original = store.snapshot(session)
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER feedback_pending_insert")
        db.execute("DROP TRIGGER feedback_pending_consume")
        db.execute("DROP TABLE pending_feedback")
        db.execute("DROP INDEX events_session_seq")
        db.execute("DROP INDEX events_session_kind_seq")
        db.execute("DROP INDEX feedback_consumed_seq")
        db.execute("PRAGMA user_version=1")
    store = Store(store.path)
    assert store.snapshot(session) == original
    assert store.session(session) == original["session"]
    assert store.pending_feedback(session) == [{"seq": second, "payload": {"message": "second"}}]
    store = Store(store.path)
    assert store.snapshot(session) == original
    store.append_event(session, "feedback_consumed", {"seq": second})
    assert Store(store.path).pending_feedback(session) == []
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert store.snapshot(session)["schema_version"] == 1
        plan = db.execute("EXPLAIN QUERY PLAN SELECT seq, kind FROM events WHERE session_id=? AND seq>? ORDER BY seq", (session, 0)).fetchall()
        assert any("events_session_seq" in row[3] for row in plan)
        plan = db.execute("EXPLAIN QUERY PLAN SELECT f.seq FROM pending_feedback p JOIN events f ON f.seq=p.seq WHERE p.session_id=? ORDER BY p.seq LIMIT 100", (session,)).fetchall()
        assert any("pending_feedback_session_seq" in row[3] for row in plan)


def test_pending_feedback_is_bounded_and_ordered(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    ids = [store.append_event(session, "human_feedback", {"message": str(i)}) for i in range(4)]
    assert [event["seq"] for event in store.pending_feedback(session, limit=2)] == ids[:2]
    store.append_event(session, "feedback_consumed", {"seq": str(ids[0])})
    assert [event["seq"] for event in store.pending_feedback(session, limit=2)] == ids[:2]
    store.append_event(session, "feedback_consumed", {"seq": ids[0]})
    assert [event["seq"] for event in Store(store.path).pending_feedback(session, limit=2)] == ids[1:3]


def test_migration_failure_rolls_back_indexes_and_version(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    before = store.snapshot(session)
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER feedback_pending_insert")
        db.execute("DROP TRIGGER feedback_pending_consume")
        db.execute("DROP INDEX events_session_seq")
        db.execute("DROP INDEX events_session_kind_seq")
        db.execute("DROP INDEX feedback_consumed_seq")
        db.execute("PRAGMA user_version=1")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        Store(store.path)
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='events_session_seq'").fetchone() is None
    assert store.snapshot(session) == before


def test_feedback_enqueue_replay_before_acknowledgement(tmp_path: Path) -> None:
    store, session = make_store(tmp_path)
    seq = store.append_event(session, "human_feedback", {"message": "retry"})
    payload = {"feedback": "retry"}
    job = store.enqueue_job(session, "scl", "implementer", f"feedback.{seq}", "digest", payload)
    store = Store(store.path)
    assert store.pending_feedback(session)[0]["seq"] == seq
    assert store.enqueue_job(session, "scl", "implementer", f"feedback.{seq}", "digest", payload) == job
    store.append_event(session, "feedback_consumed", {"seq": seq})
    assert Store(store.path).pending_feedback(session) == []
    assert len(store.snapshot(session)["jobs"]) == 1


def test_projection_keeps_scope_order_across_claims() -> None:
    from mentat.autonomy import digest, project_snapshot

    contract = {"path": "source.py", "function": "f", "inputs": {}, "requires": "True", "ensures": "True"}
    claims = [{"id": f"c{i}", "formal_contract": contract} for i in range(30)]
    evidence = []
    scopes = []
    for claim in reversed(claims):
        for suffix in ("b", "a"):
            evidence_id = claim["id"] + suffix
            evidence.append({"id": evidence_id, "claim_id": claim["id"], "status": "counterexample",
                             "method": "source-proof", "contract_origin": "reviewed-fas",
                             "contract": {k: contract[k] for k in ("inputs", "requires", "ensures")},
                             "source_identity": {"revision": "head", "path": "source.py", "function": "f",
                                                 "blob_oid": "blob", "source_sha256": "sha"}})
            scopes.append({"kind": "claim_source_scope", "payload": {"head": "head", "fas_head": "fas", "evidence_id": evidence_id}})
    snapshot = {"session": {"status": "running", "policy": {}}, "claims": claims, "evidence": evidence,
                "events": [{"kind": "fas_reviewed", "payload": {"head": "fas", "claims": {c["id"]: digest(c) for c in claims}}}, *scopes]}
    projected = project_snapshot(snapshot, identity={})
    for claim in projected["claims"]:
        assert claim["status"] == "unconfirmed-counterexample"
        assert claim["evidence_ids"] == [claim["id"] + "b", claim["id"] + "a"]
    assert all("evidence_ids" not in claim for claim in snapshot["claims"])
