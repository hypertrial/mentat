"""Bounded, path-free projections of validated project evidence for bridge clients."""

import subprocess
from pathlib import Path

from . import evidence
from .canonical import json_bytes
from .decision import saved_run_snapshot
from .project import ClaimProvenance
from .records import Assurance

OMISSIONS = ["local paths withheld", "commands withheld", "logs withheld", "unrecorded information unavailable"]
REF_KEYS = {"runId", "base", "revision", "runner", "contractDigest"}


def _root(repo):
    if not isinstance(repo, str) or not repo or not Path(repo).is_absolute() or "\x00" in repo:
        raise ValueError("Invalid repo")
    root = Path(repo).resolve(strict=True)
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=10, check=True)
    if Path(result.stdout.decode().strip()).resolve() != root:
        raise ValueError("repo must be the repository root")
    return root


def _read(state, root, ref):
    if not isinstance(ref, dict) or set(ref) != REF_KEYS:
        raise ValueError("Invalid run reference")
    if any(not isinstance(value, str) or not value or len(value) > 128 for value in ref.values()):
        raise ValueError("Invalid run reference")
    bundle, record, manifest = saved_run_snapshot(state, ref["runId"])
    if record.get("kind") != "project-verification":
        raise ValueError("Unsupported saved verification record")
    expected = {"repo": str(root), "run_id": ref["runId"], "base": ref["base"],
                "revision": ref["revision"], "runner": ref["runner"],
                "contract_digest": ref["contractDigest"]}
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError("Run does not match pinned project reference")
    return bundle, record, manifest


def _run(record, manifest):
    result = {"runId": record["run_id"], "base": record["base"], "revision": record["revision"],
              "runner": record["runner"], "contractDigest": record["contract_digest"],
              "manifestDigest": manifest, "status": record["status"]}
    if "source_qualification_status" in record:
        result["qualificationStatus"] = record["source_qualification_status"]
    return result


def _check(check, method, artifacts):
    result = {"id": check["id"], "method": method, "status": check["status"],
            "artifacts": {role: {"name": name, "sha256": artifacts[name]}
                          for role, name in check.get("artifacts", {}).items()},
            "artifactOmissions": check.get("artifact_omissions", []),
            "logOmissions": check.get("log_omissions", [])}
    if method == "formal":
        result["engine"] = check["kind"]
    for source, target in (("outcome", "outcome"), ("qualified", "qualified"),
                           ("required_cases", "requiredCases"), ("case_results", "caseResults")):
        if source in check:
            result[target] = check[source]
    return result


def _claims(record):
    claims = []
    checks = [("formal", item) for item in record["model_checks"]]
    checks += [("behavior", item) for case in record["cases"] for item in case["checks"]]
    checks += [("source-mutation", item) for item in evidence.source_qualifications(record)]
    if record["behavior_skipped"]:
        checks += [("behavior", {**item, "status": "skipped"}) for item in record.get("behavior_checks", [])]
    for claim in record["claims"]:
        item = {"id": claim["id"], "statement": claim["statement"],
                "assumptions": claim.get("assumptions", []),
                "checks": [_check(check, method, record["artifacts"]) for method, check in checks
                           if check["claim"] == claim["id"]]}
        if claim.get("assurance") is not None:
            item["assurance"] = Assurance.model_validate(claim["assurance"]).model_dump(exclude_unset=True)
        if claim.get("provenance") is not None:
            provenance = ClaimProvenance.model_validate(claim["provenance"])
            item["locations"] = [citation.model_dump() for citation in provenance.citations]
        claims.append(item)
    return claims


def _environment(record):
    tools = record.get("tools", {})
    versions = {}
    if isinstance(tools, dict):
        for name in ("lean", "lean-comparator", "z3"):
            info = tools.get(name)
            if isinstance(info, dict) and isinstance(info.get("version"), str):
                versions[name] = info["version"]
    result = {"runner": record["runner"], "tools": versions}
    runtime = record.get("runtime")
    if isinstance(runtime, dict):
        for key in ("platform", "image", "image_id"):
            if isinstance(runtime.get(key), str):
                result[key] = runtime[key]
    return result


def _bounded(result):
    if len(json_bytes(result)) > 900_000:
        raise ValueError("Evidence projection exceeds size limit")
    return result


def inspect(state, repo, ref):
    root = _root(repo)
    state = evidence.state_directory(state, candidate_root=root)
    _, record, manifest = _read(state, root, ref)
    return _bounded({"run": _run(record, manifest), "claims": _claims(record),
                     "environment": _environment(record), "omissions": OMISSIONS})


def _comparison_check(check):
    return {key: check[key] for key in ("id", "kind") if key in check} | {
        side: {"status": value["status"]} if value is not None else None
        for side in ("before", "after") if (value := check.get(side)) is not None
    }


def compare(state, repo, before_ref, after_ref, across_contracts):
    if type(across_contracts) is not bool:
        raise ValueError("Invalid acrossContracts")
    root = _root(repo)
    state = evidence.state_directory(state, candidate_root=root)
    before_path, before, before_manifest = _read(state, root, before_ref)
    after_path, after, after_manifest = _read(state, root, after_ref)
    comparison = evidence.compare_saved_runs(before_path, before, after_path, after,
                                             across_contracts=across_contracts)
    claims = []
    for claim in comparison["claims"]:
        if across_contracts:
            item = {"id": claim["id"], "definition": claim["definition"]}
            for side in ("before", "after"):
                value = claim[side]
                item[side] = None if value is None else {
                    "checks": [{"id": c["id"], "kind": c["kind"], "status": c["status"]}
                               for c in value["checks"]]}
        else:
            item = {"id": claim["id"], "checks": [_comparison_check(c) for c in claim["checks"]]}
        claims.append(item)
    result = {"mode": "across-contracts" if across_contracts else "same-scope",
              "status": comparison["status"], "before": _run(before, before_manifest),
              "after": _run(after, after_manifest),
              "scopeDifferences": [key for key in ("base", "runner", "contract_digest")
                                   if before[key] != after[key]],
              "environmentChanged": comparison["environment_changed"], "claims": claims,
              "omissions": OMISSIONS}
    if across_contracts:
        result["inputChanges"] = comparison["input_changes"]
    return _bounded(result)
