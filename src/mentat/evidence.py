"""Operator records, content fingerprints, and local evidence bundles."""

import fcntl
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

RUN_ID = re.compile(r"[a-f0-9]{32}\Z")
STATUSES = {"pass", "violation", "incomplete"}


def read_json(path: Path):
    with path.open("rb") as stream:
        return decode_json(stream.read(1_000_001))


def decode_json(data: bytes):
    if len(data) > 1_000_000:
        raise ValueError("JSON exceeds 1 MB")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            data,
            object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds parser limit") from exc


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def status(checks: list[dict], *, incomplete_first: bool = False) -> str:
    outcomes = {check.get("status") for check in checks}
    if outcomes - STATUSES or not outcomes:
        return "incomplete"
    if incomplete_first and "incomplete" in outcomes:
        return "incomplete"
    if "violation" in outcomes:
        return "violation"
    return "incomplete" if "incomplete" in outcomes else "pass"


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


def report(record: dict) -> bytes:
    lines = [
        "# Mentat verification evidence",
        "",
        f"- Run `{record['run_id']}`",
        f"- Result **{record['status']}**",
        f"- Candidate `{record['revision']}`",
        f"- Approved contract `{record['contract_digest']}`",
        "",
    ]
    if record.get("kind") == "project-verification":
        from .evidence_report import project_report

        lines.extend(project_report(record))
    else:
        for check in record.get("model_checks", []):
            claim = f" · `{check['claim']}`" if "claim" in check else ""
            lines.append(
                f"- {check['kind']} model evidence{claim} · {check['status']} · {check['detail']}"
            )
        if record.get("behavior_skipped"):
            lines.append("- Behavior checks require passing formal checks.")
        for case in record.get("cases", []):
            lines.append(f"- Execution · `{case['case_id']}` · {case['status']}")
            for check in case.get("checks", []):
                detail = check.get("detail", check.get("id", "check"))
                claim = f"`{check['claim']}` · " if "claim" in check else ""
                lines.append(f"  - {claim}{check.get('status', 'incomplete')} · {detail}")
    for regression in record.get("regressions", []):
        lines.append(f"- Native pytest · `{regression['case_id']}` · {regression['status']}")
    for method, counts in record.get("counts", {}).items():
        summary = ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in counts.items())
        lines.append(f"- {method} · {summary}")
    if record.get("kind") == "evaluation":
        lines.append(f"- Formal checks · {record['formal_seconds']} seconds")
        lines.append(
            f"- Native historical and repaired regression · {' / '.join(record['native_regression'])}"
        )
    lines += ["", "## Reproduce", ""]
    if record.get("host_reproduce"):
        lines.extend(f"    {line}" for line in record["host_reproduce"].split("\n"))
        lines += ["", "### Recorded host environment", ""]
        runtime = json.dumps(record["runtime"], ensure_ascii=True, indent=2, sort_keys=True)
        lines.extend(f"    {line}" for line in runtime.splitlines())
        lines += ["", "Replay uses the tools currently installed at the recorded locations."]
    else:
        lines.append(f"`{record['reproduce']}`")
    lines.append("")
    return "\n".join(lines).encode()


def save_run(state: Path, record: dict, artifacts: dict[str, bytes]) -> Path:
    run_id = record["run_id"]
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid run identifier")
    artifacts = {**artifacts, "report.md": report(record)}
    manifest = json_bytes(
        {**record, "artifacts": {name: digest(data) for name, data in artifacts.items()}}
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
    record = json.loads(raw)
    if record["run_id"] != run_id or set(path.name for path in bundle.iterdir()) != {
        "manifest.json",
        *record["artifacts"],
    }:
        raise ValueError("Evidence file set changed")
    for name, expected in record["artifacts"].items():
        path = bundle / name
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected:
            raise ValueError(f"Evidence artifact failed its integrity check: {name}")
    return bundle, record, digest(raw)


def read_run(state: Path, run_id: str) -> tuple[Path, dict]:
    bundle, record, _ = read_run_snapshot(state, run_id)
    return bundle, record


def _check_evidence(check: dict, artifacts: dict) -> dict:
    paths = dict(check.get("artifacts", {}))
    logs = {stream: check[stream] for stream in ("stdout", "stderr") if stream in check}
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
    for key in ("case_results", "required_cases", "qualified"):
        if key in check:
            result[key] = check[key]
    return result


def _checks_by_id(record: dict) -> dict[tuple[str, str], dict]:
    result = {}
    for kind, checks in (
        ("formal", record["model_checks"]),
        ("behavior", [check for case in record["cases"] for check in case["checks"]]),
    ):
        for check in checks:
            key = kind, check["id"]
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
    if run["status"] not in STATUSES or "report.md" not in run["artifacts"]:
        raise ValueError("Invalid saved run outcome or missing report")
    return {
        **{key: run[key] for key in ("run_id", "revision", "status", "behavior_skipped")},
        "bundle": str(path),
        "report": str(path / "report.md"),
    }


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
        claim = (left or right)["claim"]
        outcomes = {"id": check_id, "kind": kind}
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
