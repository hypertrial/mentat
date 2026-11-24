"""Immutable, record-level authority for SCL review corrections."""

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .project import safe_relative_path

AREAS = ("product", "functional", "nonfunctional", "architecture", "data-api", "acceptance")
FAS_PATH = ".mentat/fas.json"
MAX_ARTIFACT = 1_000_000


def ordinary_review(job: dict) -> bool:
    payload = job["payload"]
    return (payload.get("kind") == "review_spec" and not payload.get("source_cycle_id")
            and not isinstance(payload.get("repair"), dict))


def superseded_spec(repo: Path, job: dict, head: str, snapshot: dict, git) -> bool:
    """Require repair work to retain checkpoints and later ordinary work to retain repairs."""
    payload = job["payload"]
    if payload.get("source_cycle_id"):
        return False
    repair = isinstance(payload.get("repair"), dict)
    heads = [event["payload"].get("head") for event in snapshot["events"]
             if event["kind"] == "fas_reviewed" and (repair or event["payload"].get("repair_backed") is True)]
    if not repair:
        heads = heads[-1:]
    for checkpoint in heads:
        if not isinstance(checkpoint, str) or not checkpoint:
            raise ValueError("invalid specification review checkpoint")
        if git(repo, "merge-base", checkpoint, head) != checkpoint:
            return True
    return False


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _bounded_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT:
        raise ValueError("missing, linked, or oversized specification artifact")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("specification artifact must be an object")
    return value


