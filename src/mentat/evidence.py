"""Operator records, content fingerprints, and local evidence bundles."""

import fcntl
import hashlib
import os
import re
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from . import records

# canonical's helpers are re-exported here: callers use evidence.json_bytes and friends.
from .canonical import STATUSES, decode_json, digest, json_bytes, status
from .project_formal import LEAN_NAME_PATTERN

RUN_ID = re.compile(r"[a-f0-9]{32}\Z")
# Every checker version that has ever written saved proofs, including the current one.
# A checker bump must add the new version here.
_SAVED_CHECKER_VERSIONS = frozenset({"4.35.0"})


def read_json(path: Path):
    with path.open("rb") as stream:
        return decode_json(stream.read(1_000_001))


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def qualification_status(checks: list[dict]) -> str:
    """Summarize the source qualifications that ran; a formal-gated run ran none."""
    ran = [check for check in checks if check["status"] != "skipped"]
    return status(ran, incomplete_first=True) if ran else "skipped"


def acceptable(record: dict) -> bool:
    """A product pass is acceptable only when its recorded source qualification also passes."""
    return record["status"] == "pass" and record.get("source_qualification_status", "pass") == "pass"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_record(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def state_directory(path: Path, *, create: bool = True, candidate_root: Path | None = None) -> Path:
    try:
        path = path.expanduser().resolve()
    except RuntimeError as exc:
        raise ValueError(f"Could not resolve Mentat state path: {exc}") from exc
    if candidate_root is not None and (
        path.is_relative_to(candidate_root) or candidate_root.is_relative_to(path)
    ):
        raise ValueError("Mentat state must be outside the candidate repository")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    stat = path.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise ValueError("Mentat state must be an operator-owned directory with mode 0700")
    return path


def save_run(state: Path, record: dict, artifacts: dict[str, bytes]) -> Path:
    run_id = record["run_id"]
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid run identifier")
    if {"bundle_format", "artifacts", "manifest.json", "report.md"} & record.keys():
        raise ValueError("Reserved evidence record key")
    for name in artifacts:
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name in {".", "..", "bundle_format", "artifacts", "manifest.json", "report.md"}
        ):
            raise ValueError("Invalid or reserved evidence filename")
    manifest = json_bytes(
        {
            **record,
            "bundle_format": 2,
            "artifacts": {name: digest(data) for name, data in artifacts.items()},
        }
    )
    runs = state / "runs"
    runs.mkdir(mode=0o700, exist_ok=True)
    destination = runs / run_id
    with tempfile.TemporaryDirectory(dir=runs) as staging:
        bundle = Path(staging) / "bundle"
        bundle.mkdir(mode=0o700)
        for name, data in {**artifacts, "manifest.json": manifest}.items():
            if Path(name).name != name or name in {".", ".."}:
                raise ValueError("Invalid evidence filename")
            write_record(bundle / name, data)
        # The bundle rename publishes the run. Serialize receipt updates with that rename.
        with (state / ".publish.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"Evidence run already exists: {run_id}")
            write_record(state / "receipts" / run_id, digest(manifest).encode())
            bundle.rename(destination)
    return destination


def read_run_snapshot(state: Path, run_id: str) -> tuple[Path, dict, str]:
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid run identifier")
    bundle = state / "runs" / run_id
    manifest = bundle / "manifest.json"
    if bundle.is_symlink() or manifest.is_symlink():
        raise ValueError("Evidence path must be a regular bundle")
    raw = manifest.read_bytes()
    if digest(raw) != (state / "receipts" / run_id).read_text():
        raise ValueError("Evidence manifest failed its local integrity check")
    # Saved manifests have no size cap, but duplicate keys are refused.
    record = decode_json(raw, limit=None)
    if not isinstance(record, dict) or not isinstance(record.get("artifacts"), dict):
        raise ValueError("Invalid saved evidence manifest")
    if "bundle_format" in record:
        if type(record["bundle_format"]) is not int or record["bundle_format"] != 2:
            raise ValueError("Unsupported evidence bundle format")
        if "report.md" in record["artifacts"]:
            raise ValueError("Invalid machine evidence bundle")
    elif "report.md" not in record["artifacts"]:
        raise ValueError("Legacy evidence bundle missing report")
    if any(
        not isinstance(name, str)
        or Path(name).name != name
        or name in {".", "..", "manifest.json"}
        for name in record["artifacts"]
    ):
        raise ValueError("Invalid evidence filename")
    if record["run_id"] != run_id or set(path.name for path in bundle.iterdir()) != {
        "manifest.json",
        *record["artifacts"],
    }:
        raise ValueError("Evidence file set changed")
    for name, expected in record["artifacts"].items():
        path = bundle / name
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected:
            raise ValueError(f"Evidence artifact failed its integrity check: {name}")
    proof_evidence(record)
    split = "source_qualification_status" in record
    if split and not {"source_mutation_checks", "source_qualifications"} <= record.keys():
        raise ValueError("Saved qualification verdict lacks its qualification evidence")
    if split or "source_mutation_checks" in record or "source_qualifications" in record:
        qualifications = source_qualifications(record)
        checks = record.get("model_checks")
        if not isinstance(checks, list) or any(
            not isinstance(check, dict)
            or not isinstance(check.get("status"), str)
            or check["status"] not in STATUSES
            for check in checks
        ):
            raise ValueError("Invalid qualification formal context")
        executed = [check for case in record["cases"] for check in case["checks"]]
        if split:
            if record["status"] != status(checks + executed, incomplete_first=True):
                raise ValueError("Saved verdict contradicts product checks")
            if record["source_qualification_status"] != qualification_status(qualifications):
                raise ValueError("Saved qualification verdict contradicts qualification evidence")
        elif record["status"] != status(
            checks + executed + [check for check in qualifications if check["status"] != "skipped"],
            incomplete_first=True,
        ):
            raise ValueError("Saved verdict contradicts qualification evidence")
    return bundle, record, digest(raw)


def read_run(state: Path, run_id: str) -> tuple[Path, dict]:
    bundle, record, _ = read_run_snapshot(state, run_id)
    return bundle, record


def proof_evidence(record: dict) -> None:
    """Check saved proof identities without rerunning an historical toolchain."""
    environments = []
    for check in record.get("model_checks", []):
        if not isinstance(check, dict) or check.get("kind") != "lean-comparator":
            continue
        _model(records.ProofCheck, check, "Invalid saved proof evidence")
        info = check.get("evidence", {})
        if "campaign_id" not in info:
            if check.get("status") == "pass":
                raise ValueError("Passing proof has no campaign evidence")
            continue
        if not isinstance(info["campaign_id"], str) or not RUN_ID.fullmatch(info["campaign_id"]):
            raise ValueError("Invalid saved proof campaign identity")
        for key in ("source_sha256", "approved_sha256", "verifier_digest"):
            value = info.get(key)
            if value is None and key == "source_sha256" and check.get("status") != "pass":
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("Invalid saved proof source identity")
        theorem = info.get("theorem")
        if not isinstance(theorem, str) or not LEAN_NAME_PATTERN.fullmatch(theorem):
            raise ValueError("Invalid saved proof theorem identity")
        expected = {key: record.get(key) for key in ("revision", "base", "contract_digest")}
        expected["verifier_digest"] = record.get("comparison_inputs", {}).get("verifier")
        if any(info.get(key) != value for key, value in expected.items()):
            raise ValueError("Saved proof does not match its verification run")
        environment = info.get("environment")
        version = environment.get("version") if isinstance(environment, dict) else None
        if not isinstance(version, str) or version not in _SAVED_CHECKER_VERSIONS:
            raise ValueError("Invalid saved proof environment")
        for key in ("toolchain_sha256", "bwrap_sha256", "policy_sha256"):
            value = environment.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("Invalid saved proof environment hash")
        if environments and environment != environments[0]:
            raise ValueError("Saved proofs have conflicting checker environments")
        environments.append(environment)
        recorded_tool = record.get("tools", {}).get("lean-comparator")
        if recorded_tool is not None and recorded_tool != environment:
            raise ValueError("Saved proof environment does not match the recorded checker")
        if check.get("status") == "pass":
            source = check.get("artifacts", {}).get("proof-source")
            if record.get("artifacts", {}).get(source) != info.get("source_sha256"):
                raise ValueError("Passing proof source is missing or mismatched")


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
    """Validate source qualification bindings and project their evidence references."""
    if not ({"source_mutation_checks", "source_qualifications"} & record.keys()):
        return []
    message = "Invalid source qualification context"
    _model(records.QualificationContext, record, message)
    for claim in record["claims"]:
        _model(records.ClaimId, claim, message)
    for case in record["cases"]:
        _model(records.CaseChecks, case, message)
        for check in case["checks"]:
            _model(records.CheckStatus, check, message)
            if check["status"] not in STATUSES:
                raise ValueError(message)
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
        try:
            records.check(records.SourceMutationCheck, item)
        except ValidationError as exc:
            raise ValueError("Invalid source qualification declaration") from exc
        if (
            not item["id"]
            or item["id"] in declared
            or item["claim"] not in claims
            or len(set(item["required_cases"])) != len(item["required_cases"])
            or item["expected_case"] not in item["required_cases"]
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
    _model(records.QualificationRun, record, "Invalid source qualification context")
    if not isinstance(record["comparison_inputs"].get("verifier"), str):
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
        try:
            records.check(records.SourceQualification, result)
        except ValidationError as exc:
            raise ValueError("Source qualification bindings or outcome are invalid") from exc
        binding = result["bindings"]
        expected_binding = {
            key: record[key]
            for key in ("run_id", "revision", "snapshot_digest", "base", "contract_digest")
        }
        expected_binding.update(
            verifier_digest=record["comparison_inputs"]["verifier"],
            approved_manifest_sha256=record["source_mutation_manifest_sha256"],
        )
        if (
            any(result[key] != item[key] for key in ("claim", "check", "expected_case"))
            or any(binding.get(key) != value for key, value in expected_binding.items())
            or binding.get("runtime")
            != {"runner": record["runner"], "sha256": digest(json_bytes(record["runtime"]))}
            or result["outcome"] not in {"killed", "survived", "error"}
            or result["status"]
            != {"killed": "pass", "survived": "violation", "error": "incomplete"}.get(
                result["outcome"]
            )
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


_RECORD_INVALID = "Invalid saved verification record"
_CASE_STATUSES = frozenset({"pass", "fail", "skip", "error"})
_ENGINES = frozenset({"z3", "lean", "lean-comparator"})


def _model(model, value: dict, message: str | dict[str, str]) -> None:
    """Validate one saved section with its model; a defect raises that section's message."""
    try:
        records.check(model, value)
    except ValidationError as exc:
        location = exc.errors()[0]["loc"]
        if isinstance(message, dict):
            raise ValueError(message.get(location[0] if location else "", _RECORD_INVALID)) from exc  # pyright: ignore[reportCallIssue, reportArgumentType]  # pydantic loc entries may be ints; dict.get then returns the default
        raise ValueError(message) from exc


def _string(value) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(_RECORD_INVALID)
    return value


def _strings(value) -> list:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(_RECORD_INVALID)
    return value


def _filename(name) -> str:
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Invalid saved evidence filename")
    return name


def _reference_values(check: dict, artifacts: dict) -> None:
    """Check that a result's artifact and log references name saved files."""
    refs = check.get("artifacts", {})
    if any(not isinstance(value, str) or value not in artifacts for value in refs.values()):
        raise ValueError("Invalid saved artifact reference")
    for name in (*refs.keys(), *refs.values()):
        _filename(name)
    for key in ("stdout", "stderr"):
        if key in check and (not isinstance(check[key], str) or check[key] not in artifacts):
            raise ValueError("Invalid saved log reference")


# The helpers below run in the order the old hand checks did, so the first defect reported is
# the same. Each model checks one field; defaults stand in for absent keys.


def _artifact_omissions(check: dict) -> None:
    _model(records.ArtifactOmissions, check, _RECORD_INVALID)
    for name in check.get("artifact_omissions", []):
        _filename(name)


def _log_omissions(check: dict) -> None:
    _model(records.LogOmissions, check, _RECORD_INVALID)
    streams = check.get("log_omissions", [])
    if len(set(streams)) != len(streams) or any(
        name not in {"stdout", "stderr"} for name in streams
    ):
        raise ValueError("Invalid saved log omissions")


def _required_cases(check: dict) -> None:
    _model(records.RequiredCases, check, _RECORD_INVALID)
    names = check.get("required_cases", [])
    if len(set(names)) != len(names) or any(not name for name in names):
        raise ValueError("Invalid saved required cases")


def _case_results(check: dict) -> None:
    _model(records.CaseResults, check, "Invalid saved case results")
    results = check.get("case_results", {})
    if any(
        not isinstance(name, str) or not name or result not in _CASE_STATUSES
        for name, result in results.items()
    ):
        raise ValueError("Invalid saved case results")
    if "required_cases" in check and set(results) - set(check["required_cases"]):
        raise ValueError("Unexpected saved case result")


def _formal_check(check, claims: set, artifacts: dict) -> None:
    if not isinstance(check, dict):
        raise ValueError("Invalid saved check")
    _string(check.get("id"))
    if check.get("claim") not in claims or check.get("status") not in STATUSES:
        raise ValueError("Invalid saved check reference or status")
    if check.get("kind") not in _ENGINES:
        raise ValueError("Invalid saved formal engine")
    _model(
        records.FormalCheck,
        check,
        {"detail": "Invalid saved check detail", "artifacts": "Invalid saved artifact reference"},
    )
    _reference_values(check, artifacts)
    _artifact_omissions(check)
    _log_omissions(check)
    _model(records.FormalEvidence, check, "Invalid saved formal evidence")


def _behavior_check(check, claims: set, artifacts: dict) -> None:
    if not isinstance(check, dict):
        raise ValueError("Invalid saved check")
    _string(check.get("id"))
    if check.get("claim") not in claims or check.get("status") not in STATUSES:
        raise ValueError("Invalid saved check reference or status")
    _model(
        records.BehaviorCheck,
        check,
        {"detail": "Invalid saved check detail", "artifacts": "Invalid saved artifact reference"},
    )
    _reference_values(check, artifacts)
    _artifact_omissions(check)
    _log_omissions(check)
    _required_cases(check)
    _case_results(check)
    _model(records.ExitCode, check, "Invalid saved exit code")
    if "command_status" in check and check["command_status"] not in STATUSES:
        raise ValueError("Invalid saved command status")
    if "negative_control" in check:
        _negative_control(check["negative_control"], artifacts)


def _negative_control(control, artifacts: dict) -> None:
    if not isinstance(control, dict):
        raise ValueError("Invalid saved negative control")
    if control.get("status") not in STATUSES:
        raise ValueError("Invalid saved negative control")
    _model(
        records.NegativeControl,
        control,
        {
            "detail": "Invalid saved negative control detail",
            "qualified": "Invalid saved control qualification",
            "artifacts": "Invalid saved artifact reference",
        },
    )
    _reference_values(control, artifacts)
    _artifact_omissions(control)
    _log_omissions(control)
    _required_cases(control)
    _case_results(control)
    _model(records.ExitCode, control, "Invalid saved control exit code")


def validate_run_record(record) -> None:
    """Check a saved project-verification record's sections and how they reference each other."""
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
    _model(records.RunArtifacts, record, "Invalid saved artifact inventory")
    artifacts = record["artifacts"]
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in artifacts.items()):
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
        _model(records.Claim, claim, _RECORD_INVALID)
        if claim.get("assurance") is not None:
            # Not records.check: the contract shape rejects unknown keys, as it always has.
            records.Assurance.model_validate(claim["assurance"])
    review = record.get("claim_review", {})
    if not isinstance(review, dict) or not isinstance(review.get("titles", {}), dict):
        raise ValueError("Invalid saved claim review")
    if any(
        not isinstance(title, str) or key not in ids
        for key, title in review.get("titles", {}).items()
    ):
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
        _model(records.ReviewGroup, group, _RECORD_INVALID)
        grouped.extend(group["claim_ids"])
    if groups and (len(grouped) != len(ids) or set(grouped) != ids):
        raise ValueError("Invalid saved claim group references")
    _model(records.RunInventory, record, "Invalid saved check inventory")
    seen = set()
    for check in record["model_checks"]:
        _formal_check(check, ids, artifacts)
        if check["id"] in seen:
            raise ValueError("Duplicate saved check")
        seen.add(check["id"])
    checks = []
    for case in record["cases"]:
        if not isinstance(case, dict):
            raise ValueError("Invalid saved behavior cases")
        _model(records.BehaviorCase, case, "Invalid saved behavior cases")
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
        _behavior_check(check, ids, artifacts)
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
        _model(records.References, check, "Invalid saved artifact reference")
        _reference_values(check, artifacts)
        _artifact_omissions(check)
        _log_omissions(check)
        _required_cases(check)
        _case_results(check)


def _checks_by_id(record: dict) -> dict[tuple[str, str], dict]:
    result = {}
    for kind, checks in (
        ("formal", record["model_checks"]),
        ("behavior", [check for case in record["cases"] for check in case["checks"]]),
        ("source-mutation", source_qualifications(record)),
    ):
        for check in checks:
            key = kind, check["id"]
            if kind == "source-mutation" and check["status"] == "skipped":
                result[key] = {"claim": check["claim"], "status": "skipped"}
                continue
            if key in result or check["status"] not in STATUSES:
                raise ValueError("Invalid saved check results")
            artifact_index = record["artifacts"]
            result[key] = {
                "claim": check["claim"],
                **_check_evidence(check, artifact_index),
            }
            if "negative_control" in check:
                result[key]["negative_control"] = _check_evidence(
                    check["negative_control"], artifact_index
                )
    if record["behavior_skipped"]:
        for check in record.get("behavior_checks", []):
            key = "behavior", check["id"]
            if key in result:
                raise ValueError("Invalid saved behavior check inventory")
            result[key] = {"claim": check["claim"], "status": "skipped"}
    return result


def _run_reference(path: Path, run: dict) -> dict:
    if run["status"] not in STATUSES:
        raise ValueError("Invalid saved run outcome")
    reference = {key: run[key] for key in ("run_id", "revision", "status", "behavior_skipped")}
    if "source_qualification_status" in run:
        reference["source_qualification_status"] = run["source_qualification_status"]
    reference.update(bundle=str(path), manifest=str(path / "manifest.json"))
    return reference


def _changed(before: str | None, after: str | None) -> str:
    if before is None or after is None:
        return "unavailable"
    return "changed" if before != after else "unchanged"


def _historical_claims(run: dict) -> tuple[dict, dict]:
    metadata = run.get("comparison_inputs", {})
    definitions = metadata.get("claims", {})
    titles = run.get("claim_review", {}).get("titles", {})
    claims = {
        claim["id"]: {
            **claim,
            "title": titles.get(claim["id"]),
            "definition_digest": definitions.get(claim["id"]),
            "checks": [],
        }
        for claim in run["claims"]
    }
    if len(claims) != len(run["claims"]):
        raise ValueError("Duplicate saved claim IDs")
    for (kind, check_id), check in _checks_by_id(run).items():
        claims[check["claim"]]["checks"].append(
            {"kind": kind, "id": check_id, **{k: v for k, v in check.items() if k != "claim"}}
        )
    return claims, metadata


def _compare_contract_history(
    before_path: Path, before: dict, after_path: Path, after: dict
) -> dict:
    scope = ("repo", "runner")
    if changed := [key for key in scope if before[key] != after[key]]:
        raise ValueError(
            "Contract history requires matching scope; differs in " + ", ".join(changed)
        )
    left, old = _historical_claims(before)
    right, new = _historical_claims(after)
    claims = []
    for claim_id in sorted(left.keys() | right.keys()):
        a, b = left.get(claim_id), right.get(claim_id)
        if a is None:
            definition = "added"
        elif b is None:
            definition = "removed"
        else:
            definition = _changed(a["definition_digest"], b["definition_digest"])
        claims.append({"id": claim_id, "definition": definition, "before": a, "after": b})
    return {
        "mode": "across-contracts",
        "status": "incomplete" if "incomplete" in (before["status"], after["status"]) else "pass",
        **{key: before[key] for key in scope},
        "environment_changed": any(before[key] != after[key] for key in ("tools", "runtime")),
        "input_changes": {
            key: _changed(old.get(key), new.get(key))
            for key in ("supporting_files", "runner_settings", "verifier")
        },
        "before": {
            **_run_reference(before_path, before),
            **{key: before[key] for key in ("base", "contract_digest")},
        },
        "after": {
            **_run_reference(after_path, after),
            **{key: after[key] for key in ("base", "contract_digest")},
        },
        "claims": claims,
    }


def compare_runs(
    state: Path, before_id: str, after_id: str, *, across_contracts: bool = False
) -> dict:
    before_path, before = read_run(state, before_id)
    after_path, after = read_run(state, after_id)
    if any(run.get("kind") != "project-verification" for run in (before, after)):
        raise ValueError("Run comparison requires two project verification runs")
    if across_contracts:
        return _compare_contract_history(before_path, before, after_path, after)
    scope = ("repo", "base", "runner", "contract_digest")
    if changed := [key for key in scope if before[key] != after[key]]:
        raise ValueError("Run comparison requires matching scope; differs in " + ", ".join(changed))
    if before["claims"] != after["claims"]:
        raise ValueError("Saved claims differ under the same contract")

    before_checks = _checks_by_id(before)
    after_checks = _checks_by_id(after)
    claims = {claim["id"]: {**claim, "checks": []} for claim in before["claims"]}
    for kind, check_id in dict.fromkeys((*before_checks, *after_checks)):
        left = before_checks.get((kind, check_id))
        right = after_checks.get((kind, check_id))
        if (left is None and not (kind == "behavior" and before["behavior_skipped"])) or (
            right is None and not (kind == "behavior" and after["behavior_skipped"])
        ):
            raise ValueError(f"Saved runs contain different results for check {check_id}")
        if left is not None and right is not None and left["claim"] != right["claim"]:
            raise ValueError(f"Saved runs disagree on the claim for check {check_id}")
        claim = (left or right)["claim"]  # pyright: ignore[reportOptionalSubscript]  # every key comes from at least one side
        outcomes: dict[str, object] = {"id": check_id, "kind": kind}
        for side, check in (("before", left), ("after", right)):
            outcomes[side] = (
                {key: value for key, value in check.items() if key != "claim"}
                if check is not None
                else {"status": "skipped"}
            )
        claims[claim]["checks"].append(outcomes)

    result = {
        "status": "incomplete" if "incomplete" in (before["status"], after["status"]) else "pass",
        **{key: before[key] for key in scope},
        "environment_changed": any(before[key] != after[key] for key in ("tools", "runtime")),
        "claims": list(claims.values()),
    }
    for side, path, run in (("before", before_path, before), ("after", after_path, after)):
        result[side] = _run_reference(path, run)
    return result


def acceptance_status(state, record, digest):
    from .evidence_report import decision_source

    path = state / "acceptances" / (record["run_id"] + ".json")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return "Not recorded"
    except OSError:
        return "Invalid or mismatched receipt"
    try:
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            receipt = decode_json(stream.read(1_000_001))
        try:
            records.check(records.Acceptance, receipt)
        except ValidationError as exc:
            raise ValueError("Invalid or mismatched receipt") from exc
        if any(
            receipt[k] != v
            for k, v in {
                "run_id": record["run_id"],
                "revision": record["revision"],
                "contract_digest": record["contract_digest"],
                "manifest_digest": digest,
            }.items()
        ) or not acceptable(record):
            raise ValueError
        accepted = datetime.fromisoformat(receipt["accepted_at"])
        if accepted.tzinfo is None:
            raise ValueError
        source = decision_source(receipt)
        return "Recorded at " + receipt["accepted_at"] + (f" ({source})" if source else "")
    except (OSError, ValueError, TypeError, KeyError):
        return "Invalid or mismatched receipt"
