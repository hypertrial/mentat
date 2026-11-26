"""Durable local review decisions and the shared acceptance gate."""

import fcntl
import os
import re
import stat
from contextlib import contextmanager
from datetime import datetime

from . import evidence, records


@contextmanager
def locked(state):
    with (state / ".review-decisions.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def journal(state):
    path = state / "review-decisions.json"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Review decision journal is not a regular file")
            raw = stream.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("Review decision journal exceeds size limit")
            entries = evidence.decode_json(raw)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise ValueError("Review decision journal is unreadable") from exc
    try:
        if not isinstance(entries, list):
            raise ValueError
        seen = set()
        states = {}
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {"requestId", "action", "reason", "actor", "evaluation", "committedAt"}
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", entry["requestId"])
                    or entry["requestId"] in seen or entry["action"] not in {"reject", "defer", "resume"}
                    or entry["actor"] != "cockpit_local_operator"
                    or not isinstance(entry["reason"], str) or len(entry["reason"]) > 2000
                    or (entry["action"] != "resume" and not entry["reason"].strip())
                    or not isinstance(entry["evaluation"], dict)
                    or set(entry["evaluation"]) != {"runId", "manifest", "revision", "scope", "approval"}
                    or datetime.fromisoformat(entry["committedAt"]).tzinfo is None):
                raise ValueError
            evaluation = entry["evaluation"]
            scope = evaluation["scope"]
            records.check(records.Approval, evaluation["approval"])
            if (not isinstance(scope, dict) or set(scope) != {"repo", "base", "runner", "contract_digest"}
                    or not all(isinstance(v, str) and v for v in scope.values())
                    or not os.path.isabs(scope["repo"])
                    or scope["runner"] not in {"docker", "host"}
                    or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", scope["base"])
                    or not re.fullmatch(r"[a-f0-9]{64}", scope["contract_digest"])
                    or any(evaluation["approval"].get(k) != v for k, v in scope.items())
                    or not evidence.RUN_ID.fullmatch(evaluation["runId"])
                    or not re.fullmatch(r"[a-f0-9]{64}", evaluation["manifest"])
                    or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", evaluation["revision"])):
                raise ValueError
            key = evidence.json_bytes(evaluation)
            current = states.get(key, "pending")
            action = entry["action"]
            if (action == "resume" and current != "defer") or (action != "resume" and current != "pending"):
                raise ValueError
            states[key] = "pending" if action == "resume" else action
            seen.add(entry["requestId"])
    except (ValueError, TypeError, KeyError):
        raise ValueError("Review decision journal is invalid") from None
    return entries


def status(entries, evaluation):
    current = "pending"
    for entry in entries:
        if entry["evaluation"] == evaluation:
            action = entry["action"]
            if (action in {"reject", "defer"} and current != "pending") or (action == "resume" and current != "defer"):
                raise ValueError("Review decision journal has invalid transitions")
            current = "pending" if action == "resume" else action
    return current


def summary(entries, evaluation):
    current = status(entries, evaluation)
    receipt = next((entry for entry in reversed(entries) if entry["evaluation"] == evaluation), None)
    return {"status": current, **({"receipt": receipt} if receipt else {})}


def check_acceptance(state, evaluation):
    if status(journal(state), evaluation) != "pending":
        raise ValueError("Review decision blocks acceptance")


def save(state, entries):
    raw = evidence.json_bytes(entries)
    if len(raw) > 1_000_000:
        raise ValueError("Review decision journal is full")
    evidence.write_record(state / "review-decisions.json", raw)
    fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def evaluation(project, approved, record, manifest):
    from .decision import scope
    return {"runId": record["run_id"], "manifest": manifest, "revision": record["revision"],
            "scope": scope(project), "approval": approved}
