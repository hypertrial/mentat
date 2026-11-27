"""Bounded, qualified public projection of a saved autonomy session."""

import hashlib
import json
import math
import os
import re
import selectors
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from . import process
from .autonomy import digest, project_snapshot
from .autonomy_policy import load_policy_path
from .autonomy_state import IntegrationCapacityError

_FIELDS = {
    "session": ("id", "status", "historical_status", "base", "head", "started_at", "deadline"),
    "requirements": ("id", "area", "statement", "origin", "citations", "spec_path", "status", "freshness"),
    "claims": ("id", "requirement_ids", "statement", "assumptions", "status", "freshness", "evidence_ids", "formal_meaning", "formal_contract", "proof_scope", "declared_status"),
    "jobs": ("id", "loop", "role", "state", "status", "summary", "attempt", "item_id", "input_digest", "operation", "target_head", "review_cohort", "author_id", "outcome_status", "outcome_decision", "diagnostic"),
    "findings": ("id", "statement", "summary", "status", "severity", "requirement_ids"),
    "decisions": ("id", "statement", "summary", "status", "decision", "review_kind", "reviewer_id", "author_id", "job_id", "head", "base", "diff_digest", "review_cohort", "check_evidence_id", "check_key", "source_batch_id", "input_manifest_sha256"),
    "evidence": ("id", "statement", "summary", "status", "freshness", "kind", "claim_ids", "requirement_ids", "claim_id", "method", "assumptions", "source_identity", "contract", "precondition", "violation", "reason", "solver_version", "python_version", "python_implementation", "solver_binding_sha256", "solver_library_sha256", "frontend_sha256", "contract_sha256", "counterexample", "head", "base", "check_key", "input_digest", "policy_digest", "tool_digest", "check_evidence_id"),
}
_NESTED = frozenset(("id", "title", "label", "source", "reference", "text", "status", "method", "revision", "assumptions", "claim_assumptions", "qualification", "requires", "ensures", "path", "function", "inputs", "source_sha256", "blob_oid"))
_SECRET = re.compile(r"(?i)(?:bearer\s+\S+|(?:token|secret|password|credential|api[_-]?key)\s*[:=]\s*\S+|(?:gh[opsu]_[A-Za-z0-9_]+)|(?:sk-[A-Za-z0-9_-]+)|(?:/(?:Users|home|private|tmp|var)/[^\s,;]+))")
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _run_child(argv: list[str], *, cwd: str | Path, env: dict[str, str], limit: int, timeout: float) -> bytes:
    # Inherit the projection worker's process group: its supervisor kills the
    # complete group if the overall deadline expires.
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        assert child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("observation timed out")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > limit:
                            raise ValueError("observation exceeded capacity")
            if child.wait(timeout=max(0.01, deadline - time.monotonic())):
                raise ValueError("observation unavailable")
        return bytes(output)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        if child.stdout is not None:
            child.stdout.close()


