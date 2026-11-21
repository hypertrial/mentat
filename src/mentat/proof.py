"""Persist and recheck bounded, approval-bound Lean proof campaigns."""

import ctypes
import fcntl
import hashlib
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from . import evidence
from .project import load_project, snapshot
from .proof_checker import LEAN_VERSION

_LIMIT = 1_000_000
_ATTEMPT_SECONDS = 120
_CAMPAIGN_SECONDS = 1800
_ROUNDS = 3
_ID = re.compile(r"[0-9a-f]{32}\Z")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return evidence.json_bytes(value)


def _regular(path, limit=_LIMIT):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError(f"Expected a bounded regular file: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"File exceeds size limit: {path}")
        return data
    finally:
        os.close(fd)


def _source(path):
    data = _regular(path)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Proof source must be UTF-8: {path}") from exc
    return data


def _directory(path):
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError(f"Expected a real directory: {path}")


def _publish_directory(source, destination):
    # Ordinary rename can replace an existing empty directory.
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "linux":
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        args = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        args = (os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise ValueError("Atomic proof task publication requires Linux or macOS")
    rename.restype = ctypes.c_int
    if rename(*args):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


@contextmanager
def _lock(path, *, blocking=True):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise ValueError("Another proof build is running; retry after it finishes") from exc
        yield
    finally:
        os.close(fd)


def _fresh(state, project, candidate, approval):
    current = load_project(project.root, project.base, project.runner)
    revision = snapshot(current.root, candidate.revision)
    if current.fingerprint != project.fingerprint or revision.digest != candidate.digest:
        raise ValueError("Project or candidate snapshot changed; prepare a new campaign")
    try:
        actual = evidence.decode_json(_regular(state / "approvals" / f"{current.fingerprint}.json"))
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError("Current contract approval is unavailable") from exc
    expected = {
        "repo": str(current.root),
        "base": current.base,
        "runner": current.runner,
        "contract_digest": current.fingerprint,
    }
    if (
        actual != approval
        or not isinstance(actual, dict)
        or any(actual.get(key) != value for key, value in expected.items())
        or not isinstance(actual.get("approved_at"), str)
    ):
        raise ValueError("Contract approval changed; approve and prepare a new campaign")
    return current, revision


def _campaign(state, campaign_id):
    if not isinstance(campaign_id, str) or not _ID.fullmatch(campaign_id):
        raise ValueError("Invalid proof campaign ID")
    path = state / "proof-campaigns" / campaign_id
    _directory(path)
    raw = _regular(path / "manifest.json")
    receipt = _regular(state / "proof-campaign-receipts" / campaign_id, 128).decode("ascii")
    if _hash(raw) != receipt:
        raise ValueError("Proof campaign manifest failed integrity validation")
    manifest = evidence.decode_json(raw)
    if manifest.get("campaign_id") != campaign_id:
        raise ValueError("Proof campaign ID does not match its manifest")
    if _hash(_regular(path / "TASK.md")) != manifest.get("task_sha256"):
        raise ValueError("Proof task instructions failed integrity validation")
    for target in manifest["all_targets"]:
        data = _source(path / "approved" / f"{target['id']}.lean")
        if _hash(data) != target["approved_sha256"]:
            raise ValueError("Frozen approved source failed integrity validation")
    return path, manifest


def _current(state, manifest):
    from . import proof_checker

    project = load_project(Path(manifest["repo"]), manifest["base"], manifest["runner"])
    candidate = snapshot(project.root, manifest["revision"])
    if (
        project.fingerprint != manifest["contract_digest"]
        or project.verifier_digest != manifest["verifier_digest"]
        or candidate.digest != manifest["snapshot_digest"]
    ):
        raise ValueError("Proof campaign inputs changed; prepare a new campaign")
    _fresh(state, project, candidate, manifest["approval"])
    # Campaign state is writable by same-account code; targets must come from the contract.
    expected, _ = _targets(project)
    ids = [target["id"] for target in expected]
    selected = manifest["targets"]
    if (
        _json(manifest["all_targets"]) != _json(expected)
        or not isinstance(selected, list)
        or not selected
        or selected != [item for item in ids if item in selected]
    ):
        raise ValueError("Proof campaign targets do not match the approved contract")
    if proof_checker.checker_identity() != manifest["checker_identity"]:
        raise ValueError("Proof checker identity changed; prepare a new campaign")
    return project, candidate


def _ledger(path):
    cached = evidence.decode_json(_regular(path / "ledger.json"))
    if not isinstance(cached, (list, dict)):
        raise ValueError("Invalid proof campaign ledger")
    directory = path / "entries"
    _directory(directory)
    by_previous = {}
    for entry in directory.iterdir():
        raw = _regular(entry)
        if entry.name != f"{_hash(raw)}.json":
            raise ValueError("Proof ledger entry failed integrity validation")
        item = evidence.decode_json(raw)
        if not isinstance(item, dict) or not isinstance(item.get("previous"), str):
            raise ValueError("Invalid proof campaign ledger entry")
        if item["previous"] in by_previous:
            raise ValueError("Proof campaign ledger has conflicting entries")
        by_previous[item["previous"]] = item
    ledger = []
    previous = "0" * 64
    reserved = {}
    completed = set()
    while previous in by_previous:
        item = by_previous.pop(previous)
        ledger.append(item)
        previous = _hash(_json(item))
        attempt = item.get("attempt")
        if not isinstance(attempt, str) or not _ID.fullmatch(attempt):
            raise ValueError("Invalid proof attempt ID")
        if item.get("kind") == "reserve":
            if attempt in reserved:
                raise ValueError("Duplicate proof attempt")
            data = _source(path / "submissions" / f"{attempt}.lean")
            if _hash(data) != item.get("source_sha256"):
                raise ValueError("Proof submission failed integrity validation")
            reserved[attempt] = item
        elif item.get("kind") == "complete":
            if (
                attempt not in reserved
                or attempt in completed
                or item.get("target") != reserved[attempt].get("target")
            ):
                raise ValueError("Invalid proof attempt completion")
            if (
                item.get("status") not in {"accepted", "rejected", "incomplete"}
                or not isinstance(item.get("duration_seconds"), (int, float))
                or item["duration_seconds"] < 0
                or not isinstance(item.get("logs"), dict)
            ):
                raise ValueError("Invalid proof attempt result")
            for name, digest in item["logs"].items():
                if Path(name).name != name or _hash(_regular(path / "logs" / name)) != digest:
                    raise ValueError("Proof checker log failed integrity validation")
            completed.add(attempt)
        else:
            raise ValueError("Invalid proof campaign ledger entry")
    if isinstance(cached, dict):
        count = cached.get("length")
        valid_cache = (
            type(count) is int
            and 0 <= count <= len(ledger)
            and cached
            == {"length": count, "head": _hash(_json(ledger[count - 1])) if count else "0" * 64}
        )
    else:
        valid_cache = cached == ledger[: len(cached)]
    if by_previous or not valid_cache:
        raise ValueError("Proof campaign ledger failed integrity validation")
    return ledger


def _append(path, ledger, item):
    item = {**item, "previous": _hash(_json(ledger[-1])) if ledger else "0" * 64}
    raw = _json(item)
    entry = path / "entries" / f"{_hash(raw)}.json"
    fd = os.open(entry, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    ledger.append(item)
    evidence.write_record(path / "ledger.json", _json({"length": len(ledger), "head": _hash(raw)}))


def _usage(ledger):
    reserved = {entry["attempt"]: entry for entry in ledger if entry["kind"] == "reserve"}
    finished = {entry["attempt"]: entry for entry in ledger if entry["kind"] == "complete"}
    total = sum(
        finished[key]["duration_seconds"] if key in finished else _ATTEMPT_SECONDS
        for key in reserved
    )
    return reserved, finished, total


def _valid_checker_result(value, identity):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("status"), str)
        or value["status"] not in {"accepted", "rejected", "incomplete"}
        or value.get("identity") != identity
        or type(value.get("detail")) is not str
        or len(value["detail"]) > 4096
        or any(ord(char) < 32 and char not in "\n\r\t" for char in value["detail"])
        or not isinstance(value.get("logs"), dict)
    ):
        return False
    logs = value["logs"]
    return (
        all(
            isinstance(name, str)
            and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", name)
            and isinstance(data, bytes)
            for name, data in logs.items()
        )
        and len({name.casefold() for name in logs}) == len(logs)
        and sum(len(data) for data in logs.values()) <= _LIMIT
    )


def _task_text(manifest):
    return (
        "# Approved proof task\n\nEdit only Solution.lean in each requested target directory. "
        "Submit one or more target directories; all other files are rejected. "
        "The approved theorem and policy cannot be changed.\n\n"
        "If the approved specification needs amendment, seek separate review and renewed "
        "approval using this template:\n\n- Mismatch or counterexample:\n- Requested change:\n"
        "- Guarantee impact:\n- Witness impact:\n\n"
        f"Campaign: {manifest['campaign_id']}\n"
    ).encode()


def _targets(project):
    groups = {}
    all_targets = []
    for check in project.contract.formal:
        if check.engine != "lean-comparator":
            continue
        data = project.files[check.source]
        if len(data) > _LIMIT:
            raise ValueError("Approved proof source exceeds the size limit")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Approved proof source must be UTF-8") from exc
        policy = (check.allowed_imports, check.permitted_axioms)
        group = groups.setdefault(check.source, {"policy": policy, "theorems": []})
        if group["policy"] != policy:
            raise ValueError("Checks sharing a source must use the same Lean policy")
        group["theorems"].append(check.theorem)
        all_targets.append(
            {
                "id": check.id,
                "claim": check.claim,
                "source": check.source,
                "theorem": check.theorem,
                "allowed_imports": check.allowed_imports,
                "permitted_axioms": check.permitted_axioms,
                "approved_sha256": _hash(data),
            }
        )
    return all_targets, groups


def prepare(state: Path, project, candidate, approval: dict, out: Path, *, targets=None) -> dict:
    from . import proof_checker

    state = Path(state).resolve()
    out = Path(out).expanduser().absolute()
    resolved = out.resolve()
    for protected in (project.root.resolve(), state):
        if resolved.is_relative_to(protected) or protected.is_relative_to(resolved):
            raise ValueError(
                "Proof task directory must be outside the candidate and operator state"
            )
    project, candidate = _fresh(state, project, candidate, approval)
    identity = proof_checker.checker_identity()
    all_targets, groups = _targets(project)
    ids = [target["id"] for target in all_targets]
    selected = list(ids) if targets is None else targets
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(item, str) or item not in ids for item in selected)
        or len(selected) != len(set(selected))
    ):
        raise ValueError("Select distinct declared lean-comparator target IDs")
    selected = [item for item in ids if item in selected]
    with _lock(state / "proof-build.lock", blocking=False):
        for source, group in groups.items():
            audit = proof_checker.audit_approved(
                project.files[source],
                theorems=group["theorems"],
                allowed_imports=group["policy"][0],
                permitted_axioms=group["policy"][1],
            )
            if not _valid_checker_result(audit, identity):
                raise ValueError("Approved proof source audit returned invalid evidence")
            if audit["status"] != "accepted":
                raise ValueError(f"Approved proof source audit failed: {audit['detail']}")
    campaign_id = uuid4().hex
    manifest = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "repo": str(project.root),
        "base": project.base,
        "runner": project.runner,
        "revision": candidate.revision,
        "snapshot_digest": candidate.digest,
        "contract_digest": project.fingerprint,
        "verifier_digest": project.verifier_digest,
        "approval": approval,
        "checker_identity": identity,
        "targets": selected,
        "all_targets": all_targets,
        "obligations": selected,
        "dependencies": [],
        "completed_lemmas": "unavailable",
        "blockers": [],
        "usage": "unavailable",
    }
    task = _task_text(manifest)
    manifest["task_sha256"] = _hash(task)
    parent = state / "proof-campaigns"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipts = state / "proof-campaign-receipts"
    receipts.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent, prefix=".prepare-") as temp:
        staging = Path(temp) / "campaign"
        (staging / "approved").mkdir(parents=True, mode=0o700)
        (staging / "submissions").mkdir(mode=0o700)
        (staging / "entries").mkdir(mode=0o700)
        for target in all_targets:
            evidence.write_record(
                staging / "approved" / f"{target['id']}.lean", project.files[target["source"]]
            )
        raw = _json(manifest)
        evidence.write_record(staging / "TASK.md", task)
        evidence.write_record(staging / "manifest.json", raw)
        evidence.write_record(staging / "ledger.json", _json([]))
        tasks_staging = Path(tempfile.mkdtemp(dir=out.parent, prefix=".mentat-tasks-"))
        task_tree = tasks_staging / "tasks"
        task_tree.mkdir(mode=0o700)
        receipt_created = False
        campaign_created = False
        try:
            for target in all_targets:
                if target["id"] in selected:
                    directory = task_tree / target["id"]
                    directory.mkdir(mode=0o700)
                    evidence.write_record(
                        directory / "Solution.lean", project.files[target["source"]]
                    )
            evidence.write_record(task_tree / "TASK.md", task)
            evidence.write_record(task_tree / "manifest.json", raw)
            fd = os.open(
                receipts / campaign_id, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
            )
            receipt_created = True
            with os.fdopen(fd, "wb") as stream:
                stream.write(_hash(raw).encode())
                stream.flush()
                os.fsync(stream.fileno())
            staging.rename(parent / campaign_id)
            campaign_created = True
            _publish_directory(task_tree, out)
        except BaseException:
            if receipt_created:
                (receipts / campaign_id).unlink(missing_ok=True)
            if campaign_created:
                shutil.rmtree(parent / campaign_id)
            raise
        finally:
            shutil.rmtree(tasks_staging)
    return {
        "schema_version": 1,
        "status": "pass",
        "campaign_id": campaign_id,
        "targets": selected,
        "task_directory": str(out),
    }


