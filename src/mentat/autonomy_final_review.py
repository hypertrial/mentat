"""Pure planning primitives for bounded final source correspondence review."""

import hashlib
import json
import re

from .project import safe_relative_path


def identity(value: object) -> str:
    """Return the stable identity of JSON-compatible controller data."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _local_citation(value: object, source_entries: dict[str, dict] | set[str]) -> str | None:
    if isinstance(value, dict):
        value = value.get("path", value.get("url"))
    if not isinstance(value, str) or not value:
        raise ValueError("invalid source review citation")
    if value.startswith(("https://", "http://")):
        if not re.match(r"^https?://[^/\s?#]+(?:[/?#]|$)", value) or any(c.isspace() for c in value):
            raise ValueError("invalid source review external citation")
        return None
    path = value if value in source_entries else re.sub(
        r"(?:#L\d+(?:-L?\d+)?|:\d+(?::\d+)?)$", "", value
    )
    safe_relative_path(path)
    return path


def build_plan(base_fas: dict, current_fas: dict, changed_paths: list[str],
               source_entries: dict[str, dict], bindings: dict) -> dict:
    """Derive ordered obligations from both inventories and changed records.

    ``source_entries`` contains controller-verified base/current Git identities;
    absence is represented by None, never by invented current bytes.
    """
    for fas in (base_fas, current_fas):
        if not isinstance(fas, dict):
            raise ValueError("invalid final source FAS")
        for collection in ("requirements", "claims", "surfaces"):
            if not isinstance(fas.get(collection, []), list):
                raise ValueError("invalid final source FAS records")
    old_surfaces = {s["path"]: s for s in base_fas.get("surfaces", [])}
    surfaces = {s["path"]: s for s in current_fas.get("surfaces", [])}
    old_requirements = {r["id"]: r for r in base_fas.get("requirements", [])}
    requirements = {r["id"]: r for r in current_fas.get("requirements", [])}
    old_claims = {r["id"]: r for r in base_fas.get("claims", [])}
    claims = {r["id"]: r for r in current_fas.get("claims", [])}
    for path in bindings.get("required_paths", []):
        safe_relative_path(path)
    for path in changed_paths:
        safe_relative_path(path)
    changed = set(changed_paths)
    affected = {key for key in old_requirements.keys() | requirements.keys()
                if old_requirements.get(key) != requirements.get(key)}
    changed_cas = {path for path in changed if path.startswith("openspec/specs/")}
    affected.update(record["id"] for fas in (base_fas, current_fas)
                    for record in fas.get("requirements", [])
                    if record.get("spec_path") in changed_cas)
    for key in old_claims.keys() | claims.keys():
        if old_claims.get(key) != claims.get(key):
            for claim in (old_claims.get(key), claims.get(key)):
                if claim:
                    affected.update(claim.get("requirement_ids", []))
    for path in old_surfaces.keys() | surfaces.keys():
        old, new = old_surfaces.get(path), surfaces.get(path)
        if path in changed or old != new:
            for surface in (old, new):
                if surface and surface.get("status") == "mapped":
                    affected.update(surface.get("requirement_ids", []))
    paths = {path for path in changed if path != ".mentat/fas.json"
             and not path.startswith("openspec/specs/")}
    paths.update(bindings.get("required_paths", []))
    for path in old_surfaces.keys() | surfaces.keys():
        for surface in (old_surfaces.get(path), surfaces.get(path)):
            if surface and surface.get("status") == "mapped" and (
                path in changed or old_surfaces.get(path) != surfaces.get(path)
                or affected.intersection(surface.get("requirement_ids", []))
            ):
                paths.add(path)
    for fas in (base_fas, current_fas):
        for record in [*fas.get("requirements", []), *fas.get("claims", [])]:
            if record.get("id") not in affected and not affected.intersection(record.get("requirement_ids", [])):
                continue
            citations = record.get("citations", [])
            if not isinstance(citations, list):
                raise ValueError("invalid source review citations")
            revision = record.get("revision", {})
            if isinstance(revision, dict):
                citations = [*citations, *revision.get("evidence", [])]
            for citation in citations:
                path = _local_citation(citation, source_entries)
                if path is not None:
                    paths.add(path)
            contract = record.get("formal_contract")
            if isinstance(contract, dict) and isinstance(contract.get("path"), str):
                safe_relative_path(contract["path"])
                paths.add(contract["path"])
    obligations = []
    for path in sorted(paths):
        safe_relative_path(path)
        entry = source_entries.get(path)
        if not isinstance(entry, dict) or set(entry) != {"base", "current"}:
            raise ValueError("missing Git identity for final source obligation")
        if entry["current"] is None and path not in changed:
            raise ValueError("required current source is unavailable")
        obligations.append({"path": path, "base": entry["base"], "current": entry["current"],
                            "mode": "current-source" if entry["current"] is not None else "full-diff",
                            "base_surface": old_surfaces.get(path), "current_surface": surfaces.get(path)})
    gaps = [s for s in current_fas.get("surfaces", []) if s.get("status") != "mapped"]
    records = {}
    for label, fas in (("base", base_fas), ("current", current_fas)):
        records[label] = {
            "requirements": [record for record in fas.get("requirements", [])
                             if record.get("id") in affected],
            "claims": [record for record in fas.get("claims", [])
                       if affected.intersection(record.get("requirement_ids", []))],
        }
    body = {"schema_version": 1, "bindings": bindings, "obligations": obligations,
            "records": records,
            "affected_requirements": sorted(affected), "gaps": gaps,
            "base_fas_digest": identity(base_fas), "current_fas_digest": identity(current_fas)}
    return {**body, "id": identity(body)}


def batch(plan: dict, obligations: list[dict]) -> dict:
    """Bind a deterministic batch to its ordered plan and exact obligations."""
    body = {"plan_id": plan["id"], "obligations": obligations, "records": plan["records"],
            "base_fas_digest": plan["base_fas_digest"], "current_fas_digest": plan["current_fas_digest"]}
    return {**body, "id": identity(body)}


def validate_findings(result: dict) -> dict:
    """Validate complete source findings without truncating model output."""
    summary = result.get("summary")
    findings = result.get("findings")
    if not isinstance(summary, str) or not isinstance(findings, list):
        raise ValueError("invalid final source findings")
    if len(json.dumps({"summary": summary, "findings": findings}, ensure_ascii=True).encode()) > 4096:
        raise ValueError("final source findings exceed 4096 bytes")
    try:
        detail = json.loads(summary)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid final source summary") from exc
    if (not isinstance(detail, dict) or set(detail) != {"findings", "unresolved_dependencies"}
            or not isinstance(detail["findings"], str) or not detail["findings"].strip()
            or not isinstance(detail["unresolved_dependencies"], list)
            or any(not isinstance(item, str) or not item.strip() for item in detail["unresolved_dependencies"])):
        raise ValueError("invalid final source summary")
    if result.get("decision") == "accept" and any(
        not isinstance(result.get(field), list) or result[field]
        for field in ("requirements", "claims", "verification_requests", "defects", "changes")
    ):
        raise ValueError("accepted final source review has unsupported output fields")
    if result.get("decision") == "accept" and detail["unresolved_dependencies"]:
        raise ValueError("accepted final source review has unresolved dependencies")
    return detail


def batch_linked_records(source_batch: dict, inventory_paths: set[str]) -> list[dict]:
    """Select only records directly or transitively linked to batch obligations."""
    obligations = source_batch["obligations"]
    paths = {item["path"] for item in obligations}
    linked = {key for item in obligations
              for surface in (item.get("base_surface"), item.get("current_surface"))
              if isinstance(surface, dict) for key in surface.get("requirement_ids", [])}
    groups = [(collection, record) for version in ("base", "current")
              for collection in ("requirements", "claims")
              for record in source_batch["records"][version][collection]]
    for collection, record in groups:
        citations = record.get("citations", [])
        revision = record.get("revision", {})
        evidence = revision.get("evidence", []) if isinstance(revision, dict) else []
        references = [*(citations if isinstance(citations, list) else []),
                      *(evidence if isinstance(evidence, list) else [])]
        direct = False
        for citation in references:
            try:
                path = _local_citation(citation, inventory_paths)
            except ValueError:
                # Invalid unrelated citations cannot make their record linked.
                continue
            if path in paths:
                direct = True
        contract = record.get("formal_contract")
        if (isinstance(contract, dict) and isinstance(contract.get("path"), str)
                and contract["path"] in paths):
            direct = True
        if direct:
            if collection == "requirements":
                linked.add(record["id"])
            else:
                linked.update(record.get("requirement_ids", []))
    return [record for _, record in groups if record.get("id") in linked
            or linked.intersection(record.get("requirement_ids", []))]