def validate_specification(
    repo: Path, *, tracked: list[str] | None = None, prior: dict | None = None
) -> dict:
    """Validate identifiers and complete area accounting, not English truth."""
    fas = _bounded_json(repo / FAS_PATH)
    if (
        set(fas) != {"schema_version", "requirements", "claims", "surfaces"}
        or fas["schema_version"] != 1
    ):
        raise ValueError("unsupported FAS inventory")
    for field in ("requirements", "claims", "surfaces"):
        if not isinstance(fas[field], list) or len(fas[field]) > 4096:
            raise ValueError(f"invalid {field} inventory")
    ids = set()
    areas = set()
    for requirement in fas["requirements"]:
        if not isinstance(requirement, dict):
            raise ValueError("invalid requirement")
        rid = requirement.get("id")
        try:
            uuid.UUID(rid)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("requirements need persistent UUIDs") from exc
        if (
            rid in ids
            or requirement.get("area") not in AREAS
            or requirement.get("origin") not in {"observed", "intended", "inferred", "uncertain"}
        ):
            raise ValueError("duplicate or invalid requirement")
        statement = requirement.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError("empty requirement")
        spec_path = requirement.get("spec_path")
        if not isinstance(spec_path, str) or not spec_path.startswith("openspec/specs/"):
            raise ValueError("requirement must reference its repository OpenSpec document")
        safe_relative_path(spec_path)
        spec = repo / spec_path
        if (
            spec.is_symlink()
            or not spec.is_file()
            or not isinstance(rid, str)
            or rid not in spec.read_text()
        ):
            raise ValueError("OpenSpec document is missing its persistent requirement ID")
        ids.add(rid)
        areas.add(requirement["area"])
    if areas != set(AREAS):
        raise ValueError("CAS must account for all six areas, including explicit uncertainty")
    claim_ids = set()
    for claim in fas["claims"]:
        if not isinstance(claim, dict):
            raise ValueError("invalid formal claim")
        try:
            uuid.UUID(claim.get("id"))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("formal claims need persistent UUIDs") from exc
        refs = claim.get("requirement_ids")
        if (
            claim["id"] in claim_ids
            or not isinstance(refs, list)
            or not refs
            or not set(refs) <= ids
        ):
            raise ValueError("invalid claim traceability")
        if not isinstance(claim.get("statement"), str) or not claim["statement"].strip():
            raise ValueError("empty formal claim")
        if not isinstance(claim.get("assumptions", []), list):
            raise ValueError("claim assumptions must be explicit")
        if claim.get("status", "unattempted") not in {"unattempted", "unsupported", "inconclusive"}:
            raise ValueError("claim status must not assert an agent-authored proof")
        contract = claim.get("formal_contract")
        if "formal_contract" in claim:
            if not isinstance(contract, dict) or set(contract) != {
                "path",
                "function",
                "inputs",
                "requires",
                "ensures",
            }:
                raise ValueError("invalid source formal contract")
            if (
                not isinstance(contract["path"], str)
                or not isinstance(contract["function"], str)
                or not contract["function"].isidentifier()
            ):
                raise ValueError("invalid source formal contract identity")
            safe_relative_path(contract["path"])
            if not isinstance(contract["inputs"], dict) or any(
                not isinstance(k, str) or not k.isidentifier() or v not in ("int", "bool")
                for k, v in contract["inputs"].items()
            ):
                raise ValueError("invalid source formal contract inputs")
            if not all(
                isinstance(contract[key], str) and contract[key] for key in ("requires", "ensures")
            ):
                raise ValueError("invalid source formal contract predicates")
        claim_ids.add(claim["id"])
    for surface in fas["surfaces"]:
        if not isinstance(surface, dict) or surface.get("status") not in {
            "mapped",
            "gap",
            "unsupported",
            "unexamined",
        }:
            raise ValueError("every inventoried surface needs a mapping or explicit gap")
        safe_relative_path(surface.get("path", ""))
        if surface.get("status") == "mapped" and (
            not surface.get("requirement_ids") or not set(surface.get("requirement_ids", [])) <= ids
        ):
            raise ValueError("surface references unknown requirements")
    surface_paths = [surface["path"] for surface in fas["surfaces"]]
    if len(surface_paths) != len(set(surface_paths)):
        raise ValueError("duplicate surface inventory path")
    if tracked is not None and not set(tracked) <= set(surface_paths):
        raise ValueError("surface inventory omits tracked paths")
    if prior is not None:
        for key in ("requirements", "claims"):
            current = {item["id"]: item for item in fas[key]}
            for old in prior[key]:
                new = current.get(old["id"])
                if new is None:
                    raise ValueError("existing identifiers cannot be removed")
                if new == old:
                    continue
                lineage = new.get("revision")
                previous_revision = old.get("revision", {})
                old_version = (
                    previous_revision.get("version", 1)
                    if isinstance(previous_revision, dict)
                    else 1
                )
                if (
                    not isinstance(lineage, dict)
                    or type(lineage.get("version")) is not int
                    or lineage["version"] != old_version + 1
                    or lineage.get("previous_digest") != digest(old)
                    or not isinstance(lineage.get("reason"), str)
                    or not lineage["reason"].strip()
                    or not isinstance(lineage.get("evidence"), list)
                    or not lineage["evidence"]
                ):
                    raise ValueError(
                        "revised records require versioned lineage, rationale and evidence"
                    )
                if (
                    key == "requirements"
                    and old.get("origin") in {"observed", "intended"}
                    and new.get("origin") not in {"observed", "intended"}
                ):
                    raise ValueError("established intent cannot be downgraded")
    return fas



def _index(fas: dict, collection: str) -> dict:
    key = "path" if collection == "surfaces" else "id"
    records = fas.get(collection)
    if not isinstance(records, list):
        raise ValueError("invalid correction FAS collection")
    result = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get(key), str):
            raise ValueError("invalid correction FAS record")
        name = record[key]
        if name in result:
            raise ValueError("duplicate correction FAS record")
        result[name] = record
    return result


