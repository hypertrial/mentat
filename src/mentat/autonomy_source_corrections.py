"""Pure validation for current final-source correction requests."""

import json
from pathlib import Path

from .autonomy_final_review import batch_linked_records
from .project import safe_relative_path


def validate_result(result: dict, scope: dict, inventory_paths: set[str]) -> dict:
    """Validate complete v2 source findings and existing, scoped correction targets."""
    summary, findings = result.get("summary"), result.get("findings")
    if not isinstance(summary, str) or not isinstance(findings, list):
        raise ValueError("invalid final source findings")
    if len(json.dumps({"summary": summary, "findings": findings}, ensure_ascii=True).encode()) > 4096:
        raise ValueError("final source findings exceed 4096 bytes")
    def unique(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate source summary key")
            result[key] = value
        return result
    try:
        detail = json.loads(summary, object_pairs_hook=unique)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid final source summary") from exc
    if (not isinstance(detail, dict)
            or set(detail) != {"schema_version", "findings", "unresolved_dependencies", "spec_corrections"}
            or type(detail["schema_version"]) is not int or detail["schema_version"] != 2
            or not isinstance(detail["findings"], str) or not detail["findings"].strip()
            or not isinstance(detail["unresolved_dependencies"], list)
            or any(not isinstance(item, str) or not item.strip() for item in detail["unresolved_dependencies"])
            or not isinstance(detail["spec_corrections"], list)):
        raise ValueError("invalid version 2 final source summary")
    current = scope["records"]["current"]
    linked_records = batch_linked_records(scope, inventory_paths)
    linked_ids = {record.get("id") for record in linked_records}
    allowed = {
        "requirements": {r["id"] for r in current["requirements"] if r.get("id") in linked_ids},
        "claims": {r["id"] for r in current["claims"] if r.get("id") in linked_ids},
        "surfaces": {o["path"] for o in scope["obligations"] if isinstance(o.get("current_surface"), dict)},
    }
    seen = set()
    for target in detail["spec_corrections"]:
        if (not isinstance(target, dict) or set(target) != {"collection", "key", "reason"}
                or target["collection"] not in allowed or not isinstance(target["key"], str)
                or target["key"] not in allowed[target["collection"]]
                or not isinstance(target["reason"], str) or not target["reason"].strip()):
            raise ValueError("invalid or out-of-scope specification correction")
        key = (target["collection"], target["key"])
        if key in seen:
            raise ValueError("duplicate specification correction")
        seen.add(key)
    decision = result.get("decision")
    if decision in {"accept", "revise"} and any(
        not isinstance(result.get(field), list) or result[field]
        for field in ("requirements", "claims", "verification_requests", "defects", "changes")
    ):
        raise ValueError("source acceptance or revision has unsupported output fields")
    if decision == "accept" and (detail["unresolved_dependencies"] or detail["spec_corrections"]):
        raise ValueError("accepted source review has unresolved work")
    if decision == "revise" and (detail["unresolved_dependencies"] or not detail["spec_corrections"]):
        raise ValueError("source revision needs resolved, explicit specification corrections")
    return detail


def input_bindings(job: dict, session_id: str, authority_digest: str, tool_identity: dict) -> dict:
    """Construct the controller-owned immutable source input binding."""
    payload = job["payload"]
    proposal = payload["proposal"]
    return {"schema_version": 1, "job_id": job["id"], "attempt": job["attempt"],
            "session_id": session_id, "head": proposal["head"], "base": proposal["parent"],
            "diff_digest": proposal["diff_digest"], "source_batch": payload["source_batch"],
            "policy_digest": authority_digest, "tool_identity": tool_identity,
            "check_key": payload["check_key"], "check_evidence_id": payload["check_evidence_id"]}


def allowed_path(kind: str, path: str) -> bool:
    safe_relative_path(path)
    if kind == "generate_spec":
        return path == ".mentat/fas.json" or path.startswith("openspec/specs/")
    if kind == "generate_verification":
        return path.startswith((".mentat/verification/", "verification/"))
    return not (
        path.startswith(("openspec/", ".mentat/", "verification/", "scripts/", ".github/"))
        or path
        in {
            "AGENTS.md",
            "CONTRIBUTING.md",
            "pyproject.toml",
            "uv.lock",
            "Makefile",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "tox.ini",
            "pytest.ini",
            "setup.cfg",
            "setup.py",
            "verify.py",
        }
        or Path(path).name.startswith(("requirements", "conftest"))
    )
