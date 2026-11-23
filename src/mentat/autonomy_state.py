"""Durable, transactionally fenced state for autonomous sessions."""

import hashlib
import json
import math
import sqlite3
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_JSON_BYTES = 1_000_000
LOOPS = frozenset({"scl", "ivl", "iil"})
ROLES = frozenset({"explorer", "implementer", "debugger", "test-engineer", "code-reviewer", "architecture-reviewer", "data-reviewer", "security-auditor", "verifier"})
STATUSES = frozenset({"queued", "running", "paused", "completed", "blocked", "failed", "stopped", "invalidated"})
KINDS = frozenset({"requirement", "claim", "finding", "decision", "evidence"})


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError(f"{name} must be a nonempty string of at most 4096 characters")
    return value


def _json(value: object) -> str:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("expected a JSON object")
    try:
        result = json.dumps(value, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid JSON object") from exc
    if len(result.encode()) > MAX_JSON_BYTES:
        raise ValueError("JSON object too large")
    return result


def record_digest(record: dict) -> str:
    """Return the SHA-256 digest of a canonical JSON record."""
    return hashlib.sha256(_json(record).encode("utf-8")).hexdigest()


def _time(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("time must be finite")
    return float(value)


class Store:
    """SQLite session ledger. Each operation opens its own connection and transaction."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError(f"unsupported autonomy schema version: {version}")
            if version == 0:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
                    raise ValueError("unversioned autonomy database is not empty")
                schema = '''
                    CREATE TABLE sessions (id TEXT PRIMARY KEY, status TEXT NOT NULL, repo TEXT NOT NULL,
                        base TEXT NOT NULL, head TEXT NOT NULL, started_at REAL NOT NULL,
                        deadline REAL NOT NULL, policy TEXT NOT NULL);
                    CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
                        kind TEXT NOT NULL, payload TEXT NOT NULL);
                    CREATE TABLE records (session_id TEXT NOT NULL REFERENCES sessions(id), kind TEXT NOT NULL,
                        id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(session_id, kind, id));
                    CREATE TABLE jobs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                        loop TEXT NOT NULL, role TEXT NOT NULL, item_id TEXT NOT NULL, input_digest TEXT NOT NULL,
                        payload TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                        worker_id TEXT, lease_token TEXT, lease_until REAL, outcome TEXT,
                        UNIQUE(session_id, loop, role, item_id, input_digest));
                '''
                for statement in schema.split(';'):
                    if statement.strip():
                        db.execute(statement)
                db.execute("PRAGMA user_version = 1")
            receipts_exist = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='transitions'").fetchone() is not None
            db.execute("CREATE TABLE IF NOT EXISTS transitions (job_id TEXT PRIMARY KEY REFERENCES jobs(id), status TEXT NOT NULL CHECK(status IN ('pending', 'done')))")
            db.execute("CREATE TABLE IF NOT EXISTS stale_records (session_id TEXT NOT NULL REFERENCES sessions(id), kind TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY(session_id, kind, id))")
            db.execute("CREATE TABLE IF NOT EXISTS stale_transitions (job_id TEXT PRIMARY KEY REFERENCES transitions(job_id))")
            if not receipts_exist:
                # Older stores could have crashed between finishing and advancing.
                # Replay is conservative; advancement must be idempotent.
                db.execute("INSERT INTO transitions(job_id, status) SELECT id, 'pending' FROM jobs WHERE status='succeeded'")
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 30000")
        return db

    @staticmethod
    def _event(db: sqlite3.Connection, session_id: str, kind: str, payload: dict[str, Any]) -> int:
        cur = db.execute("INSERT INTO events(session_id, kind, payload) VALUES (?, ?, ?)", (session_id, kind, _json(payload)))
        assert cur.lastrowid is not None
        return cur.lastrowid

    @staticmethod
    def _session(db: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise ValueError("unknown session")
        return row

    def new_session(self, repo: str, base: str, head: str, policy: dict, now: float, deadline: float) -> str:
        repo = _text(repo, "repo")
        base, head = _text(base, "base"), _text(head, "head")
        now, deadline = _time(now), _time(deadline)
        if deadline <= now or deadline > now + 7200:
            raise ValueError("deadline must be within two hours of start")
        if self.path.is_relative_to(Path(repo).expanduser().resolve()):
            raise ValueError("session database must be outside the repository")
        policy_json = _json(policy)
        session_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO sessions VALUES (?, 'running', ?, ?, ?, ?, ?, ?)", (session_id, repo, base, head, now, deadline, policy_json))
            self._event(db, session_id, "session_started", {"repo": repo, "base": base, "head": head, "started_at": now, "deadline": deadline, "policy": policy})
            db.commit()
        return session_id

    def append_event(self, session_id: str, kind: str, payload: dict) -> int:
        _text(kind, "kind")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._session(db, session_id)
            seq = self._event(db, session_id, kind, payload)
            db.commit()
            return seq

    def put_record(self, session_id: str, kind: str, record: dict) -> str:
        if kind not in KINDS:
            raise ValueError("unknown record kind")
        data = dict(record) if isinstance(record, dict) else record
        _json(data)
        record_id = _text(data.get("id", uuid.uuid4().hex), "record id")
        data["id"] = record_id
        if kind == "requirement" and data.get("area") not in {"product", "functional", "nonfunctional", "architecture", "data-api", "acceptance"}:
            raise ValueError("invalid requirement area")
        encoded = _json(data)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._session(db, session_id)
            old = db.execute("SELECT data FROM records WHERE session_id=? AND kind=? AND id=?", (session_id, kind, record_id)).fetchone()
            if old:
                if old[0] != encoded:
                    raise ValueError("records are immutable")
            else:
                db.execute("INSERT INTO records VALUES (?, ?, ?, ?)", (session_id, kind, record_id, encoded))
                self._event(db, session_id, "record_added", {"kind": kind, "record": data})
            db.commit()
        return record_id

    def revise_record(self, session_id: str, kind: str, record: dict, *, expected_digest: str, invalidate_dependents: bool = True, owner_job_id: str | None = None) -> str:
        """Replace a planning record only when its current digest matches."""
        if kind not in {"requirement", "claim", "finding"}:
            raise ValueError("record kind cannot be revised")
        if not isinstance(invalidate_dependents, bool) or (kind in {"requirement", "claim"} and not invalidate_dependents):
            raise ValueError("requirement and claim revisions must stale dependencies")
        encoded = _json(record)
        record_id = _text(record.get("id"), "record id")
        if kind == "requirement" and record.get("area") not in {"product", "functional", "nonfunctional", "architecture", "data-api", "acceptance"}:
            raise ValueError("invalid requirement area")
        _text(expected_digest, "expected_digest")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._session(db, session_id)
            if owner_job_id is not None:
                _text(owner_job_id, "owner_job_id")
                owner = db.execute("SELECT 1 FROM transitions JOIN jobs ON jobs.id=transitions.job_id WHERE transitions.job_id=? AND transitions.status='pending' AND jobs.session_id=?", (owner_job_id, session_id)).fetchone()
                if owner is None:
                    raise ValueError("owner must be a pending transition in this session")
            old = db.execute("SELECT data FROM records WHERE session_id=? AND kind=? AND id=?", (session_id, kind, record_id)).fetchone()
            if old is None:
                raise ValueError("unknown record")
            previous = json.loads(old["data"])
            if record_digest(previous) != expected_digest:
                raise ValueError("stale record digest")
            digest = record_digest(record)
            if old["data"] != encoded:
                db.execute("UPDATE records SET data=? WHERE session_id=? AND kind=? AND id=?", (encoded, session_id, kind, record_id))
                self._event(db, session_id, "record_revised", {"kind": kind, "previous": previous, "record": record, "previous_digest": expected_digest, "digest": digest})
                if kind in {"requirement", "claim"}:
                    db.execute("INSERT OR IGNORE INTO stale_records SELECT session_id, kind, id FROM records WHERE session_id=? AND kind IN ('claim', 'evidence')", (session_id,))
                    db.execute("UPDATE jobs SET status='stale', worker_id=NULL, lease_token=NULL, lease_until=NULL WHERE session_id=? AND status IN ('queued', 'running')", (session_id,))
                    stale_jobs = db.execute("SELECT transitions.job_id FROM transitions JOIN jobs ON jobs.id=transitions.job_id WHERE jobs.session_id=? AND transitions.status='pending' AND transitions.job_id != coalesce(?, '')", (session_id, owner_job_id)).fetchall()
                    for stale_job in stale_jobs:
                        db.execute("INSERT INTO stale_transitions(job_id) VALUES (?)", (stale_job["job_id"],))
                        db.execute("UPDATE transitions SET status='done' WHERE job_id=?", (stale_job["job_id"],))
                        self._event(db, session_id, "transition_staled", {"job_id": stale_job["job_id"], "revision_kind": kind, "revision_id": record_id})
                    self._event(db, session_id, "dependencies_staled", {"reason": "record revised", "kind": kind, "id": record_id})
            db.commit()
            return digest

    def refresh_record(self, session_id: str, kind: str, record_id: str, *, expected_digest: str) -> None:
        """Mark a reviewed, explicitly unproved claim as current, never its old evidence."""
        if kind != "claim":
            raise ValueError("only unproved claims can be refreshed")
        _text(record_id, "record id")
        _text(expected_digest, "expected_digest")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            session = self._session(db, session_id)
            if session["status"] != "running":
                raise ValueError("session is not running")
            row = db.execute("SELECT data FROM records WHERE session_id=? AND kind='claim' AND id=?", (session_id, record_id)).fetchone()
            if row is None:
                raise ValueError("unknown claim")
            record = json.loads(row["data"])
            if record_digest(record) != expected_digest:
                raise ValueError("stale record digest")
            if record.get("status", "unattempted") not in {"unattempted", "unsupported", "inconclusive"}:
                raise ValueError("agent-authored proof cannot be refreshed")
            removed = db.execute("DELETE FROM stale_records WHERE session_id=? AND kind='claim' AND id=?", (session_id, record_id))
            if removed.rowcount:
                self._event(db, session_id, "claim_refreshed", {"id": record_id, "digest": expected_digest})
            db.commit()

    def enqueue_job(self, session_id: str, loop: str, role: str, item_id: str, input_digest: str, payload: dict) -> str:
        if loop not in LOOPS or role not in ROLES:
            raise ValueError("invalid loop or role")
        _text(item_id, "item_id")
        _text(input_digest, "input_digest")
        encoded = _json(payload)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._session(db, session_id)
            old = db.execute("SELECT id, payload FROM jobs WHERE session_id=? AND loop=? AND role=? AND item_id=? AND input_digest=?", (session_id, loop, role, item_id, input_digest)).fetchone()
            if old:
                if old["payload"] != encoded:
                    raise ValueError("job payload conflicts with pinned input")
                job_id = old["id"]
            else:
                job_id = uuid.uuid4().hex
                db.execute("INSERT INTO jobs(id, session_id, loop, role, item_id, input_digest, payload, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued')", (job_id, session_id, loop, role, item_id, input_digest, encoded))
                self._event(db, session_id, "job_enqueued", {"job_id": job_id, "loop": loop, "role": role, "item_id": item_id, "input_digest": input_digest, "payload": payload})
            db.commit()
        return job_id

    @staticmethod
    def _recover(db: sqlite3.Connection, session_id: str, now: float, *, all_leases: bool = False) -> int:
        rows = db.execute("SELECT id, attempt FROM jobs WHERE session_id=? AND status='running' AND (lease_until<=? OR ?)", (session_id, now, all_leases)).fetchall()
        for row in rows:
            status = "failed" if row["attempt"] >= 3 else "queued"
            db.execute("UPDATE jobs SET status=?, worker_id=NULL, lease_token=NULL, lease_until=NULL WHERE id=?", (status, row["id"]))
            Store._event(db, session_id, "job_lease_expired", {"job_id": row["id"], "attempt": row["attempt"], "status": status, "now": now})
        return len(rows)

    def recover(self, session_id: str, now: float) -> int:
        now = _time(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._session(db, session_id)
            count = self._recover(db, session_id, now)
            db.commit()
            return count

    def claim_job(self, session_id: str, worker_id: str, *, now: float, lease_seconds: float = 120) -> dict | None:
        _text(worker_id, "worker_id")
        now, lease_seconds = _time(now), _time(lease_seconds)
        if lease_seconds <= 0 or not math.isfinite(now + lease_seconds):
            raise ValueError("invalid lease duration")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            session = self._session(db, session_id)
            self._recover(db, session_id, now)
            row = None
            running = db.execute("SELECT count(*) FROM jobs WHERE session_id=? AND status='running'", (session_id,)).fetchone()[0]
            if session["status"] == "running" and now < session["deadline"] and running < 3:
                candidates = db.execute("SELECT * FROM jobs WHERE session_id=? AND status='queued' ORDER BY rowid", (session_id,)).fetchall()
                for candidate in candidates:
                    totals = db.execute("SELECT coalesce(sum(attempt - CASE WHEN status IN ('running', 'succeeded') THEN 1 ELSE 0 END), 0), sum(CASE WHEN status='running' THEN 1 ELSE 0 END) FROM jobs WHERE session_id=? AND item_id=? AND input_digest=?", (session_id, candidate["item_id"], candidate["input_digest"])).fetchone()
                    failures, active = totals[0], totals[1] or 0
                    if failures >= 3:
                        db.execute("UPDATE jobs SET status='failed' WHERE id=?", (candidate["id"],))
                        self._event(db, session_id, "job_retry_limit", {"job_id": candidate["id"]})
                    elif failures + active < 3:
                        row = candidate
                        break
            if row is None:
                db.commit()
                return None
            token = uuid.uuid4().hex
            lease_until = min(now + lease_seconds, session["deadline"])
            db.execute("UPDATE jobs SET status='running', attempt=attempt+1, worker_id=?, lease_token=?, lease_until=? WHERE id=?", (worker_id, token, lease_until, row["id"]))
            self._event(db, session_id, "job_claimed", {"job_id": row["id"], "worker_id": worker_id, "attempt": row["attempt"] + 1, "lease_until": lease_until})
            result = dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
            result["payload"] = json.loads(result["payload"])
            db.commit()
            return result

    def finish_job(self, job_id: str, lease_token: str, outcome: dict, *, now: float) -> None:
        _text(job_id, "job_id")
        _text(lease_token, "lease_token")
        now = _time(now)
        encoded = _json(outcome)
        if outcome.get("status") not in {"succeeded", "failed"}:
            raise ValueError("invalid outcome status")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            session = self._session(db, row["session_id"]) if row is not None else None
            if row is None or row["status"] != "running" or row["lease_token"] != lease_token or row["lease_until"] <= now or session is None or session["status"] != "running" or now >= session["deadline"]:
                raise ValueError("stale or invalid job lease")
            status = "succeeded" if outcome["status"] == "succeeded" else ("failed" if row["attempt"] >= 3 else "queued")
            db.execute("UPDATE jobs SET status=?, outcome=?, worker_id=NULL, lease_token=NULL, lease_until=NULL WHERE id=?", (status, encoded, job_id))
            self._event(db, row["session_id"], "job_finished", {"job_id": job_id, "attempt": row["attempt"], "status": status, "outcome": outcome, "now": now})
            if status == "succeeded":
                db.execute("INSERT INTO transitions(job_id, status) VALUES (?, 'pending')", (job_id,))
            db.commit()

    def pending_transitions(self, session_id: str) -> list[dict]:
        """Return successful jobs whose advancement has not been acknowledged."""
        with self._connect() as db:
            self._session(db, session_id)
            rows = db.execute("SELECT jobs.* FROM transitions JOIN jobs ON jobs.id=transitions.job_id WHERE jobs.session_id=? AND transitions.status='pending' ORDER BY jobs.rowid", (session_id,)).fetchall()
            result = []
            for row in rows:
                job = dict(row)
                job.pop("lease_token", None)
                job["payload"] = json.loads(job["payload"])
                job["outcome"] = json.loads(job["outcome"])
                result.append(job)
            return result

    def transition_status(self, job_id: str) -> str:
        """Return pending, done or stale for a durable advancement receipt."""
        _text(job_id, "job_id")
        with self._connect() as db:
            row = db.execute("SELECT transitions.status, stale_transitions.job_id AS stale FROM transitions LEFT JOIN stale_transitions ON stale_transitions.job_id=transitions.job_id WHERE transitions.job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("unknown job transition")
            return "stale" if row["stale"] is not None else row["status"]

    def finish_transition(self, job_id: str) -> None:
        """Acknowledge an idempotently applied advancement; repeated calls are safe."""
        _text(job_id, "job_id")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT transitions.status, jobs.session_id FROM transitions JOIN jobs ON jobs.id=transitions.job_id WHERE transitions.job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("unknown job transition")
            if db.execute("SELECT 1 FROM stale_transitions WHERE job_id=?", (job_id,)).fetchone():
                raise ValueError("job transition is stale")
            if row["status"] == "pending":
                db.execute("UPDATE transitions SET status='done' WHERE job_id=?", (job_id,))
                self._event(db, row["session_id"], "transition_finished", {"job_id": job_id})
            db.commit()

    def session_status(self, session_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError("invalid session status")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            session = self._session(db, session_id)
            if session["status"] == "invalidated" and status != "invalidated":
                raise ValueError("invalidated session cannot be resumed")
            if status == "running" and session["status"] != "running":
                raise ValueError("use resume_session to start a new run")
            db.execute("UPDATE sessions SET status=? WHERE id=?", (status, session_id))
            self._event(db, session_id, "session_status", {"status": status})
            db.commit()

    def resume_session(self, session_id: str, *, now: float, deadline: float) -> None:
        now, deadline = _time(now), _time(deadline)
        if deadline <= now or deadline > now + 7200:
            raise ValueError("deadline must be within two hours of now")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            session = self._session(db, session_id)
            if session["status"] == "invalidated":
                raise ValueError("invalidated session cannot be resumed")
            if db.execute("SELECT 1 FROM jobs WHERE session_id=? AND status='running' AND lease_until>? LIMIT 1", (session_id, now)).fetchone():
                raise ValueError("live job leases prevent resume")
            self._recover(db, session_id, now)
            db.execute("UPDATE sessions SET status='running', deadline=? WHERE id=?", (deadline, session_id))
            self._event(db, session_id, "session_resumed", {"now": now, "deadline": deadline})
            db.commit()

    def events(self, session_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("after must be a nonnegative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as db:
            self._session(db, session_id)
            rows = db.execute("SELECT seq, kind, payload FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?", (session_id, after, limit)).fetchall()
            result = [dict(row) for row in rows]
            for event in result:
                event["payload"] = json.loads(event["payload"])
            return result

    def invalidate(self, session_id: str, identity: dict) -> None:
        _json(identity)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._session(db, session_id)
            db.execute("UPDATE sessions SET status='invalidated' WHERE id=?", (session_id,))
            self._event(db, session_id, "session_invalidated", {"identity": identity})
            db.commit()

    def snapshot(self, session_id: str | None = None) -> dict:
        with self._connect() as db:
            db.execute("BEGIN")
            if session_id is None:
                row = db.execute("SELECT * FROM sessions ORDER BY rowid DESC LIMIT 1").fetchone()
                if row is None:
                    raise ValueError("no session")
                session_id = row["id"]
            else:
                row = self._session(db, session_id)
            session = dict(row)
            session["policy"] = json.loads(session["policy"])
            events = [dict(r) for r in db.execute("SELECT seq, kind, payload FROM events WHERE session_id=? ORDER BY seq", (session_id,))]
            for event in events:
                event["payload"] = json.loads(event["payload"])
            result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "revision": events[-1]["seq"] if events else 0, "session": session}
            for kind in KINDS:
                result["evidence" if kind == "evidence" else kind + "s"] = [json.loads(r[0]) for r in db.execute("SELECT data FROM records WHERE session_id=? AND kind=? ORDER BY rowid", (session_id, kind))]
            jobs = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE session_id=? ORDER BY rowid", (session_id,))]
            for job in jobs:
                job.pop("lease_token", None)
                job["payload"] = json.loads(job["payload"])
                if job["outcome"] is not None:
                    job["outcome"] = json.loads(job["outcome"])
            stale = {(r["kind"], r["id"]) for r in db.execute("SELECT kind, id FROM stale_records WHERE session_id=?", (session_id,))}
            for kind, key in (("claim", "claims"), ("evidence", "evidence")):
                for record in result[key]:
                    if session["status"] == "invalidated" or (kind, record["id"]) in stale:
                        record["freshness"] = "stale"
            result["jobs"] = jobs
            result["events"] = events
            db.commit()
            return result