def derive(before: dict, after: dict, changed_paths: list[str], binding: dict) -> dict:
    """Derive editable records only from the admitted draft's actual delta."""
    if not isinstance(binding, dict) or set(binding) != {
        "accepted", "draft", "review_base", "cohort", "decision_id", "review_job_id"
    } or any(not isinstance(v, str) or not v for v in binding.values()):
        raise ValueError("invalid correction binding")
    allowed = {}
    indexes = {}
    for collection in ("requirements", "claims", "surfaces"):
        old, new = _index(before, collection), _index(after, collection)
        indexes[collection] = (old, new)
        allowed[collection] = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
    files = set()
    for path in changed_paths:
        safe_relative_path(path)
        if path == FAS_PATH:
            continue
        if not path.startswith("openspec/specs/"):
            raise ValueError("correction draft changes non-specification file")
        files.add(path)
    for record_id in allowed["requirements"]:
        for index in indexes["requirements"]:
            record = index.get(record_id)
            if record is not None:
                path = record.get("spec_path")
                safe_relative_path(path)
                if not path.startswith("openspec/specs/"):
                    raise ValueError("invalid correction specification path")
                files.add(path)
    value = {"schema_version": 1, "binding": dict(binding), "records": allowed, "files": sorted(files)}
    return {**value, "id": digest(value)}


def validate_delta(scope: dict, before: dict, after: dict, changed_paths: list[str]) -> None:
    """Reject decoded edits to records or files outside an authenticated envelope."""
    for collection in ("requirements", "claims", "surfaces"):
        old, new = _index(before, collection), _index(after, collection)
        allowed = set(scope["records"][collection])
        if collection == "surfaces":
            allowed.update(
                path for path in changed_paths
                if path in scope["files"] and path not in old
                and new.get(path) == {"path": path, "status": "unexamined", "requirement_ids": []}
            )
        unexpected = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k) and k not in allowed)
        if unexpected:
            raise ValueError(f"correction scope excludes {collection}: {unexpected[0]}")
    for path in changed_paths:
        if path != FAS_PATH and path not in scope["files"]:
            raise ValueError(f"correction scope excludes file: {path}")



def _endpoint(controller: Any, revision: str, inventory: Any, *, allow_absent: bool = False) -> dict:
    import tempfile

    from . import gitio

    tracked = [item["path"] for item in inventory(controller.repo, revision)]
    if allow_absent and FAS_PATH not in tracked:
        import time

        deadline = time.monotonic() + 60
        top = list(gitio.ls_tree(controller.repo, revision, "-z", [], deadline))
        rows = list(gitio.ls_tree(controller.repo, revision, "-rzt", [], deadline))
        gitio.verify_listing(controller.repo, revision, rows, {b"", b".mentat"}, top=top)
        paths = {row.split(b"\t", 1)[1].decode("utf-8", "strict") for row in rows}
        if not any(path == FAS_PATH or path.startswith(FAS_PATH + "/") for path in paths):
            return {"schema_version": 1, "requirements": [], "claims": [], "surfaces": []}
    raw = gitio.read_file(controller.repo, revision, FAS_PATH)
    if len(raw) > MAX_ARTIFACT:
        raise ValueError("oversized correction FAS")
    value = json.loads(raw)
    with tempfile.TemporaryDirectory(prefix="mentat-scope-") as directory:
        root = Path(directory)
        dest = root / FAS_PATH
        dest.parent.mkdir(parents=True)
        dest.write_bytes(raw)
        if not isinstance(value, dict) or not isinstance(value.get("requirements"), list):
            raise ValueError("invalid correction FAS")
        for requirement in value["requirements"]:
            if not isinstance(requirement, dict):
                raise ValueError("invalid correction requirement")
            path = requirement.get("spec_path")
            if not isinstance(path, str):
                raise ValueError("invalid correction specification path")
            safe_relative_path(path)
            if not path.startswith("openspec/specs/"):
                raise ValueError("invalid correction specification path")
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gitio.read_file(controller.repo, revision, path))
        return validate_specification(root, tracked=tracked)