def _submissions(directory, targets):
    directory = Path(directory)
    _directory(directory)
    entries = list(directory.iterdir())
    if not entries:
        raise ValueError("Proof submission must include at least one target")
    solutions = {}
    for entry in entries:
        if entry.name not in targets:
            raise ValueError(f"Unexpected proof submission path: {entry.name}")
        _directory(entry)
        children = list(entry.iterdir())
        if len(children) != 1 or children[0].name != "Solution.lean":
            raise ValueError(f"Target {entry.name} must contain only Solution.lean")
        solutions[entry.name] = _source(children[0])
    return solutions


def _run_attempt(path, manifest, ledger, target, solution, approved, *, recheck=False):
    from . import proof_checker

    reserved, finished, total = _usage(ledger)
    rounds = sum(
        1
        for item in reserved.values()
        if item["target"] == target["id"] and not item.get("recheck") and not item.get("denied")
    )
    exhausted = (not recheck and rounds >= _ROUNDS) or total + _ATTEMPT_SECONDS > _CAMPAIGN_SECONDS
    attempt = uuid4().hex
    evidence.write_record(path / "submissions" / f"{attempt}.lean", solution)
    _append(
        path,
        ledger,
        {
            "kind": "reserve",
            "attempt": attempt,
            "target": target["id"],
            "source_sha256": _hash(solution),
            "recheck": recheck,
            "denied": exhausted,
        },
    )
    if exhausted:
        record = {
            "kind": "complete",
            "attempt": attempt,
            "target": target["id"],
            "status": "incomplete",
            "detail": "Proof campaign attempt or time limit reached",
            "duration_seconds": 0.0,
            "logs": {},
        }
        _append(path, ledger, record)
        return record
    start = time.monotonic()
    try:
        checked = proof_checker.check_submission(
            approved,
            solution,
            theorem=target["theorem"],
            allowed_imports=target["allowed_imports"],
            permitted_axioms=target["permitted_axioms"],
            timeout_seconds=_ATTEMPT_SECONDS,
        )
        if (
            not _valid_checker_result(checked, manifest["checker_identity"])
            or checked.get("source_sha256") != _hash(solution)
            or checked.get("approved_sha256") != target["approved_sha256"]
            or checked.get("theorem") != target["theorem"]
        ):
            checked = {
                "status": "incomplete",
                "detail": "Proof checker returned invalid evidence",
                "logs": {},
            }
    except Exception:
        checked = {"status": "incomplete", "detail": "Proof checker could not complete", "logs": {}}
    duration = max(0.0, time.monotonic() - start)
    log_refs = {}
    for name, data in checked["logs"].items():
        key = f"{attempt}.{name}"
        evidence.write_record(path / "logs" / key, data)
        log_refs[key] = _hash(data)
    record = {
        "kind": "complete",
        "attempt": attempt,
        "target": target["id"],
        "status": checked["status"],
        "detail": checked["detail"],
        "duration_seconds": duration,
        "logs": log_refs,
    }
    _append(path, ledger, record)
    return record


