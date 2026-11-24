"""Interrupted startup must not leave a resumable session missing initial work."""

import json
import sqlite3
from contextlib import closing

import pytest
from test_autonomy import Agent, GitHub, Worker, git
from test_autonomy import repo as repo

from mentat.autonomy import Coordinator, current_tool_identity, digest
from mentat.autonomy_state import Store


def _setup(repo, tmp_path):
    config_path = repo / ".mentat/workspace.json"
    config = json.loads(config_path.read_text())
    config["source_checks"] = [
        {"path": "src.py", "function": "value", "inputs": {"x": "int"},
         "requires": "True", "ensures": "result >= x"},
        {"path": "src.py", "function": "value", "inputs": {"x": "int"},
         "requires": "x >= 0", "ensures": "result >= 0"},
    ]
    config_path.write_text(json.dumps(config))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "configure source checks")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    store = Store(tmp_path / "state/state.db")
    policy = {
        "schema_version": 1, "repo": str(repo.resolve()),
        "mandatory_local_checks": [["check"]], "mandatory_worker_checks": [],
        "model": "gpt-6.1-terra", "reasoning_effort": "high", "auto_merge": True,
        "session_seconds": 7200, "concurrency": 3, "max_attempts": 3,
    }
    (store.path.parent / "policy.json").write_text(json.dumps(policy))
    return store, config


def _assert_initial_jobs(snapshot, config):
    jobs = snapshot["jobs"]
    specs = [j for j in jobs if j["payload"]["kind"] == "generate_spec"]
    checks = [j for j in jobs if j["payload"]["kind"] == "source_check"]
    assert len(specs) == 1
    assert len(checks) == len(config["source_checks"]) == 2
    assert {json.dumps(j["payload"]["check"], sort_keys=True) for j in checks} == {
        json.dumps(check, sort_keys=True) for check in config["source_checks"]
    }
    assert len({j["id"] for j in jobs}) == len(jobs)
    events = snapshot["events"]
    assert sum(e["kind"] == "authority_pinned" for e in events) == 1
    assert sum(e["kind"] == "toolchain_pinned" for e in events) == 1
    assert sum(e["kind"] == "inventory" for e in events) == 1


@pytest.mark.parametrize("table,condition", [
    ("events", "NEW.kind = 'authority_pinned'"),
    ("events", "NEW.kind = 'toolchain_pinned'"),
    ("events", "NEW.kind = 'inventory'"),
    ("jobs", "NEW.item_id = 'cas.inventory'"),
    ("jobs", "NEW.item_id = 'source.0'"),
    ("jobs", "NEW.item_id = 'source.1'"),
])
def test_bootstrap_failure_is_atomic_or_resumes_complete(repo, tmp_path, monkeypatch, table, condition):
    store, config = _setup(repo, tmp_path)
    with closing(store._connect()) as db:
        db.execute(f"CREATE TRIGGER interrupt_bootstrap BEFORE INSERT ON {table} "
                   f"WHEN {condition} BEGIN SELECT RAISE(ABORT, 'injected crash'); END")
    # No work is dispatched after initialization, so the test isolates startup.
    monkeypatch.setattr(Store, "claim_job", lambda *args, **kwargs: None)
    with pytest.raises(sqlite3.Error, match="injected crash"):
        Coordinator(repo, store, Agent(), Worker(), GitHub()).run()
    with closing(store._connect()) as db:
        db.execute("DROP TRIGGER interrupt_bootstrap")
        sessions = [row[0] for row in db.execute("SELECT id FROM sessions")]
    assert sessions == []
    with closing(store._connect()) as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    reopened = Store(store.path)
    snapshot = Coordinator(repo, reopened, Agent(), Worker(), GitHub()).run()
    _assert_initial_jobs(snapshot, config)
    resumed = Coordinator(repo, Store(store.path), Agent(), Worker(), GitHub()).run(
        resume=snapshot["session"]["id"]
    )
    _assert_initial_jobs(resumed, config)


