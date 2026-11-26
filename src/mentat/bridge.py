"""Local versioned JSONL bridge for reviewed Cockpit decisions and saved evidence."""

import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import evidence, evidence_projection, review_decisions, spec_review, verification
from .canonical import decode_json, json_bytes
from .decision import acceptable as _acceptable
from .decision import approval as _approval
from .decision import approval_path as _approval_path
from .decision import saved_run as _saved_run
from .decision import saved_run_snapshot as _saved_run_snapshot
from .decision import scope as _scope
from .gitio import verify_revision
from .project import load_project
from .project_formal import run_formal, tool_info
from .runner import run_behavior, runner_info

MAX_MESSAGE = 1_000_000
METHODS = (
    "initialize", "project.inspect", "specification.get", "approval.prepare", "approval.commit",
    "runs.list", "runs.get", "evidence.export", "evidence.compare", "evidence.inspect",
    "evidence.compareScoped", "acceptance.prepare",
    "acceptance.commit", "verification.start", "verification.cancel", "operation.get",
    "reviewDecision.prepare", "reviewDecision.commit", "reviewDecision.get",
)
SOURCE = "cockpit_local_operator"


def _string(params, key, *, maximum=4096):
    value = params.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise ValueError(f"Invalid {key}")
    return value


def _path(params, key):
    value = Path(_string(params, key))
    if not value.is_absolute():
        raise ValueError(f"{key} must be an absolute path")
    return value


def _head(repo):
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD^{commit}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=10, check=True,
    )
    return result.stdout.decode("ascii").strip()


def _claims(claims):
    return [{"id": item["id"], "statement": item["statement"],
             "assumptions": item.get("assumptions", [])} for item in claims]


def _project_claims(project):
    return _claims([claim.model_dump(mode="json") for claim in project.contract.claims])


def _assurance(state, record, manifest, revision=None):
    accepted, _ = evidence.acceptance_receipt(state, record, manifest)
    qualification = record.get("source_qualification_status")
    status = {"pass": "pass", "violation": "fail", "incomplete": "incomplete"}.get(
        record.get("status"), "incomplete"
    )
    if qualification == "violation":
        status = "fail"
    elif qualification in ("incomplete", "skipped") and status == "pass":
        status = "incomplete"
    eligible = False
    review_decision = None
    freshness = "historical" if revision is not None and revision != record["revision"] else "unknown"
    try:
        project = load_project(Path(record["repo"]), record["base"], record["runner"])
        if revision is not None:
            verify_revision(project.root, revision)
        same_scope = all(record.get(key) == value for key, value in _scope(project).items())
        if not same_scope:
            freshness = "historical" if revision is not None else "unknown"
        else:
            try:
                approval = _approval(state, project)
            except ValueError:
                freshness = "historical" if revision is not None else "unknown"
            else:
                same_approval = record.get("approval") == approval
                eligible = revision == record["revision"] and _acceptable(project, approval, record)
                if revision is not None:
                    freshness = "current" if revision == record["revision"] and same_approval else "historical"
                if revision == record["revision"] and same_approval:
                    evaluation = review_decisions.evaluation(project, approval, record, manifest)
                    review_decision = review_decisions.summary(review_decisions.journal(state), evaluation)
                    eligible = eligible and review_decision["status"] == "pending"
    except (OSError, ValueError, subprocess.SubprocessError):
        eligible = False
    result = {
        "status": status, "runId": record["run_id"], "revision": record["revision"],
        "base": record["base"], "contractDigest": record["contract_digest"],
        "eligible": eligible, "accepted": bool(accepted),
        "freshness": freshness,
        "claims": _claims(record.get("claims", [])),
        "method": "Approved formal and behavior checks",
        "detail": f"Product checks: {record['status']}" + (
            f"; source qualification: {qualification}" if qualification is not None else ""),
    }
    checks = [*record.get("model_checks", []),
              *(check for case in record.get("cases", []) for check in case.get("checks", []))]
    if record.get("behavior_skipped"):
        checks.extend({**item, "kind": "behavior", "status": "skipped",
                       "detail": "Behavior check did not run"}
                      for item in record.get("behavior_checks", []))
    for claim in result["claims"]:
        claim_checks = [{"id": check["id"], "kind": check.get("kind", "behavior"),
                         "status": check["status"], "detail": check.get("detail", "")}
                        for check in checks if check.get("claim") == claim["id"]]
        if claim_checks:
            claim["checks"] = claim_checks
            states = {check["status"] for check in claim_checks}
            claim["status"] = ("fail" if "violation" in states else
                               "incomplete" if states & {"incomplete", "skipped"} else "pass")
    if review_decision is not None:
        result["reviewDecision"] = review_decision
    if qualification is not None:
        result["qualification"] = qualification
    if accepted and isinstance(accepted.get("decision_source"), str):
        result["acceptanceSource"] = accepted["decision_source"]
    return result