def _initial_pair(controller: Any, snapshot: dict, review: dict, proposal: dict, cohort: str) -> tuple[list[str], int]:
    """Authenticate the original scheduling boundary for the complete review pair."""
    root, round_number = proposal.get("root_item"), proposal.get("round", 0)
    if not isinstance(root, str) or not root or type(round_number) is not int or round_number < 0:
        raise ValueError("invalid initial specification review identity")
    jobs = snapshot["jobs"]
    events = snapshot["events"]
    pair = []
    sequences = []
    for slot in (1, 2):
        item = f"{root}.review_spec..{round_number}.{slot}"
        matches = [j for j in jobs if j.get("item_id") == item
                   and j.get("payload", {}).get("review_cohort") == cohort]
        if len(matches) != 1:
            raise ValueError("initial specification review pair is missing or ambiguous")
        member = matches[0]
        payload = member.get("payload", {})
        enqueued = [e for e in events if e.get("kind") == "job_enqueued"
                    and e.get("payload", {}).get("job_id") == member.get("id")]
        if (member.get("session_id") != controller.session_id or member.get("loop") != "scl"
            or member.get("role") != "code-reviewer" or payload != review["payload"]
            or payload.get("proposal") != proposal or payload.get("kind") != "review_spec"
            or controller._review_cohort("review_spec", proposal, payload, None, None) != cohort
            or len(enqueued) != 1):
            raise ValueError("initial specification review pair is inconsistent")
        event = enqueued[0]
        recorded = event.get("payload", {})
        if (type(event.get("seq")) is not int or recorded.get("loop") != "scl"
            or recorded.get("role") != "code-reviewer" or recorded.get("item_id") != item
            or recorded.get("payload") != payload
            or recorded.get("input_digest") != member.get("input_digest")):
            raise ValueError("initial specification review scheduling is inconsistent")
        pair.append(member["id"])
        sequences.append(event["seq"])
    if len(set(pair)) != 2 or review["id"] not in pair:
        raise ValueError("initial specification review slots are not distinct")
    return pair, max(sequences)


def derive_for_review(controller: Any, review_job_id: str, decision_id: str, inventory: Any, git_paths: Any, *, legacy: bool = False) -> dict:
    """Reconstruct scope from the durable review and validated immutable endpoint trees."""
    from . import gitio
    from .autonomy_runtime import git

    snapshot = controller.store.snapshot(controller.session_id)
    reviews = [j for j in snapshot["jobs"] if j["id"] == review_job_id]
    decisions = [d for d in snapshot["decisions"] if d["id"] == decision_id]
    if len(reviews) != 1 or len(decisions) != 1:
        raise ValueError("correction review authority is missing")
    review, decision = reviews[0], decisions[0]
    payload, outcome = review["payload"], review.get("outcome") or {}
    proposal = payload.get("proposal")
    if not isinstance(proposal, dict):
        raise ValueError("correction review proposal is missing")
    cohort = payload.get("review_cohort")
    if (review.get("status") != "succeeded" or payload.get("kind") != "review_spec"
        or decision.get("job_id") != review_job_id or decision.get("review_cohort") != cohort
        or decision.get("review_kind") != "review_spec" or decision.get("decision") != "revise"
        or outcome.get("status") != "succeeded" or outcome.get("result", {}).get("decision") != "revise"
        or outcome.get("author_id") != decision.get("reviewer_id")
        or outcome.get("head") != proposal.get("head") or outcome.get("diff_digest") != proposal.get("diff_digest")
        or decision.get("head") != proposal.get("head") or decision.get("diff_digest") != proposal.get("diff_digest")
        or controller._review_cohort("review_spec", proposal, payload, None, None) != cohort):
        raise ValueError("correction review authority is inconsistent")
    binding = {"accepted": proposal.get("accepted_spec_base"), "draft": proposal.get("head"),
               "review_base": proposal.get("parent"), "cohort": cohort,
               "decision_id": decision_id, "review_job_id": review_job_id}
    for revision in (binding["accepted"], binding["draft"], binding["review_base"]):
        if not isinstance(revision, str) or gitio.verify_revision(controller.repo, revision) != revision:
            raise ValueError("invalid correction endpoint")
    if (git(controller.repo, "merge-base", binding["accepted"], binding["draft"]) != binding["accepted"]
        or git(controller.repo, "merge-base", binding["review_base"], binding["accepted"]) != binding["review_base"]):
        raise ValueError("correction endpoints are not ancestral")

    delta_base = binding["accepted"]
    initial = None
    if not legacy and binding["review_base"] == snapshot["session"]["base"] and delta_base != binding["review_base"]:
        pair, cutoff = _initial_pair(controller, snapshot, review, proposal, cohort)
        if not any(e.get("kind") == "fas_reviewed" and e.get("seq", 0) <= cutoff
                   for e in snapshot["events"]):
            delta_base = binding["review_base"]
            initial = {"basis": "initial", "delta_base": delta_base,
                       "review_jobs": pair, "cutoff": cutoff}
    old, new = _endpoint(controller, delta_base, inventory, allow_absent=True), _endpoint(controller, binding["draft"], inventory)
    paths = git_paths(controller.repo, "diff", "--name-only", "-z", "--no-renames",
                       "--no-ext-diff", "--no-textconv", delta_base, binding["draft"])
    if initial is not None:
        paths = [p for p in paths if p == FAS_PATH or p.startswith("openspec/specs/")]
    result = derive(old, new, paths, binding)
    if initial is not None:
        value = {**{k: v for k, v in result.items() if k != "id"},
                 "schema_version": 2, "binding": {**binding, **initial}}
        return {**value, "id": digest(value)}
    return result


