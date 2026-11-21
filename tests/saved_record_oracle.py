"""Frozen oracle: the saved-record validators as they were before the typed models.

The functions below are copied verbatim from commit 3e085c7, when every check was hand-written.
They are a reference only and are never called from src. tests/test_saved_record_equivalence.py
compares validate_run_record and source_qualifications with them, outcome and message, over a
mutation corpus from the fixtures. A change to a check is a deliberate change to this file.
"""

import re
from datetime import datetime

from mentat.canonical import STATUSES, digest, json_bytes
from mentat.evidence_report import claim_groups
from mentat.project import Assurance

_CASE_STATUSES = {"pass", "fail", "skip", "error"}


def _string(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid saved verification record")
    return value


def _strings(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("Invalid saved verification record")
    return value


def _check(check, claims, artifacts, kind):
    if not isinstance(check, dict):
        raise ValueError("Invalid saved check")
    _string(check.get("id"))
    if check.get("claim") not in claims or check.get("status") not in STATUSES:
        raise ValueError("Invalid saved check reference or status")
    if kind == "formal" and check.get("kind") not in {"z3", "lean", "lean-comparator"}:
        raise ValueError("Invalid saved formal engine")
    if not isinstance(check.get("detail"), str):
        raise ValueError("Invalid saved check detail")
    _references(check, artifacts)
    if kind == "behavior":
        _cases(check)
        if (
            "exit_code" in check
            and check["exit_code"] is not None
            and type(check["exit_code"]) is not int
        ):
            raise ValueError("Invalid saved exit code")
        if "command_status" in check and check["command_status"] not in STATUSES:
            raise ValueError("Invalid saved command status")
        if "negative_control" in check:
            control = check["negative_control"]
            if not isinstance(control, dict) or control.get("status") not in STATUSES:
                raise ValueError("Invalid saved negative control")
            if not isinstance(control.get("detail"), str):
                raise ValueError("Invalid saved negative control detail")
            if "qualified" in control and type(control["qualified"]) is not bool:
                raise ValueError("Invalid saved control qualification")
            _references(control, artifacts)
            _cases(control)
            if (
                "exit_code" in control
                and control["exit_code"] is not None
                and type(control["exit_code"]) is not int
            ):
                raise ValueError("Invalid saved control exit code")
    elif "evidence" in check and not isinstance(check["evidence"], dict):
        raise ValueError("Invalid saved formal evidence")


def _filename(name):
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Invalid saved evidence filename")
    return name


def _references(check, artifacts):
    refs = check.get("artifacts", {})
    if not isinstance(refs, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or v not in artifacts
        for k, v in refs.items()
    ):
        raise ValueError("Invalid saved artifact reference")
    for name in (*refs.keys(), *refs.values()):
        _filename(name)
    for key in ("stdout", "stderr"):
        if key in check and (not isinstance(check[key], str) or check[key] not in artifacts):
            raise ValueError("Invalid saved log reference")
    if "artifact_omissions" in check:
        for name in _strings(check["artifact_omissions"]):
            _filename(name)
    if "log_omissions" in check:
        streams = _strings(check["log_omissions"])
        if len(set(streams)) != len(streams) or any(
            name not in {"stdout", "stderr"} for name in streams
        ):
            raise ValueError("Invalid saved log omissions")


def _cases(check):
    if "required_cases" in check:
        names = _strings(check["required_cases"])
        if len(set(names)) != len(names) or any(not name for name in names):
            raise ValueError("Invalid saved required cases")
    if "case_results" in check:
        results = check["case_results"]
        if not isinstance(results, dict) or any(
            not isinstance(name, str) or not name or result not in _CASE_STATUSES
            for name, result in results.items()
        ):
            raise ValueError("Invalid saved case results")
        if "required_cases" in check and set(results) - set(check["required_cases"]):
            raise ValueError("Unexpected saved case result")


def validate_record(record):
    if (
        not isinstance(record, dict)
        or record.get("kind") != "project-verification"
        or (type(record.get("schema_version")) is not int or record["schema_version"] != 2)
    ):
        raise ValueError("Unsupported saved verification record")
    for key in ("run_id", "revision", "base", "contract_digest", "runner"):
        _string(record.get(key))
    if any(
        not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", record[key])
        for key in ("revision", "base")
    ) or not re.fullmatch(r"[0-9a-f]{64}", record["contract_digest"]):
        raise ValueError("Invalid saved revision or contract digest")
    if record.get("status") not in STATUSES or record["runner"] not in {"host", "docker"}:
        raise ValueError("Invalid saved verification outcome")
    if "recorded_at" in record:
        timestamp = datetime.fromisoformat(_string(record["recorded_at"]))
        if timestamp.tzinfo is None:
            raise ValueError("Invalid saved timestamp")
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in artifacts.items()
    ):
        raise ValueError("Invalid saved artifact inventory")
    for name in artifacts:
        _filename(name)
    claims = record.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("Invalid saved claims")
    ids = set()
    for claim in claims:
        if not isinstance(claim, dict) or _string(claim.get("id")) in ids:
            raise ValueError("Invalid saved claims")
        ids.add(claim["id"])
        _string(claim.get("statement"))
        _strings(claim.get("assumptions", []))
        if claim.get("assurance") is not None:
            Assurance.model_validate(claim["assurance"])
    review = record.get("claim_review", {})
    if not isinstance(review, dict) or not isinstance(review.get("titles", {}), dict):
        raise ValueError("Invalid saved claim review")
    if any(k not in ids or not isinstance(v, str) for k, v in review.get("titles", {}).items()):
        raise ValueError("Invalid saved claim titles")
    _strings(review.get("verification_limits") or [])
    groups = review.get("groups", [])
    if not isinstance(groups, list):
        raise ValueError("Invalid saved claim groups")
    grouped = []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("Invalid saved claim groups")
        _string(group.get("title"))
        grouped.extend(_strings(group.get("claim_ids")))
    if groups and (len(grouped) != len(ids) or set(grouped) != ids):
        raise ValueError("Invalid saved claim group references")
    if (
        type(record.get("behavior_skipped")) is not bool
        or not isinstance(record.get("model_checks"), list)
        or not isinstance(record.get("cases"), list)
    ):
        raise ValueError("Invalid saved check inventory")
    seen = set()
    for check in record["model_checks"]:
        _check(check, ids, artifacts, "formal")
        key = check["id"]
        if key in seen:
            raise ValueError("Duplicate saved check")
        seen.add(key)
    checks = []
    for case in record["cases"]:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("checks"), list)
            or not case["checks"]
        ):
            raise ValueError("Invalid saved behavior cases")
        if "case_id" in case:
            _string(case["case_id"])
        if "status" in case and case["status"] not in STATUSES:
            raise ValueError("Invalid saved behavior case status")
        if len(case["checks"]) == 1:
            check = case["checks"][0]
            if (
                not isinstance(check, dict)
                or ("case_id" in case and case["case_id"] != check.get("id"))
                or ("status" in case and case["status"] != check.get("status"))
            ):
                raise ValueError("Invalid saved behavior case reference")
        checks.extend(case["checks"])
    if record["behavior_skipped"] and checks:
        raise ValueError("Invalid formal gate evidence")
    for check in checks:
        _check(check, ids, artifacts, "behavior")
        if check["id"] in seen:
            raise ValueError("Duplicate saved check")
        seen.add(check["id"])
    inventory = record.get("behavior_checks")
    if inventory is not None:
        if not isinstance(inventory, list):
            raise ValueError("Invalid saved behavior inventory")
        declared = {}
        for item in inventory:
            if (
                not isinstance(item, dict)
                or _string(item.get("id")) in declared
                or item.get("claim") not in ids
            ):
                raise ValueError("Invalid saved behavior inventory")
            declared[item["id"]] = item["claim"]
        if len(set(declared) & {check["id"] for check in record["model_checks"]}):
            raise ValueError("Duplicate saved check")
        if not record["behavior_skipped"] and declared != {
            check["id"]: check["claim"] for check in checks
        }:
            raise ValueError("Saved behavior inventory does not match results")
    for check in source_qualifications(record):
        _references(check, artifacts)
        _cases(check)
    return claim_groups(record)