def test_crash_after_bootstrap_resumes_exact_initial_work(repo, tmp_path, monkeypatch):
    store, config = _setup(repo, tmp_path)
    def crash(*args, **kwargs):
        raise RuntimeError("crash before dispatch")
    monkeypatch.setattr(Store, "claim_job", crash)
    with pytest.raises(RuntimeError, match="crash before dispatch"):
        Coordinator(repo, store, Agent(), Worker(), GitHub()).run()
    with closing(store._connect()) as db:
        sessions = [row[0] for row in db.execute("SELECT id FROM sessions")]
    assert len(sessions) == 1
    _assert_initial_jobs(Store(store.path).snapshot(sessions[0]), config)
    monkeypatch.setattr(Store, "claim_job", lambda *args, **kwargs: None)
    resumed = Coordinator(repo, Store(store.path), Agent(), Worker(), GitHub()).run(
        resume=sessions[0]
    )
    _assert_initial_jobs(resumed, config)


def test_incomplete_legacy_session_is_rejected_without_repinning(repo, tmp_path):
    store, config = _setup(repo, tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    policy = json.loads((store.path.parent / "policy.json").read_text())
    session = store.new_session(str(repo), head, head, config, 1, 100)
    store.append_event(session, "authority_pinned", {"digest": digest(policy)})
    store.append_event(session, "toolchain_pinned", {"identity": current_tool_identity()})
    with pytest.raises(ValueError, match="(?i)(bootstrap|initial|incomplete)"):
        Coordinator(repo, Store(store.path), Agent(), Worker(), GitHub()).run(resume=session)
    snap = Store(store.path).snapshot(session)
    assert sum(e["kind"] == "authority_pinned" for e in snap["events"]) == 1
    assert sum(e["kind"] == "toolchain_pinned" for e in snap["events"]) == 1
    assert not snap["jobs"]
    assert snap["session"]["status"] != "completed"


def test_bootstrap_record_failure_rolls_back_session(repo, tmp_path, monkeypatch):
    import uuid

    from mentat.autonomy import AREAS

    store, config = _setup(repo, tmp_path)
    spec = repo / "openspec/specs/application/spec.md"
    spec.parent.mkdir(parents=True)
    requirements = [{"id": str(uuid.uuid4()), "area": area, "origin": "uncertain",
                     "statement": "Unspecified", "spec_path": str(spec.relative_to(repo))}
                    for area in AREAS]
    spec.write_text("\n".join(r["id"] for r in requirements))
    paths = ["src.py", ".mentat/workspace.json", ".mentat/fas.json",
             "openspec/specs/application/spec.md"]
    fas = {"schema_version": 1, "requirements": requirements, "claims": [],
           "surfaces": [{"path": path, "status": "gap", "requirement_ids": []} for path in paths]}
    (repo / ".mentat/fas.json").write_text(json.dumps(fas))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "establish specification")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    with closing(store._connect()) as db:
        db.execute("CREATE TRIGGER interrupt_records BEFORE INSERT ON records "
                   "WHEN EXISTS (SELECT 1 FROM records WHERE kind='requirement') "
                   "BEGIN SELECT RAISE(ABORT, 'record crash'); END")
    monkeypatch.setattr(Store, "claim_job", lambda *args, **kwargs: None)
    with pytest.raises(sqlite3.Error, match="record crash"):
        Coordinator(repo, store, Agent(), Worker(), GitHub()).run()
    with closing(store._connect()) as db:
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM records").fetchone()[0] == 0
        db.execute("DROP TRIGGER interrupt_records")
    snapshot = Coordinator(repo, Store(store.path), Agent(), Worker(), GitHub()).run()
    _assert_initial_jobs(snapshot, config)
    assert {r["id"] for r in snapshot["requirements"]} == {r["id"] for r in requirements}
    resumed = Coordinator(repo, Store(store.path), Agent(), Worker(), GitHub()).run(
        resume=snapshot["session"]["id"]
    )
    assert {r["id"] for r in resumed["requirements"]} == {r["id"] for r in requirements}