def check(state: Path, campaign_id: str, submission: Path) -> dict:
    state = Path(state).resolve()
    path, manifest = _campaign(state, campaign_id)
    solutions = _submissions(submission, set(manifest["targets"]))
    with _lock(path / "ledger.lock"):
        path, manifest = _campaign(state, campaign_id)
        project, _ = _current(state, manifest)
        ledger = _ledger(path)
        targets = {target["id"]: target for target in manifest["all_targets"]}
        outcomes = []
        with _lock(state / "proof-build.lock", blocking=False):
            for target_id, solution in solutions.items():
                target = targets[target_id]
                outcome = _run_attempt(
                    path, manifest, ledger, target, solution, project.files[target["source"]]
                )
                outcomes.append({"id": target_id, **outcome})
        status = (
            "incomplete"
            if any(item["status"] == "incomplete" for item in outcomes)
            else "violation"
            if any(item["status"] == "rejected" for item in outcomes)
            else "pass"
        )
        return {
            "schema_version": 1,
            "status": status,
            "campaign_id": campaign_id,
            "targets": outcomes,
            "duration_seconds": _usage(ledger)[2],
            "obligations": manifest["obligations"],
            "dependencies": [],
            "completed_lemmas": "unavailable",
            "blockers": [],
            "usage": "unavailable",
        }