def authenticate(controller: Any, job: dict, inventory: Any, git_paths: Any) -> dict | None:
    """Re-derive the envelope from its durable review; never trust model-supplied scope."""
    payload = job["payload"]
    snapshot = controller.store.snapshot(controller.session_id)
    events = [e["payload"] for e in snapshot["events"] if e.get("kind") == "job_enqueued" and e.get("payload", {}).get("job_id") == job["id"]]
    scope = payload.get("correction_scope")
    if scope is None and not any("correction_scope" in event.get("payload", {}) for event in events):
        return None
    if len(events) != 1 or events[0].get("payload") != payload:
        raise ValueError("correction job differs from durable input")
    if scope is None:
        raise ValueError("durable correction scope is missing")
    if not isinstance(scope, dict) or not isinstance(scope.get("binding"), dict):
        raise ValueError("invalid correction scope")
    binding = scope["binding"]
    version = scope.get("schema_version")
    if version not in (1, 2):
        raise ValueError("unsupported correction scope version")
    expected = derive_for_review(controller, binding.get("review_job_id"), binding.get("decision_id"), inventory, git_paths, legacy=version == 1)
    if scope != expected or payload.get("review_base") != binding["review_base"]:
        raise ValueError("correction scope or review baseline changed")
    from .autonomy_runtime import git

    parent = payload.get("target_head")
    if not isinstance(parent, str) or git(controller.repo, "merge-base", binding["accepted"], parent) != binding["accepted"]:
        raise ValueError("correction input is not an authorized descendant")
    original = _endpoint(controller, binding["accepted"], inventory, allow_absent=True)
    current = _endpoint(controller, parent, inventory, allow_absent=parent == binding["accepted"])
    changed = git_paths(controller.repo, "diff", "--name-only", "-z", "--no-renames",
                        "--no-ext-diff", "--no-textconv", binding["accepted"], parent)
    validate_delta(scope, original, current, changed)
    return scope


def validate_live_frontier(controller: Any, scope: dict, head: str) -> None:
    """Do not let an initial correction discard a later accepted checkpoint."""
    if scope.get("schema_version") != 2:
        return
    from .autonomy_runtime import git

    cutoff = scope["binding"]["cutoff"]
    for event in controller.store.snapshot(controller.session_id)["events"]:
        if event.get("kind") != "fas_reviewed" or event.get("seq", 0) <= cutoff:
            continue
        accepted = event.get("payload", {}).get("head")
        if not isinstance(accepted, str) or not accepted or git(controller.repo, "merge-base", accepted, head) != accepted:
            raise ValueError("initial correction does not retain a newer accepted specification")
