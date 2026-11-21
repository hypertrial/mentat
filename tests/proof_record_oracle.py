"""Pre-model hand checks for proof manifests, ledger entries, checker results and saved proof
checks, copied verbatim from the parent commit (WORK-59).

These are a frozen oracle: over a mutation corpus, src/mentat/proof.py and
evidence.proof_evidence must agree with them on outcome, exception type and message. Do not edit
them to follow new code; a difference is a behavior change to list.

The oracle imports _regular, _source, _directory, _hash and _json from the live mentat.proof
module, and they are not copied. WORK-59 does not change them, but a change to any of them moves
both the oracle and the code under test. The constants _LIMIT, _ID and _SAVED_CHECKER_VERSIONS
are copied, and RUN_ID and LEAN_NAME_PATTERN come from their public modules.
"""

import re
from pathlib import Path

from mentat import evidence
from mentat.evidence import RUN_ID
from mentat.project_formal import LEAN_NAME_PATTERN
from mentat.proof import _directory, _hash, _json, _regular, _source

_LIMIT = 1_000_000
_ID = re.compile(r"[0-9a-f]{32}\Z")
_SAVED_CHECKER_VERSIONS = frozenset({"4.35.0"})



def _manifest_shape(manifest):
    # Fields this module dereferences; wrong types or missing keys are user errors, not crashes.
    types = {
        "repo": str, "base": str, "runner": str, "revision": str, "contract_digest": str,
        "verifier_digest": str, "snapshot_digest": str, "approval": dict,
        "checker_identity": dict, "targets": list, "all_targets": list, "obligations": list,
    }
    return (
        isinstance(manifest, dict)
        and all(isinstance(manifest.get(key), kind) for key, kind in types.items())
        and all(isinstance(item, str) for item in manifest["targets"])
        and all(
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("approved_sha256"), str)
            for item in manifest["all_targets"]
        )
    )


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
            if not isinstance(item.get("target"), str):
                raise ValueError("Invalid proof campaign ledger entry")
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


def proof_evidence(record: dict) -> None:
    """Check saved proof identities without rerunning an historical toolchain."""
    environments = []
    for check in record.get("model_checks", []):
        if not isinstance(check, dict) or check.get("kind") != "lean-comparator":
            continue
        info = check.get("evidence", {})
        if not isinstance(info, dict):
            raise ValueError("Invalid saved proof evidence")
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