def _check_evidence(check: dict, artifacts: dict) -> dict:
    paths = check.get("artifacts", {})
    logs = {stream: check[stream] for stream in ("stdout", "stderr") if stream in check}
    if (
        not isinstance(artifacts, dict)
        or not isinstance(paths, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in paths.items()
        )
        or any(not isinstance(value, str) for value in logs.values())
    ):
        raise ValueError("Invalid saved check evidence references")
    if (set(paths.values()) | set(logs.values())) - artifacts.keys():
        raise ValueError("Saved check references files outside its evidence manifest")
    result = {
        "status": check["status"],
        "detail": check["detail"],
        "artifacts": paths,
        "logs": logs,
    }
    if "command_status" in check:
        result.update(command_status=check["command_status"], exit_code=check["exit_code"])
    for key in (
        "case_results",
        "required_cases",
        "qualified",
        "outcome",
        "bindings",
        "applied_count",
        "assertion_failed",
        "expected_case",
        "check",
    ):
        if key in check:
            result[key] = check[key]
    if check.get("kind") == "lean-comparator":
        info = check.get("evidence", {})
        result["proof"] = {
            key: info.get(key, "unavailable")
            for key in ("campaign_id", "theorem", "source_sha256", "approved_sha256", "environment")
        }
    return result