def recheck(state: Path, project, candidate, approval: dict, campaign_ids: list[str]):
    state = Path(state).resolve()
    project, candidate = _fresh(state, project, candidate, approval)
    if len(campaign_ids) != len(set(campaign_ids)):
        raise ValueError("Proof campaign IDs must be distinct")
    results, artifacts, seen = [], {}, set()
    for campaign_id in campaign_ids:
        path, _ = _campaign(state, campaign_id)
        with _lock(path / "ledger.lock"):
            # Bind the same manifest read that is checked; state may change while waiting.
            path, manifest = _campaign(state, campaign_id)
            if (
                manifest["repo"] != str(project.root)
                or manifest["base"] != project.base
                or manifest["runner"] != project.runner
                or manifest["revision"] != candidate.revision
                or manifest["snapshot_digest"] != candidate.digest
                or manifest["contract_digest"] != project.fingerprint
                or manifest["verifier_digest"] != project.verifier_digest
                or manifest["approval"] != approval
            ):
                raise ValueError("Proof campaign does not match this verification")
            _current(state, manifest)
            if seen.intersection(manifest["targets"]):
                raise ValueError("Proof campaigns have overlapping targets")
            seen.update(manifest["targets"])
            ledger = _ledger(path)
            targets = {item["id"]: item for item in manifest["all_targets"]}
            with _lock(state / "proof-build.lock", blocking=False):
                for target_id in manifest["targets"]:
                    target = targets[target_id]
                    reserved, finished, _ = _usage(ledger)
                    latest = next(
                        (
                            item
                            for item in reversed(ledger)
                            if item["kind"] == "reserve"
                            and item["target"] == target_id
                            and not item.get("recheck")
                        ),
                        None,
                    )
                    completion = finished.get(latest["attempt"]) if latest else None
                    outcome = {
                        "status": "incomplete",
                        "detail": "Latest proof submission is not accepted",
                    }
                    solution = (
                        _source(path / "submissions" / f"{latest['attempt']}.lean")
                        if latest
                        else None
                    )
                    if latest and _hash(solution) != latest["source_sha256"]:
                        raise ValueError("Proof submission failed integrity validation")
                    if completion and completion["status"] == "accepted":
                        outcome = _run_attempt(
                            path,
                            manifest,
                            ledger,
                            target,
                            solution,
                            project.files[target["source"]],
                            recheck=True,
                        )
                    result = {
                        "id": target_id,
                        "claim": target["claim"],
                        "kind": "lean-comparator",
                        "status": "pass" if outcome["status"] == "accepted" else "incomplete",
                        "detail": outcome["detail"],
                        "version": LEAN_VERSION,
                        "evidence": {
                            "campaign_id": campaign_id,
                            "theorem": target["theorem"],
                            "source_sha256": _hash(solution) if latest else None,
                            "approved_sha256": target["approved_sha256"],
                            "environment": manifest["checker_identity"],
                            "revision": manifest["revision"],
                            "base": manifest["base"],
                            "contract_digest": manifest["contract_digest"],
                            "verifier_digest": manifest["verifier_digest"],
                        },
                    }
                    if latest:
                        source_name = f"proof-{campaign_id}-{target_id}-Solution.lean"
                        artifacts[source_name] = solution
                        result.setdefault("artifacts", {})["proof-source"] = source_name
                    for name, digest in outcome.get("logs", {}).items():
                        data = _regular(path / "logs" / name)
                        if _hash(data) != digest:
                            raise ValueError("Proof checker log failed integrity validation")
                        key = f"proof-{campaign_id}-{name}"
                        artifacts[key] = data
                        result.setdefault("artifacts", {})[name] = key
                    results.append(result)
    return results, artifacts