def _export(bundle, record, manifest, out):
    # Open files relative to the verified directory without following replacement symlinks.
    directory = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    files = {}
    remaining = 32 * 1024 * 1024
    try:
        for name, expected in {"manifest.json": manifest, **record["artifacts"]}.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Export contains a non-regular file")
                data = stream.read(remaining + 1)
            if len(data) > remaining:
                raise ValueError("Evidence export exceeds size limit")
            remaining -= len(data)
            if evidence.digest(data) != expected:
                raise ValueError("Evidence changed during export")
            files[name] = data
    finally:
        os.close(directory)
    out.mkdir(mode=0o700)
    for name, data in files.items():
        with (out / name).open("xb") as stream:
            stream.write(data)


@contextmanager
def _operation_transition(directory):
    with (directory / "transition.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


class Bridge:
    def __init__(self, state, *, fixed_state=False, docker_context=None, notify=None, defer_notifications=False):
        self.state = Path(state)
        self.fixed_state = fixed_state
        self.instance = secrets.token_hex(16)
        self.tokens = {}
        self.docker_context = docker_context
        self.notify = notify or (lambda snapshot: None)
        self.workers = {}
        self.worker_lock = threading.RLock()
        self.closing = False
        self.defer_notifications = defer_notifications
        self.notification_gates = {}

    def state_for(self, params, project=None):
        path = _path(params, "state") if "state" in params else self.state
        if self.fixed_state and path.expanduser().resolve() != self.state.expanduser().resolve():
            raise ValueError("State conflicts with the configured bridge state")
        return evidence.state_directory(path, candidate_root=project.root if project else None)

    def project(self, params):
        repo, base, runner = _path(params, "repo"), _string(params, "base", maximum=64), _string(params, "runner", maximum=16)
        return load_project(repo, base, runner)

    def saved(self, params):
        state = self.state_for(params)
        run_id = _string(params, "runId", maximum=32)
        bundle, record, manifest = _saved_run_snapshot(state, run_id)
        if record.get("kind") != "project-verification":
            raise ValueError("Run is not a project verification")
        if any(key in params for key in ("repo", "base", "runner")):
            project = self.project(params)
            self.state_for(params, project)
            if any(record.get(k) != v for k, v in _scope(project).items() if k != "contract_digest"):
                raise ValueError("Run does not match project scope")
        return state, bundle, record, manifest

    def review(self, kind, params):
        project = self.project(params)
        state = self.state_for(params, project)
        if project.runner == "host" and params.get("hostConsent") is not True:
            raise ValueError("Host execution requires explicit consent")
        head = _head(project.root)
        revision = None
        saved = None
        if kind == "acceptance":
            revision = verify_revision(project.root, _string(params, "revision", maximum=64))
            saved = self.saved(params)
            approved = _approval(state, project)
            if saved[2]["revision"] != revision or not _acceptable(project, approved, saved[2]):
                raise ValueError("Run is not eligible for acceptance under this scope")
            review_decisions.check_acceptance(state, review_decisions.evaluation(project, approved, saved[2], saved[3]))
            saved = (saved[2]["run_id"], saved[3], approved)
        scope = {"repo": str(project.root), "base": project.base, "digest": project.fingerprint,
                 "runner": project.runner}
        if revision:
            scope["revision"] = revision
        artifacts = None
        if kind == "approval":
            artifacts = []
            base_paths = {f"base/{path}": path for path in project.contract.base_files}
            try:
                for name, data in sorted(project.files.items()):
                    path = base_paths.get(name, ".mentat/" + name)
                    content = data.decode("utf-8")
                    if any(not ch.isprintable() and ch not in "\n\r\t" for ch in content):
                        raise ValueError("Approval requires fully visible text files")
                    artifacts.append({"path": path, "content": content})
            except UnicodeDecodeError as exc:
                raise ValueError("Approval requires every contract file to be valid UTF-8") from exc
            summary = spec_review.summary(project)
            details = {"runner": project.runner, "lean_runs_on_host": summary["lean_runs_on_host"],
                       "host_requirements": summary["contract"].get("host_requirements"),
                       "claims_and_checks": summary["review"]}
            artifacts.append({"path": "Execution summary (derived)",
                              "content": json.dumps(details, ensure_ascii=False, indent=2)})
            if len(json_bytes(artifacts)) > MAX_MESSAGE - 100_000:
                raise ValueError("Approval review exceeds the full-content size limit")
        if len(self.tokens) >= 256:
            self.tokens = {k: v for k, v in self.tokens.items() if v[0] > time.monotonic()}
        if len(self.tokens) >= 256:
            raise ValueError("Too many pending decisions")
        token = secrets.token_urlsafe(32)
        result = {"token": token, "kind": kind, "scope": scope,
                "detail": (("Approve the reviewed contract and execution scope" if kind == "approval"
                            else "Accept the reviewed saved verification run")
                           + ("; reviewed code may execute on this host" if project.runner == "host" else "")),
                "expiresAt": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                "files": sorted(project.files)}
        if artifacts is not None:
            result["artifacts"] = artifacts
        if len(json_bytes(result)) > MAX_MESSAGE - 1024:
            raise ValueError("Approval review exceeds the full-content size limit")
        self.tokens[token] = (time.monotonic() + 300, kind, scope, str(state), head, saved)
        return result

    def commit(self, kind, params):
        if set(params) != {"token"}:
            raise ValueError("Commit accepts only a decision token")
        token = _string(params, "token", maximum=256)
        decision = self.tokens.pop(token, None)
        if decision is None or decision[0] <= time.monotonic() or decision[1] != kind:
            raise ValueError("Decision token is invalid, expired, or already used")
        _, _, scope, state_path, head, saved = decision
        project = load_project(Path(scope["repo"]), scope["base"], scope["runner"])
        state = evidence.state_directory(Path(state_path), candidate_root=project.root)
        if project.fingerprint != scope["digest"] or _head(project.root) != head:
            raise ValueError("Reviewed scope changed")
        if kind == "approval":
            record = {**_scope(project), "approved_at": evidence.now(), "decision_source": SOURCE}
            evidence.write_record(_approval_path(state, project), json_bytes(record))
            return {"approved": True, "source": SOURCE}
        with review_decisions.locked(state):
            project = load_project(Path(scope["repo"]), scope["base"], scope["runner"])
            if project.fingerprint != scope["digest"] or _head(project.root) != head:
                raise ValueError("Reviewed scope changed")
            run_id, manifest, approved = saved
            verify_revision(project.root, scope["revision"])
            _, record, current_manifest = _saved_run_snapshot(state, run_id)
            if (current_manifest != manifest or _approval(state, project) != approved
                    or record["revision"] != scope["revision"]
                    or not _acceptable(project, approved, record)):
                raise ValueError("Reviewed evidence or approval changed")
            review_decisions.check_acceptance(state, review_decisions.evaluation(project, approved, record, manifest))
            existing, present = evidence.acceptance_receipt(state, record, manifest)
            if present:
                if not existing:
                    raise ValueError("Existing acceptance receipt is invalid")
                return {"accepted": True, "source": (existing.get("decision_source")
                        if isinstance(existing.get("decision_source"), str)
                        and 0 < len(existing["decision_source"]) <= 128 else "unreported"),
                        "assurance": _assurance(state, record, manifest, scope["revision"])}
            receipt = {"run_id": run_id, "manifest_digest": manifest, "revision": record["revision"],
                       "contract_digest": project.fingerprint, "accepted_at": evidence.now(),
                       "decision_source": SOURCE}
            evidence.write_record(state / "acceptances" / f"{run_id}.json", json_bytes(receipt))
            return {"accepted": True, "source": SOURCE,
                    "assurance": _assurance(state, record, manifest, scope["revision"])}

    def review_decision_prepare(self, params):
        project = self.project(params)
        state = self.state_for(params, project)
        if project.runner == "host" and params.get("hostConsent") is not True:
            raise ValueError("Host execution requires explicit consent")
        request_id = _string(params, "requestId", maximum=128)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            raise ValueError("Invalid requestId")
        action = _string(params, "action", maximum=16)
        if action not in {"reject", "defer", "resume"}:
            raise ValueError("Invalid review action")
        reason = params.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 2000 or "\x00" in reason or (action != "resume" and not reason.strip()):
            raise ValueError("Invalid review reason")
        revision = verify_revision(project.root, _string(params, "revision", maximum=64))
        _, _, record, manifest = self.saved(params)
        approved = _approval(state, project)
        if (record["revision"] != revision or record.get("approval") != approved
                or any(record.get(k) != v for k, v in _scope(project).items())):
            raise ValueError("Run does not match current approval and scope")
        evaluation = review_decisions.evaluation(project, approved, record, manifest)
        request = {"requestId": request_id, "action": action, "reason": reason,
                   "actor": SOURCE, "evaluation": evaluation}
        with review_decisions.locked(state):
            entries = review_decisions.journal(state)
            prior = next ( (e for e in entries if e["requestId"] == request_id), None)
            if prior:
                if any(prior[k] != v for k, v in request.items()):
                    raise ValueError("Request ID already used for another decision")
                return {"receipt": prior}
            current = review_decisions.status(entries, evaluation)
            if (action == "resume" and current != "defer") or (action != "resume" and current != "pending"):
                raise ValueError("Review action is not valid for the current decision")
            _, present = evidence.acceptance_receipt(state, record, manifest)
            if present:
                raise ValueError("Acceptance already recorded or invalid")
        if len(self.tokens) >= 256:
            self.tokens = {k: v for k, v in self.tokens.items() if v[0] > time.monotonic()}
        if len(self.tokens) >= 256:
            raise ValueError("Too many pending decisions")
        token = secrets.token_urlsafe(32)
        self.tokens[token] = (time.monotonic() + 300, "reviewDecision", str(state), _head(project.root), request)
        return {"token": token, "kind": "reviewDecision", "scope": evaluation["scope"],
                "evaluation": evaluation, "action": action, "reason": reason, "requestId": request_id,
                "expiresAt": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()}

    def review_decision_commit(self, params):
        if set(params) != {"token"}:
            raise ValueError("Commit accepts only a decision token")
        token = _string(params, "token", maximum=256)
        decision = self.tokens.pop(token, None)
        if decision is None or decision[0] <= time.monotonic() or decision[1] != "reviewDecision":
            raise ValueError("Decision token is invalid, expired, or already used")
        _, _, state_path, head, request = decision
        evaluation = request["evaluation"]
        scope = evaluation["scope"]
        project = load_project(Path(scope["repo"]), scope["base"], scope["runner"])
        state = evidence.state_directory(Path(state_path), candidate_root=project.root)
        with review_decisions.locked(state):
            project = load_project(Path(scope["repo"]), scope["base"], scope["runner"])
            entries = review_decisions.journal(state)
            prior = next((e for e in entries if e["requestId"] == request["requestId"]), None)
            if prior:
                if any(prior[k] != v for k, v in request.items()):
                    raise ValueError("Request ID already used for another decision")
                return {"receipt": prior}
            verify_revision(project.root, evaluation["revision"])
            _, record, manifest = _saved_run_snapshot(state, evaluation["runId"])
            approved = _approval(state, project)
            if (_head(project.root) != head
                    or review_decisions.evaluation(project, approved, record, manifest) != evaluation
                    or record.get("approval") != approved):
                raise ValueError("Reviewed scope, evidence or approval changed")
            current = review_decisions.status(entries, evaluation)
            action = request["action"]
            if (action == "resume" and current != "defer") or (action != "resume" and current != "pending"):
                raise ValueError("Review action is not valid for the current decision")
            _, present = evidence.acceptance_receipt(state, record, manifest)
            if present:
                raise ValueError("Acceptance already recorded or invalid")
            receipt = {**request, "committedAt": evidence.now()}
            review_decisions.save(state, [*entries, receipt])
            return {"receipt": receipt}

    def review_decision_get(self, params):
        state = self.state_for(params)
        request_id = _string(params, "requestId", maximum=128)
        run_id = _string(params, "runId", maximum=32)
        with review_decisions.locked(state):
            receipt = next((e for e in review_decisions.journal(state)
                            if e["requestId"] == request_id), None)
            if receipt is None or receipt["evaluation"]["runId"] != run_id:
                raise ValueError("Review decision receipt not found")
            return {"receipt": receipt}

    def operation_path(self, params):
        operation_id = _string(params, "operationId", maximum=128)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id):
            raise ValueError("Invalid operationId")
        state = self.state_for(params)
        return operation_id, state, state / "bridge-operations" / operation_id

    def operation_snapshot(self, operation_id, directory):
        path = directory / "receipt.json"
        if not path.exists():
            evidence.write_record(path, json_bytes({"operationId": operation_id,
                "status": "interrupted", "error": "Operation reservation has unknown scope"}))
        receipt = evidence.read_json(path)
        if receipt.get("operationId") != operation_id:
            raise ValueError("Invalid operation receipt")
        status = receipt.get("status")
        if status in ("running", "cancel_requested"):
            with (directory / "owner.lock").open("rb") as owner:
                try:
                    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if (directory / "cancel").exists():
                        receipt["status"] = "cancel_requested"
                else:
                    receipt.update(status="interrupted", error="Bridge stopped before verification finished")
                    evidence.write_record(path, json_bytes(receipt))
        snapshot = {key: receipt[key] for key in ("operationId", "status", "runId", "error") if key in receipt}
        if receipt.get("status") == "completed" and "runId" in receipt:
            try:
                scope = receipt["scope"]
                state = directory.parent.parent
                _, record, manifest = _saved_run_snapshot(state, receipt["runId"])
                if (record.get("kind") != "project-verification"
                        or any(record.get(key) != scope[key] for key in
                               ("repo", "base", "runner", "contract_digest", "revision"))):
                    raise ValueError("Operation evidence does not match its scope")
                assurance = _assurance(state, record, manifest, scope["revision"])
                if len(json_bytes(assurance)) <= MAX_MESSAGE // 2:
                    snapshot["assurance"] = assurance
            except Exception:
                # The operation remains completed even when its evidence is unavailable.
                pass
        return snapshot

    def operation_get(self, params):
        operation_id, _, directory = self.operation_path(params)
        with (directory.parent / ".reservation.lock").open("a+b") as reservation:
            fcntl.flock(reservation, fcntl.LOCK_EX)
            with self.worker_lock, _operation_transition(directory):
                return self.operation_snapshot(operation_id, directory)

    def operation_start(self, params):
        operation_id, state, directory = self.operation_path(params)
        project = self.project(params)
        self.state_for(params, project)
        revision = verify_revision(project.root, _string(params, "revision", maximum=64))
        consent = params.get("hostConsent", False)
        if project.runner == "host" and consent is not True:
            raise ValueError("Host execution requires explicit consent")
        binding = {**_scope(project), "revision": revision, "state": str(state),
                   "hostConsent": consent, "dockerContext": self.docker_context}
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (directory.parent / ".reservation.lock").open("a+b") as reservation:
            fcntl.flock(reservation, fcntl.LOCK_EX)
            try:
                directory.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                created = False
            with self.worker_lock, _operation_transition(directory):
                path = directory / "receipt.json"
                if not created or path.exists():
                    if not path.exists():
                        self.operation_snapshot(operation_id, directory)
                    receipt = evidence.read_json(path)
                    if receipt.get("scope") != binding:
                        raise ValueError("Operation ID is already bound to a different scope or its scope is unknown")
                    return {"operationId": operation_id}
                owner = (directory / "owner.lock").open("x+b")
                fcntl.flock(owner, fcntl.LOCK_EX)
                try:
                    approved = _approval(state, project)
                    receipt = {"operationId": operation_id, "scope": binding, "status": "running"}
                    evidence.write_record(path, json_bytes(receipt))
                    gate = threading.Event()
                    if not self.defer_notifications:
                        gate.set()
                    self.notification_gates[(str(state), operation_id)] = gate
                    worker = threading.Thread(target=self.operation_run,
                                              args=(directory, receipt, approved, owner), daemon=True)
                    self.workers[(str(state), operation_id)] = worker
                    worker.start()
                except Exception:
                    self.workers.pop((str(state), operation_id), None)
                    self.notification_gates.pop((str(state), operation_id), None)
                    evidence.write_record(path, json_bytes(
                        {"operationId": operation_id, "scope": binding, "status": "failed",
                         "error": "Verification could not be started"}))
                    owner.close()
                    raise
        return {"operationId": operation_id}

    def operation_run(self, directory, receipt, approved, owner):
        scope = receipt["scope"]
        state = Path(scope["state"])

        def checkpoint():
            if (directory / "cancel").exists():
                raise InterruptedError("Verification cancellation requested")

        def approval(current_state, project):
            checkpoint()
            current = _approval(current_state, project)
            if _scope(project) != {key: scope[key] for key in ("repo", "base", "runner", "contract_digest")} or current != approved:
                raise ValueError("Approved verification scope changed")
            return current

        def checked(function):
            def invoke(*args, **kwargs):
                checkpoint()
                return function(*args, **kwargs)
            return invoke

        try:
            record = verification.verify(
                Path(scope["repo"]), scope["base"], scope["revision"], scope["runner"], None,
                state_root=state, docker_context=scope["dockerContext"], read_approval=approval,
                scope=_scope, tool_info=checked(tool_info), run_formal=checked(run_formal),
                runner_info=checked(runner_info), run_behavior=checked(run_behavior),
                checkpoint=checkpoint,
            )
            run_id = record["run_id"]
            _, verified, _ = _saved_run_snapshot(state, run_id)
            if verified.get("kind") != "project-verification":
                raise ValueError("Unsupported saved verification record")
            outcome = {"status": "completed", "runId": run_id}
        except Exception as exc:
            outcome = {"status": "failed", "error": (str(exc) or "Verification failed")[:1000]}
        try:
            with self.worker_lock, _operation_transition(directory):
                current = evidence.read_json(directory / "receipt.json")
                if current.get("status") in ("interrupted", "incomplete"):
                    return
                if (directory / "cancel").exists():
                    outcome = {"status": "incomplete", "error": "Cancellation requested; check termination is not established"}
                current.update(outcome)
                evidence.write_record(directory / "receipt.json", json_bytes(current))
                should_notify = not self.closing
            if should_notify:
                self.notification_gates[(str(state), receipt["operationId"])].wait()
                if not self.closing:
                    self.notify(self.operation_get({"state": str(state),
                                                    "operationId": receipt["operationId"]}))
        finally:
            owner.close()
            with self.worker_lock:
                self.workers.pop((str(state), receipt["operationId"]), None)
                self.notification_gates.pop((str(state), receipt["operationId"]), None)

    def operation_cancel(self, params):
        operation_id, _, directory = self.operation_path(params)
        with (directory.parent / ".reservation.lock").open("a+b") as reservation:
            fcntl.flock(reservation, fcntl.LOCK_EX)
            with self.worker_lock, _operation_transition(directory):
                snapshot = self.operation_snapshot(operation_id, directory)
                if snapshot["status"] in ("running", "cancel_requested"):
                    try:
                        fd = os.open(directory / "cancel", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    except FileExistsError:
                        pass
                    else:
                        os.close(fd)
                    snapshot["status"] = "cancel_requested"
                    if not self.closing:
                        self.notify(snapshot)
                return snapshot

    def close(self):
        with self.worker_lock:
            self.closing = True
            for gate in self.notification_gates.values():
                gate.set()
            workers = list(self.workers.items())
        for (state, operation_id), _ in workers:
            self.operation_cancel({"operationId": operation_id, "state": state})
        deadline = time.monotonic() + 2
        for _, worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        with self.worker_lock:
            for (state, operation_id), worker in workers:
                if worker.is_alive():
                    path = Path(state) / "bridge-operations" / operation_id / "receipt.json"
                    with _operation_transition(path.parent):
                        receipt = evidence.read_json(path)
                        if receipt.get("status") in ("running", "cancel_requested"):
                            receipt.update(status="incomplete", error="Bridge closed before verification stopped")
                            evidence.write_record(path, json_bytes(receipt))

    def call(self, method, params):
        scope = {"repo", "base", "runner", "state"}
        allowed = {
            "initialize": {"version"},
            "verification.start": scope | {"operationId", "revision", "hostConsent"},
            "verification.cancel": {"operationId", "state"},
            "operation.get": {"operationId", "state"},
            "project.inspect": scope, "specification.get": scope,
            "approval.prepare": scope | {"hostConsent"}, "approval.commit": {"token"},
            "runs.list": scope, "runs.get": scope | {"runId", "revision"},
            "evidence.export": scope | {"runId", "out"},
            "evidence.compare": scope | {"before", "after"},
            "evidence.inspect": scope | {"runId", "revision", "contractDigest"},
            "evidence.compareScoped": {"repo", "state", "before", "after", "acrossContracts"},
            "acceptance.prepare": scope | {"runId", "revision", "hostConsent"},
            "acceptance.commit": {"token"},
            "reviewDecision.prepare": scope | {"runId", "revision", "hostConsent", "action", "reason", "requestId"},
            "reviewDecision.commit": {"token"},
            "reviewDecision.get": {"runId", "requestId", "state"},
        }
        if method not in allowed:
            raise NotImplementedError("Unsupported method")
        if set(params) - allowed[method]:
            raise ValueError("Unexpected method parameters")
        if "hostConsent" in params and not isinstance(params["hostConsent"], bool):
            raise ValueError("Invalid host consent")
        if method == "initialize":
            if type(params.get("version")) is not int or params["version"] != 1:
                raise ValueError("Unsupported protocol version")
            return {"version": 1, "capabilities": list(METHODS), "instanceId": self.instance}
        if method == "reviewDecision.prepare":
            return self.review_decision_prepare(params)
        if method == "reviewDecision.commit":
            return self.review_decision_commit(params)
        if method == "reviewDecision.get":
            return self.review_decision_get(params)
        if method == "verification.start":
            return self.operation_start(params)
        if method == "verification.cancel":
            return self.operation_cancel(params)
        if method == "operation.get":
            return self.operation_get(params)
        if method in ("approval.commit", "acceptance.commit"):
            return self.commit(method.split(".")[0], params)
        if method in ("approval.prepare", "acceptance.prepare"):
            return self.review(method.split(".")[0], params)
        if method in ("project.inspect", "specification.get"):
            try:
                project = self.project(params)
                state = self.state_for(params, project)
                try:
                    approval = _approval(state, project)
                except (ValueError, OSError):
                    approval = None
                claims = _project_claims(project)
                result = {"claims": claims, "files": sorted(project.files), "digest": project.fingerprint}
                if method == "specification.get":
                    return result
                result.update(status="approved" if approval else "needs-approval", runner=project.runner)
                if approval and approval.get("decision_source"):
                    result["approvedSource"] = approval["decision_source"]
                return result
            except (ValueError, OSError) as exc:
                if method == "specification.get":
                    raise
                return {"status": "unconfigured" if str(exc) == "base has no .mentat/contract.json" else "error", "claims": [], "runner": params.get("runner") if params.get("runner") in ("host", "docker") else "docker",
                        "detail": str(exc)[:1000]}
        if method == "runs.list":
            state = self.state_for(params)
            runs = []
            project = self.project(params) if any(k in params for k in ("repo", "base", "runner")) else None
            if project:
                self.state_for(params, project)
            directory = state / "runs"
            for path in sorted(directory.iterdir()) if directory.exists() else []:
                if not evidence.RUN_ID.fullmatch(path.name):
                    continue
                _, record = _saved_run(state, path.name)
                if record.get("kind") != "project-verification":
                    continue
                if project and any(record.get(k) != v for k, v in _scope(project).items() if k != "contract_digest"):
                    continue
                runs.append({"runId": path.name, "revision": record["revision"], "status": record["status"]})
                if len(runs) > 1000:
                    raise ValueError("Too many runs to list")
            return {"runs": runs}
        if method in ("runs.get", "evidence.export"):
            state, bundle, record, manifest = self.saved(params)
            if method == "runs.get":
                revision = params.get("revision")
                if revision is not None:
                    revision = verify_revision(Path(record["repo"]), _string(params, "revision", maximum=64))
                return {"assurance": _assurance(state, record, manifest, revision), "record": record}
            out = _path(params, "out")
            _export(bundle, record, manifest, out)
            return {"path": str(out.resolve()), "runId": record["run_id"]}
        if method == "evidence.inspect":
            ref = {key: params.get(key) for key in evidence_projection.REF_KEYS}
            return evidence_projection.inspect(self.state_for(params), _string(params, "repo"), ref)
        if method == "evidence.compareScoped":
            return evidence_projection.compare(self.state_for(params), _string(params, "repo"),
                                               params.get("before"), params.get("after"),
                                               params.get("acrossContracts", False))
        if method == "evidence.compare":
            before, after = _string(params, "before", maximum=32), _string(params, "after", maximum=32)
            _, before_path, before_record, _ = self.saved({**params, "runId": before})
            _, after_path, after_record, _ = self.saved({**params, "runId": after})
            return evidence.compare_saved_runs(before_path, before_record, after_path, after_record)
        raise NotImplementedError("Unsupported method")


def serve(state, stdin=None, stdout=None, *, fixed_state=False, docker_context=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    output_lock = threading.Lock()

    def send(data):
        encoded = json_bytes(data)
        if len(encoded) > MAX_MESSAGE:
            raise ValueError("Notification exceeds size limit")
        with output_lock:
            stdout.write(encoded)
            stdout.flush()

    def notify(snapshot):
        try:
            send({"method": "operation.updated", "params": snapshot})
        except (OSError, ValueError):
            pass

    bridge = Bridge(state, fixed_state=fixed_state, docker_context=docker_context, notify=notify,
                    defer_notifications=True)
    try:
        while True:
            line = stdin.readline(MAX_MESSAGE + 1)
            if not line:
                return
            request_id = None
            try:
                if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
                    if len(line) > MAX_MESSAGE:
                        while line and not line.endswith(b"\n"):
                            line = stdin.readline(MAX_MESSAGE + 1)
                    raise ValueError("Invalid or oversized request")
                request = decode_json(line)
                if not isinstance(request, dict) or set(request) != {"id", "method", "params"}:
                    raise ValueError("Invalid request envelope")
                request_id = request["id"]
                if (not isinstance(request_id, (str, int)) or isinstance(request_id, bool)
                        or isinstance(request_id, str) and len(request_id) > 256
                        or not isinstance(request["method"], str)
                        or not isinstance(request["params"], dict)):
                    raise ValueError("Invalid request envelope")
                result = bridge.call(request["method"], request["params"])
                response = {"id": request_id, "result": result}
                encoded = json_bytes(response)
                if len(encoded) > MAX_MESSAGE:
                    raise ValueError("Response exceeds size limit")
            except Exception as exc:
                code = "unsupported" if isinstance(exc, NotImplementedError) else "invalid_request"
                message = str(exc)[:1000] or "Request failed"
                encoded = json_bytes({"id": request_id, "error": {"code": code, "message": message}})
            with output_lock:
                stdout.write(encoded)
                stdout.flush()
            if isinstance(request_id, (str, int)) and "result" in locals() and isinstance(result, dict):
                operation_id = result.get("operationId")
                if operation_id is not None:
                    with bridge.worker_lock:
                        for (_, known_id), gate in bridge.notification_gates.items():
                            if known_id == operation_id:
                                gate.set()
            result = None
    finally:
        bridge.close()
