"""Qualification of approved behavior checks using bounded source mutations."""

import hashlib
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import PurePosixPath

from . import runner
from .evidence import decode_json, json_bytes
from .project import NAME_PATTERN, safe_relative_path, tree_digest


def _protected(path, base_files):
    parts = path.casefold().split("/")
    name = parts[-1]
    return (
        path.casefold() in {item.casefold() for item in base_files}
        or any(
            part in {".mentat", "test", "tests", "fixture", "fixtures", "__tests__"}
            for part in parts
        )
        or name.startswith(("test_", "test.", "conftest."))
        or ".test." in name
        or ".spec." in name
        or name.endswith((".lock", "-lock.json", ".lockb", ".lock.yaml"))
        or name.startswith(
            (
                "tsconfig.",
                "vite.config.",
                "webpack.config.",
                "jest.config.",
                "vitest.config.",
                "next.config.",
                "rollup.config.",
            )
        )
        or name
        in {
            "makefile",
            "dockerfile",
            "package.json",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "tox.ini",
            "cargo.toml",
            "go.mod",
            "go.sum",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "gradle.properties",
            "tsconfig.json",
            "requirements.txt",
            "requirements-dev.txt",
            "pnpm-workspace.yaml",
            "vite.config.js",
            "vite.config.ts",
            "webpack.config.js",
            "cmakelists.txt",
        }
    )


def approved_mutations(contract, files):
    """Validate the base-approved manifest against the approved behavior contract."""
    config = getattr(contract, "source_mutations", None)
    if config is None:
        return []
    path = config
    if not isinstance(path, str) or safe_relative_path(path) not in files:
        raise ValueError("missing approved source mutation manifest")
    manifest = decode_json(files[path])
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"version", "mutants"}
        or type(manifest["version"]) is not int
        or manifest["version"] != 1
        or not isinstance(manifest["mutants"], dict)
        or not manifest["mutants"]
        or len(manifest["mutants"]) > 128
    ):
        raise ValueError("invalid source mutation manifest")
    checks = {check.id: check for check in contract.behavior}
    claims = {claim.id for claim in contract.claims}
    mutations = []
    for ident, mutation in manifest["mutants"].items():
        if not isinstance(ident, str) or not NAME_PATTERN.fullmatch(ident):
            raise ValueError("invalid source mutation ID")
        if (
            not isinstance(mutation, dict)
            or set(mutation) != {"check", "path", "before", "after", "expected_case"}
            or any(not isinstance(value, str) for value in mutation.values())
        ):
            raise ValueError(f"invalid source mutation: {ident}")
        check = checks.get(mutation["check"])
        if (
            check is None
            or check.claim not in claims
            or check.case_report is None
            or mutation["expected_case"] not in check.case_report.required_cases
        ):
            raise ValueError(f"source mutation requires a named behavior case: {ident}")
        target = safe_relative_path(mutation["path"])
        if _protected(target, contract.base_files):
            raise ValueError(f"protected source mutation path: {target}")
        if not mutation["before"] or mutation["before"] == mutation["after"]:
            raise ValueError(f"source mutation must change nonempty bytes: {ident}")
        try:
            mutation["before"].encode("utf-8")
            mutation["after"].encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"invalid source mutation text: {ident}") from exc
        mutations.append({"id": ident, **mutation, "claim": check.claim})
    return mutations


def _retain_artifacts(observation, produced, check, prefix, remaining):
    """Retain referenced evidence within the campaign budget, prioritizing the case report."""
    observation = deepcopy(observation)
    retained = {}
    valid = True
    refs = observation.get("artifacts", {})
    if not isinstance(refs, dict):
        refs = {}
        valid = False
    observation["artifacts"] = refs
    report = check.case_report.artifact
    if report not in refs:
        observation.setdefault("artifact_omissions", []).append(report)
        valid = False
    ordered = sorted(refs, key=lambda name: name != report)
    for name in ordered:
        ref = refs[name]
        data = produced.get(ref) if isinstance(ref, str) else None
        if not isinstance(data, bytes) or PurePosixPath(ref).name != ref:
            valid = False
            data = None
        if data is None or len(data) > remaining:
            del refs[name]
            observation.setdefault("artifact_omissions", []).append(name)
            if name == report:
                valid = False
            continue
        key = f"{prefix}.{ref}"
        refs[name] = key
        retained[key] = data
        remaining -= len(data)
    for name in ("stdout", "stderr"):
        if name not in observation:
            continue
        ref = observation[name]
        data = produced.get(ref) if isinstance(ref, str) else None
        if not isinstance(data, bytes) or PurePosixPath(ref).name != ref:
            valid = False
            data = None
        if data is None or len(data) > remaining:
            del observation[name]
            observation.setdefault("log_omissions", []).append(name)
            continue
        key = f"{prefix}.{ref}"
        observation[name] = key
        retained[key] = data
        remaining -= len(data)
    return observation, retained, remaining, valid