def source_qualifications(record: dict) -> list[dict]:
    """Frozen source qualification check, as it was before the models."""
    if not ({"source_mutation_checks", "source_qualifications"} & record.keys()):
        return []
    if (
        not {"claims", "behavior_skipped", "cases"} <= record.keys()
        or not isinstance(record["claims"], list)
        or type(record["behavior_skipped"]) is not bool
        or not isinstance(record["cases"], list)
        or any(
            not isinstance(case, dict)
            or not isinstance(case.get("checks"), list)
            or any(
                not isinstance(check, dict)
                or not isinstance(check.get("id"), str)
                or not isinstance(check.get("status"), str)
                or check["status"] not in STATUSES
                for check in case["checks"]
            )
            for case in record["cases"]
        )
        or any(
            not isinstance(claim, dict) or not isinstance(claim.get("id"), str)
            for claim in record["claims"]
        )
    ):
        raise ValueError("Invalid source qualification context")
    inventory = record.get("source_mutation_checks")
    results = record.get("source_qualifications")
    if (
        not isinstance(inventory, list)
        or not inventory
        or len(inventory) > 128
        or not isinstance(results, list)
    ):
        raise ValueError("Invalid source qualification inventory")
    claims = {claim["id"] for claim in record["claims"]}
    declared = {}
    for item in inventory:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"]
            or item["id"] in declared
            or not isinstance(item.get("claim"), str)
            or item["claim"] not in claims
            or not isinstance(item.get("check"), str)
            or not isinstance(item.get("required_cases"), list)
            or not item["required_cases"]
            or any(not isinstance(case, str) for case in item["required_cases"])
            or len(set(item["required_cases"])) != len(item["required_cases"])
            or item.get("expected_case") not in item["required_cases"]
            or not isinstance(item.get("violation_exit_codes"), list)
            or not item["violation_exit_codes"]
            or any(
                type(code) is not int or not 1 <= code <= 124
                for code in item["violation_exit_codes"]
            )
        ):
            raise ValueError("Invalid source qualification declaration")
        declared[item["id"]] = item
    if record["behavior_skipped"]:
        if results:
            raise ValueError("Source qualifications ran despite the formal gate")
        return [
            {**item, "status": "skipped", "detail": "Formal checks did not pass"}
            for item in inventory
        ]
    baseline = {check["id"]: check for case in record["cases"] for check in case["checks"]}
    if len(baseline) != sum(len(case["checks"]) for case in record["cases"]):
        raise ValueError("Duplicate source qualification baseline check")
    if (
        not {
            "run_id",
            "revision",
            "snapshot_digest",
            "base",
            "contract_digest",
            "runner",
            "runtime",
            "comparison_inputs",
            "source_mutation_manifest_sha256",
        }
        <= record.keys()
        or not isinstance(record["runtime"], dict)
        or not isinstance(record["comparison_inputs"], dict)
        or not isinstance(record["comparison_inputs"].get("verifier"), str)
        or not isinstance(record["source_mutation_manifest_sha256"], str)
    ):
        raise ValueError("Invalid source qualification context")
    observed_ids = set()
    projected = []
    for result in results:
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("id"), str)
            or result["id"] not in declared
            or result["id"] in observed_ids
        ):
            raise ValueError("Invalid source qualification result")
        observed_ids.add(result["id"])
        item = declared[result["id"]]
        binding = result.get("bindings")
        expected_binding = {
            key: record[key]
            for key in ("run_id", "revision", "snapshot_digest", "base", "contract_digest")
        }
        expected_binding.update(
            verifier_digest=record["comparison_inputs"]["verifier"],
            approved_manifest_sha256=record["source_mutation_manifest_sha256"],
        )
        if (
            type(result.get("schema_version")) is not int
            or result["schema_version"] != 1
            or any(result.get(key) != item[key] for key in ("claim", "check", "expected_case"))
            or not isinstance(binding, dict)
            or any(binding.get(key) != value for key, value in expected_binding.items())
            or binding.get("runtime")
            != {"runner": record["runner"], "sha256": digest(json_bytes(record["runtime"]))}
            or not isinstance(result.get("outcome"), str)
            or result["outcome"] not in {"killed", "survived", "error"}
            or result.get("status")
            != {"killed": "pass", "survived": "violation", "error": "incomplete"}.get(
                result.get("outcome")
            )
            or type(result.get("applied_count")) is not int
            or result["applied_count"] not in (0, 1)
            or not isinstance(result.get("detail"), str)
            or result.get("assertion_failed") is not None
            and type(result["assertion_failed"]) is not bool
        ):
            raise ValueError("Source qualification bindings or outcome are invalid")
        check = result.get("check_result")
        cases = result.get("case_results")
        if not isinstance(cases, dict) or any(
            case not in item["required_cases"]
            or not isinstance(outcome, str)
            or outcome not in {"pass", "fail", "skip", "error"}
            for case, outcome in cases.items()
        ):
            raise ValueError("Invalid source qualification cases")
        refs = {}
        if check is not None:
            if (
                not isinstance(check, dict)
                or check.get("id") != item["check"]
                or check.get("claim") != item["claim"]
                or not isinstance(check.get("status"), str)
                or check["status"] not in STATUSES
                or check.get("case_results", {}) != cases
                or result["assertion_failed"] != (cases.get(item["expected_case"]) == "fail")
            ):
                raise ValueError("Invalid mutated check attribution")
            refs = {
                key: check[key]
                for key in ("artifacts", "stdout", "stderr", "artifact_omissions", "log_omissions")
                if key in check
            }
            if "artifacts" in record:
                _check_evidence(check, record["artifacts"])
        elif cases or result["assertion_failed"] is not None:
            raise ValueError("Source qualification lacks its underlying check")
        if (
            result["outcome"] == "error"
            and check is not None
            and set(cases) == set(item["required_cases"])
            and all(value in {"pass", "fail"} for value in cases.values())
            and not check.get("artifact_omissions")
            and not check.get("log_omissions")
            and (
                (
                    check["status"] == "violation"
                    and check.get("exit_code") in item["violation_exit_codes"]
                    and result["assertion_failed"] is True
                )
                or (
                    check["status"] == "pass"
                    and check.get("exit_code") == 0
                    and all(value == "pass" for value in cases.values())
                )
            )
        ):
            raise ValueError("Source qualification error contradicts conclusive evidence")
        if result["outcome"] != "error":
            original = baseline.get(item["check"], {})
            if (
                original.get("status") != "pass"
                or original.get("command_status") != "pass"
                or original.get("exit_code") != 0
                or original.get("claim") != item["claim"]
                or original.get("case_results") != dict.fromkeys(item["required_cases"], "pass")
                or result["applied_count"] != 1
                or check is None
                or set(cases) != set(item["required_cases"])
                or any(value not in {"pass", "fail"} for value in cases.values())
                or not all(
                    isinstance(binding.get(key), str)
                    and re.fullmatch(r"[0-9a-f]{64}", binding[key])
                    for key in ("source_before_sha256", "source_after_sha256")
                )
                or binding["source_before_sha256"] == binding["source_after_sha256"]
            ):
                raise ValueError(
                    "Source qualification lacks complete baseline or mutation evidence"
                )
            if result["outcome"] == "killed":
                valid = (
                    check["status"] == "violation"
                    and check.get("exit_code") in item["violation_exit_codes"]
                    and result["assertion_failed"] is True
                )
            else:
                valid = (
                    check["status"] == "pass"
                    and check.get("exit_code") == 0
                    and all(value == "pass" for value in cases.values())
                )
            if not valid:
                raise ValueError("Source qualification contradicts the expected assertion")
        projected.append({**result, **refs, "required_cases": item["required_cases"]})
    if observed_ids != declared.keys():
        raise ValueError("Missing source qualification result")
    return projected