def _git(repo: Path, *args: str) -> str:
    env = {"PATH": os.defpath, "HOME": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    output = _run_child(["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "diff.external=", "-c", "credential.helper=", *args], cwd=repo, env=env, limit=1_000_000, timeout=0.5)
    return output.decode("utf-8", "strict").strip()


def _identity() -> dict:
    bootstrap = ("import sys,types; p=types.ModuleType('mentat'); "
                 "p.__path__=[sys.argv[1]]; sys.modules['mentat']=p; "
                 "from mentat.source_verify import _identity_main; _identity_main()")
    output = _run_child([sys.executable, "-I", "-B", "-c", bootstrap, str(Path(__file__).resolve().parent)], cwd="/", env={}, limit=16384, timeout=2)
    result = json.loads(output)
    fields = {"python_version", "python_implementation", "solver_version", "solver_binding_sha256", "solver_library_sha256"}
    if not isinstance(result, dict) or set(result) != fields or any(not isinstance(result[k], str) or not result[k] for k in fields):
        raise ValueError("identity unavailable")
    if any(not re.fullmatch(r"[0-9a-f]{64}", result[key]) for key in ("solver_binding_sha256", "solver_library_sha256")):
        raise ValueError("identity unavailable")
    result["frontend_sha256"] = hashlib.sha256(Path(__file__).with_name("source_verify.py").read_bytes()).hexdigest()
    from .autonomy_runtime import execution_identity

    result["execution_sha256"] = execution_identity()
    return result

def _check_git_config(repo: Path) -> None:
    # Includes and worktree config may introduce executable filters outside the
    # file inspected here. Fail closed before invoking status in their presence.
    config = _git(repo, "config", "--local", "--no-includes", "--null", "--list")
    for entry in config.split("\0"):
        key, _, value = entry.partition("\n")
        key = key.lower()
        if key.startswith(("include.", "includeif.", "filter.")) or key == "core.worktree" or key == "extensions.worktreeconfig" and value.lower() not in ("", "false", "no", "off", "0"):
            raise ValueError("unsafe repository configuration")


def _observe(repo: Path, store_path: Path | None) -> tuple:
    _check_git_config(repo)
    head = _git(repo, "rev-parse", "--verify", "HEAD")
    dirty = bool(_git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--no-renames"))
    policy = digest(load_policy_path(store_path)) if store_path is not None else None
    identity = _identity()
    return head, dirty, policy, identity


def github_source_base(remote: str) -> str | None:
    match = re.fullmatch(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]+)", remote)
    if not match:
        return None
    owner, repo = match.groups()
    repo = repo.removesuffix(".git")
    return f"https://github.com/{owner}/{repo}" if repo not in ("", ".", "..") else None


def _path(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 1024 and not any(c in value for c in ("\\", ":", "%")) and not any(ord(c) < 32 or ord(c) >= 0xD800 and ord(c) <= 0xDFFF for c in value) and all(part not in ("", ".", "..") and part.lower() != ".git" for part in value.split("/"))


def _clean(value: object, feedback: list[str], depth: int = 0) -> object:
    if depth > 6:
        raise ValueError("nested value omitted")
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("nonfinite value")
        return value
    if isinstance(value, str):
        if len(value) > 4096 or _SECRET.search(value) or any(s and s in value for s in feedback):
            raise ValueError("private or oversized text omitted")
        return value
    if isinstance(value, list):
        if len(value) > 128:
            raise ValueError("oversized list omitted")
        return [_clean(item, feedback, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 128:
            raise ValueError("oversized object omitted")
        if "path" in value and not _path(value["path"]):
            raise ValueError("invalid relative path")
        return {k: _clean(v, feedback, depth + 1) for k, v in value.items() if k in _NESTED}
    raise ValueError("unsupported value omitted")


def _record(item: object, fields: tuple[str, ...], feedback: list[str], source_base: str | None) -> dict:
    if not isinstance(item, dict):
        return {"id": "unavailable", "status": "incomplete", "freshness": "unknown", "unavailable_fields": ["record"]}
    result: dict = {}
    omitted = []
    for key in fields:
        if key not in item:
            continue
        try:
            value = item[key]
            if key in ("spec_path",) and not _path(value):
                raise ValueError("invalid path")
            if key == "citations":
                if not isinstance(value, list) or len(value) > 128:
                    raise ValueError("invalid citations")
                cleaned = []
                for citation in value:
                    if not isinstance(citation, dict):
                        raise ValueError("invalid citation")
                    part = _clean(citation, feedback)
                    if not isinstance(part, dict):
                        raise ValueError("invalid citation")
                    path, rev = citation.get("path"), citation.get("revision")
                    if path is not None and not _path(path):
                        raise ValueError("invalid citation path")
                    if source_base and isinstance(path, str) and _path(path) and isinstance(rev, str) and re.fullmatch(r"[0-9a-f]{40}", rev):
                        part["href"] = f"{source_base}/blob/{rev}/{quote(path, safe='/')}"
                    cleaned.append(part)
                result[key] = cleaned
            elif key in ("formal_contract", "contract") and isinstance(value, dict):
                result[key] = _clean(value, feedback)
                inputs = value.get("inputs")
                if isinstance(inputs, dict):
                    if len(inputs) > 128 or any(not isinstance(k, str) or not _ID.fullmatch(k) or re.search(r"token|secret|password|credential|key", k, re.I) or v not in ("int", "bool") for k, v in inputs.items()):
                        raise ValueError("invalid inputs")
                    result[key]["inputs"] = dict(inputs)
            elif key == "counterexample":
                if not isinstance(value, dict) or len(value) > 32 or any(not isinstance(k, str) or not _ID.fullmatch(k) or re.search(r"token|secret|password|credential|key", k, re.I) or not (type(v) is bool or type(v) is int and v.bit_length() <= 256) for k, v in value.items()):
                    raise ValueError("invalid counterexample")
                result[key] = dict(value)
            else:
                result[key] = _clean(value, feedback)
        except (ValueError, TypeError):
            result.pop(key, None)
            omitted.append(key)
    result.setdefault("id", "unavailable")
    result.setdefault("status", "unavailable")
    result.setdefault("freshness", "unknown")
    result["unavailable_fields"] = sorted(set(omitted + [key for key in fields if key not in item]))
    if any(k in omitted for k in ("proof_scope", "formal_contract", "assumptions")):
        result["recorded_status"] = result["status"]
        result["status"] = "incomplete"
        result["freshness"] = "unknown"
    return result



def _qualify_proof(record: dict) -> None:
    """Never present proof without its full, disclosed qualification."""
    missing = []
    if record.get("method") != "source-proof":
        missing.append("method")
    if record.get("precondition") != "sat":
        missing.append("precondition")
    if record.get("violation") != "unsat":
        missing.append("violation")
    contract = record.get("contract")
    if not isinstance(contract, dict) or not isinstance(contract.get("inputs"), dict) or any(not isinstance(contract.get(k), str) or not contract[k] for k in ("requires", "ensures")):
        missing.append("contract")
    source = record.get("source_identity")
    if not isinstance(source, dict) or any(not isinstance(source.get(k), str) or not source[k] for k in ("path", "function", "revision", "blob_oid", "source_sha256")):
        missing.append("source_identity")
    assumptions = record.get("assumptions")
    if not isinstance(assumptions, list) or not assumptions or any(not isinstance(item, str) or not item for item in assumptions):
        missing.append("assumptions")
    for key in ("frontend_sha256", "contract_sha256", "solver_version", "solver_binding_sha256", "solver_library_sha256", "python_version", "python_implementation"):
        if not isinstance(record.get(key), str) or not record[key]:
            missing.append(key)
    if missing:
        record["recorded_status"] = "proved"
        record["status"] = "incomplete"
        if record.get("freshness") == "current":
            record["freshness"] = "unknown"
        record["unavailable_fields"] = sorted(set(record["unavailable_fields"] + missing))

def _project_inner(raw: dict, *, store_path: Path | None = None) -> dict:
    """Project only authorized fields; uncertain observations invalidate freshness."""
    snapshot = raw
    session = raw["session"]
    observed_at = time.time()
    before = after = None
    try:
        repo = Path(session["repo"])
        _check_git_config(repo)
        source_base = github_source_base(_git(repo, "config", "--get", "remote.origin.url"))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        source_base = None
    try:
        before = _observe(Path(session["repo"]), store_path)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        pass
    authorities = [e.get("payload", {}).get("digest") for e in snapshot.get("events", []) if e.get("kind") == "authority_pinned"]
    try:
        after = _observe(Path(session["repo"]), store_path)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        pass
    tools = [e.get("payload", {}).get("identity") for e in snapshot.get("events", []) if e.get("kind") == "toolchain_pinned"]
    current = before is not None and before == after and not before[1] and before[0] == session.get("head") and authorities == [before[2]] and tools == [before[3]] and session.get("status") != "invalidated"
    if not current:
        # project_snapshot makes its own deep copy. Copy only the fields changed
        # before that call, so the caller's capture remains untouched.
        snapshot = {**raw, "session": {**session, "historical_status": session.get("status"), "status": "invalidated"}}
        for key in ("requirements", "claims", "evidence"):
            snapshot[key] = [{**record, "freshness": "stale"} for record in raw.get(key, [])]
    projected = project_snapshot(snapshot, identity=before[3] if current and before is not None else {})
    for evidence in projected.get("evidence", []):
        source = evidence.get("source_identity")
        if isinstance(source, dict) and source.get("revision") and (not before or source["revision"] != before[0]):
            evidence["freshness"] = "stale"
    for claim in projected.get("claims", []):
        scope = claim.get("proof_scope", {})
        if scope.get("revision") and (not before or scope["revision"] != before[0]):
            claim["freshness"] = "stale"
    feedback = [e.get("payload", {}).get("message") for e in snapshot.get("events", []) if e.get("kind") == "human_feedback" and isinstance(e.get("payload", {}).get("message"), str)]
    result = {"session": _record(raw["session"], _FIELDS["session"], feedback, source_base), "freshness": {"status": "current" if current else "unknown", "observed_at": observed_at}}
    for section in ("requirements", "claims", "jobs", "findings", "decisions", "evidence"):
        records = projected.get(section, [])
        if section == "jobs":
            derived = []
            for job in records:
                if not isinstance(job, dict):
                    derived.append(job)
                    continue
                item = dict(job)
                payload = job.get("payload")
                if isinstance(payload, dict):
                    for target, source in (("operation", "kind"), ("target_head", "target_head"), ("review_cohort", "review_cohort")):
                        if source in payload:
                            item[target] = payload[source]
                outcome = job.get("outcome")
                if isinstance(outcome, dict):
                    for target, source in (("author_id", "author_id"), ("outcome_status", "status")):
                        if source in outcome and isinstance(outcome[source], str):
                            item[target] = outcome[source]
                    if outcome.get("error"):
                        item["diagnostic"] = "Error details withheld; consult authorized engine state."
                    result_data = outcome.get("result")
                    if isinstance(result_data, dict) and isinstance(result_data.get("decision"), str):
                        item["outcome_decision"] = result_data["decision"]
                derived.append(item)
            records = derived
        public_records = [_record(item, _FIELDS[section], feedback, source_base) for item in records]
        if section == "evidence":
            for public, original in zip(public_records, records, strict=True):
                if isinstance(original, dict) and original.get("status") == "proved":
                    _qualify_proof(public)
        result[section] = public_records
    inventories = [event.get("payload") for event in raw.get("events", [])
                   if event.get("kind") == "inventory"]
    result["inventory"] = []
    if inventories:
        if len(inventories) != 1 or not isinstance(inventories[0], dict):
            raise ValueError("invalid inventory")
        surfaces = inventories[0].get("surfaces")
        if not isinstance(surfaces, list) or inventories[0].get("count") != len(surfaces):
            raise ValueError("invalid inventory")
        reviewed_requirements = {
            (record_id, record_digest)
            for event in raw.get("events", []) if event.get("kind") == "fas_reviewed"
            for record_id, record_digest in event.get("payload", {}).get("requirements", {}).items()
        }
        mapped = set()
        if current:
            for requirement in raw.get("requirements", []):
                if not isinstance(requirement, dict) or requirement.get("origin") == "uncertain":
                    continue
                record = {key: value for key, value in requirement.items() if key != "freshness"}
                if (record.get("id"), digest(record)) not in reviewed_requirements:
                    continue
                for citation in record.get("citations", []):
                    if isinstance(citation, dict) and citation.get("revision") == session.get("base") and _path(citation.get("path")):
                        mapped.add(citation["path"])
        for surface in surfaces:
            if not isinstance(surface, dict) or not _path(surface.get("path")) or surface.get("status") != "unmapped":
                raise ValueError("invalid inventory surface")
            path = _clean(surface["path"], feedback)
            is_mapped = path in mapped
            result["inventory"].append({"path": path, "status": "mapped" if is_mapped else "unmapped",
                                        "reason": "Reviewed requirement cites this path at the base revision." if is_mapped
                                        else "Initial inventory; mapping has not been assessed."})
    public_evidence = {record.get("id"): record for record in result["evidence"]}
    for claim in result["claims"]:
        if claim.get("status") == "established-within-scope":
            evidence_ids = claim.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids or any(public_evidence.get(eid, {}).get("status") != "proved" for eid in evidence_ids):
                claim["recorded_status"] = claim["status"]
                claim["status"] = "incomplete"
                claim["freshness"] = "unknown"
                claim["unavailable_fields"] = sorted(set(claim["unavailable_fields"] + ["qualified_evidence"]))
    return result


_INPUT_LIMIT = 32 * 1024 * 1024
_OUTPUT_LIMIT = 8 * 1024 * 1024
_CAPACITY_EXIT = 3


def _write_bounded(value: object, target, limit: int) -> None:
    total = 0
    for chunk in json.JSONEncoder(allow_nan=False, ensure_ascii=True, separators=(",", ":")).iterencode(value):
        data = chunk.encode("utf-8")
        total += len(data)
        if total > limit:
            raise IntegrationCapacityError("projection capacity exceeded")
        target.write(data)


def _worker_main() -> None:
    """Fixed worker entry point; input and output are private bounded pipes."""
    try:
        data = sys.stdin.buffer.read(_INPUT_LIMIT + 1)
        if len(data) > _INPUT_LIMIT:
            raise IntegrationCapacityError("projection capacity exceeded")
        request = json.loads(data)
        if not isinstance(request, dict) or set(request) != {"raw", "store_path"}:
            raise ValueError("invalid request")
        path = request["store_path"]
        if path is not None and not isinstance(path, str):
            raise ValueError("invalid request")
        result = _project_inner(request["raw"], store_path=Path(path) if path is not None else None)
        with tempfile.TemporaryFile() as output:
            _write_bounded(result, output, _OUTPUT_LIMIT)
            output.seek(0)
            while chunk := output.read(65536):
                sys.stdout.buffer.write(chunk)
    except IntegrationCapacityError:
        raise SystemExit(_CAPACITY_EXIT) from None
    except Exception:
        # Never reflect raw records, paths or exception text to the caller.
        raise SystemExit(2) from None


def project(raw: dict, *, store_path: Path | None = None, timeout: float = 10) -> dict:
    """Produce a bounded projection in a killable isolated trusted process."""
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("projection unavailable")
    deadline = time.monotonic() + min(float(timeout), 10.0)
    bootstrap = ("import sys; sys.path.insert(0, sys.argv[1]); "
                 "from mentat.autonomy_projection import _worker_main; _worker_main()")
    try:
        with tempfile.TemporaryFile() as input_file:
            _write_bounded({"raw": raw, "store_path": str(store_path) if store_path is not None else None}, input_file, _INPUT_LIMIT)
            input_file.seek(0)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("projection deadline")
            code, output, _ = process.run([sys.executable, "-I", "-B", "-c", bootstrap, str(Path(__file__).resolve().parent.parent)], cwd="/", env={}, stdin=input_file, output_limit=_OUTPUT_LIMIT, timeout=remaining, stderr=False)
        if code == _CAPACITY_EXIT:
            raise IntegrationCapacityError("projection capacity exceeded")
        if code:
            raise ValueError("projection unavailable")
        result = json.loads(output)
        if not isinstance(result, dict):
            raise ValueError("projection unavailable")
        return result
    except IntegrationCapacityError:
        raise
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        raise ValueError("projection unavailable") from exc