def run_source_mutations(
    project, snapshot, context, info, run_id, baseline_results, *, output_budget=None, checkpoint=None
):
    mutations = approved_mutations(project.contract, project.files)
    if not mutations:
        return [], {}
    manifest_sha = hashlib.sha256(project.files[project.contract.source_mutations]).hexdigest()
    checks = {check.id: check for check in project.contract.behavior}
    runtime_sha = hashlib.sha256(json_bytes(info)).hexdigest()
    results, artifacts = [], {}
    remaining = runner.MAX_RUN_OUTPUT if output_budget is None else output_budget
    if type(remaining) is not int or not 0 <= remaining <= runner.MAX_RUN_OUTPUT:
        raise ValueError("Invalid source qualification output budget")
    for number, mutation in enumerate(mutations, 1):
        if checkpoint is not None:
            checkpoint()
        check = checks[mutation["check"]]
        required = set(check.case_report.required_cases)
        result = {
            "schema_version": 1,
            "id": mutation["id"],
            "claim": mutation["claim"],
            "check": check.id,
            "expected_case": mutation["expected_case"],
            "path": mutation["path"],
            "status": "incomplete",
            "outcome": "error",
            "detail": "Baseline check did not pass completely",
            "bindings": {
                "run_id": run_id,
                "revision": snapshot.revision,
                "snapshot_digest": snapshot.digest,
                "base": project.base,
                "contract_digest": project.fingerprint,
                "verifier_digest": project.verifier_digest,
                "approved_manifest_sha256": manifest_sha,
                "source_before_sha256": None,
                "source_after_sha256": None,
                "runtime": {"runner": project.runner, "sha256": runtime_sha},
            },
            "applied_count": 0,
            "assertion_failed": None,
            "case_results": {},
        }
        results.append(result)
        baseline = [item for item in baseline_results if item.get("id") == check.id]
        baseline_cases = baseline[0].get("case_results") if len(baseline) == 1 else None
        if (
            len(baseline) != 1
            or baseline[0].get("claim") != check.claim
            or baseline[0].get("status") != "pass"
            or baseline[0].get("command_status") != "pass"
            or baseline[0].get("exit_code") != 0
            or not isinstance(baseline_cases, dict)
            or set(baseline_cases) != required
            or any(value != "pass" for value in baseline_cases.values())
        ):
            continue
        if (
            info.get("runner") != project.runner
            or "path" not in info
            or (project.runner == "docker" and not {"context", "image_id"} <= info.keys())
        ):
            result["detail"] = "Original runtime information is incomplete"
            continue
        entry = snapshot.files.get(mutation["path"])
        if entry is None or entry[0] not in (0o644, 0o755):
            result["detail"] = "Mutation target is missing or not a regular file"
            continue
        mode, source = entry
        result["bindings"]["source_before_sha256"] = hashlib.sha256(source).hexdigest()
        before, after = mutation["before"].encode(), mutation["after"].encode()
        offset = source.find(before)
        if offset < 0 or source.find(before, offset + 1) >= 0:
            result["detail"] = "Mutation must match exactly once"
            continue
        changed = source[:offset] + after + source[offset + len(before) :]
        result["bindings"]["source_after_sha256"] = hashlib.sha256(changed).hexdigest()
        result["applied_count"] = 1
        files = {**snapshot.files, mutation["path"]: (mode, changed)}
        candidate = replace(snapshot, files=files, digest=tree_digest(files))
        isolated = replace(
            project,
            contract=project.contract.model_copy(
                update={"behavior": [check.model_copy(update={"negative_control": None})]}
            ),
        )
        if checkpoint is not None:
            checkpoint()
        try:
            observed, produced = runner.run_behavior(
                isolated, candidate, context, deepcopy(info), run_id
            )
            if len(observed) != 1:
                result["detail"] = "Mutation check returned incomplete results"
                continue
            observation = observed[0]
            if observation.get("id") != check.id or observation.get("claim") != check.claim:
                result["detail"] = "Mutation check returned a mismatched check or claim"
                continue
            observation, retained, remaining, valid = _retain_artifacts(
                observation, produced, check, f"mutation-{number:03d}", remaining
            )
            artifacts.update(retained)
            result["check_result"] = observation
            cases = observation.get("case_results", {})
            if not isinstance(cases, dict):
                result["detail"] = "Mutation check returned invalid case results"
                continue
            result["case_results"] = cases
            result["assertion_failed"] = cases.get(mutation["expected_case"]) == "fail"
            complete = set(cases) == required and all(
                value in ("pass", "fail") for value in cases.values()
            )
            if not valid:
                result["detail"] = "Mutation check evidence was missing or could not be retained"
            elif (
                complete
                and observation.get("status") == "violation"
                and observation.get("exit_code") in check.violation_exit_codes
                and cases.get(mutation["expected_case"]) == "fail"
            ):
                result.update(status="pass", outcome="killed", detail="Expected case failed")
            elif (
                complete
                and observation.get("status") == "pass"
                and observation.get("exit_code") == 0
                and all(value == "pass" for value in cases.values())
            ):
                result.update(status="violation", outcome="survived", detail="Mutation survived")
            else:
                result["detail"] = "Mutation check did not establish the expected outcome"
        except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError):
            result["detail"] = "Mutation check could not complete"
    return results, artifacts
