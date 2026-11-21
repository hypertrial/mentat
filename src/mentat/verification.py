"""Run the approved formal and behavior checks for a candidate commit and save the record."""

import os
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import evidence, spec_review
from .evidence_report import review_context
from .project import Project, load_project, snapshot
from .runner import host_path


def _reproduce(project: Project, state: Path, revision: str, context: str | None) -> str:
    argv = ["mentat", "--state", str(state)]
    if project.runner == "docker" and context:
        argv += ["--docker-context", context]
    return shlex.join(
        argv
        + [
            "verify",
            "--repo",
            str(project.root),
            "--base",
            project.base,
            "--rev",
            revision,
            "--runner",
            project.runner,
            "--json",
        ]
    )


def _proof_recheck(
    state: Path,
    project: Project,
    candidate,
    approved: dict,
    proof_campaign: list[str],
    models: list[dict],
) -> tuple[list[dict], dict[str, bytes]]:
    from . import proof

    checked, artifacts = proof.recheck(state, project, candidate, approved, proof_campaign)
    by_id = {item["id"]: item for item in checked}
    if len(by_id) != len(checked) or set(by_id) - {
        check.id for check in project.contract.formal if check.engine == "lean-comparator"
    }:
        raise ValueError("Proof recheck returned unexpected formal obligations")
    models = [by_id.get(item["id"], item) for item in models]
    from .runner import MAX_RUN_OUTPUT

    # Source evidence is required; proof diagnostics are optional.
    retained = {}
    remaining = MAX_RUN_OUTPUT
    for model in checked:
        filename = model.get("artifacts", {}).get("proof-source")
        if filename:
            data = artifacts[filename]
            if len(data) <= remaining:
                retained[filename] = data
                remaining -= len(data)
            else:
                model.update(
                    status="incomplete",
                    detail="Proof source evidence exceeded the run output budget",
                )
                del model["artifacts"]["proof-source"]
                model.setdefault("artifact_omissions", []).append(filename)
    remaining = min(remaining, 1024 * 1024)
    for model in checked:
        for name, filename in list(model.get("artifacts", {}).items()):
            if name == "proof-source":
                continue
            data = artifacts[filename]
            if len(data) <= remaining:
                retained[filename] = data
                remaining -= len(data)
            else:
                del model["artifacts"][name]
                model.setdefault("artifact_omissions", []).append(filename)
    return models, retained


def verify(
    repo: Path,
    base: str,
    rev: str,
    runner: str,
    proof_campaign: list[str] | None,
    *,
    state_root: Path,
    docker_context: str | None,
    read_approval: Callable[[Path, Project], dict],
    scope: Callable[[Project], dict],
    tool_info: Callable[[Project], Any],
    run_formal: Callable[[Project, Any], list[dict]],
    runner_info: Callable[[Project, str | None], dict],
    run_behavior: Callable[..., tuple[list[dict], dict[str, bytes]]],
) -> dict:
    """Save the verification record for one candidate and return it with its bundle path.

    The collaborators are parameters so the CLI's module-level names stay the patch seams.
    """
    project = load_project(repo, base, runner)
    state = evidence.state_directory(state_root, candidate_root=project.root)
    approved = read_approval(state, project)
    candidate = snapshot(project.root, rev)
    run_id = uuid4().hex
    tools = tool_info(project)
    models = run_formal(project, tools)
    cases = []
    qualifications = []
    artifacts = {}
    if proof_campaign:
        models, artifacts = _proof_recheck(
            state, project, candidate, approved, proof_campaign, models
        )
    runtime = {"runner": runner}
    context = docker_context
    if len(models) != len(project.contract.formal):
        raise ValueError("Formal runner returned incomplete check results")
    if evidence.status(models, incomplete_first=True) == "pass":
        runtime = runner_info(project, context)
        if artifacts:
            from .runner import MAX_RUN_OUTPUT

            behavior, behavior_artifacts = run_behavior(
                project,
                candidate,
                context,
                runtime,
                run_id,
                output_budget=MAX_RUN_OUTPUT - sum(map(len, artifacts.values())),
            )
        else:
            behavior, behavior_artifacts = run_behavior(
                project, candidate, context, runtime, run_id
            )
        artifacts.update(behavior_artifacts)
        if len(behavior) != len(project.contract.behavior):
            raise ValueError("Behavior runner returned incomplete check results")
        cases = [
            {"case_id": check["id"], "status": check["status"], "checks": [check]}
            for check in behavior
        ]
    if runner == "host":
        runtime.setdefault("path", host_path())
        runtime["mentat_python"] = sys.executable
        runtime["invocation_path"] = os.environ.get("PATH", os.defpath)
        runtime["invocation_directory"] = str(Path.cwd())
    if project.contract.source_mutations and cases:
        from . import runner as behavior_runner
        from .qualification import run_source_mutations

        qualifications, mutation_artifacts = run_source_mutations(
            project,
            candidate,
            context,
            runtime,
            run_id,
            behavior,
            output_budget=max(
                0, behavior_runner.MAX_RUN_OUTPUT - sum(map(len, artifacts.values()))
            ),
        )
        artifacts.update(mutation_artifacts)
    if load_project(repo, base, runner).fingerprint != project.fingerprint:
        raise ValueError("Contract or verifier changed during the run. Repeat after approval")
    if read_approval(state, project) != approved:
        raise ValueError("Approval changed during the run")
    record = {
        "schema_version": 2,
        "kind": "project-verification",
        "run_id": run_id,
        "recorded_at": evidence.now(),
        **scope(project),
        "status": evidence.status([*models, *cases], incomplete_first=True),
        "revision": candidate.revision,
        "snapshot_digest": candidate.digest,
        "approval": approved,
        "claims": [
            claim.model_dump(mode="json", exclude={"title"}, exclude_none=True)
            for claim in project.contract.claims
        ],
        "claim_review": review_context(project.contract),
        "comparison_inputs": spec_review.comparison_inputs(project),
        "model_checks": models,
        "behavior_checks": [
            {"id": check.id, "claim": check.claim} for check in project.contract.behavior
        ],
        "behavior_skipped": not bool(cases),
        "cases": cases,
        "runtime": runtime,
        "tools": tools,
        "reproduce": _reproduce(project, state, candidate.revision, runtime.get("context")),
    }
    if proof_campaign:
        record["proof_campaigns"] = proof_campaign
        record["reproduce"] += " " + shlex.join(
            [arg for campaign in proof_campaign for arg in ("--proof-campaign", campaign)]
        )
    if project.contract.source_mutations:
        from .qualification import approved_mutations

        checks = {check.id: check for check in project.contract.behavior}
        record["source_mutation_checks"] = [
            {
                "id": mutation["id"],
                "claim": mutation["claim"],
                "check": mutation["check"],
                "expected_case": mutation["expected_case"],
                "required_cases": checks[mutation["check"]].case_report.required_cases,  # pyright: ignore[reportOptionalMemberAccess]  # approved_mutations rejects checks without case_report
                "violation_exit_codes": checks[mutation["check"]].violation_exit_codes,
            }
            for mutation in approved_mutations(project.contract, project.files)
        ]
        record["source_qualifications"] = qualifications
        record["source_qualification_status"] = evidence.qualification_status(qualifications)
        record["source_mutation_manifest_sha256"] = evidence.digest(
            project.files[project.contract.source_mutations]
        )
    if runner == "host":
        args = shlex.split(record["reproduce"])[1:]
        launcher = [sys.executable, "-P", "-c", "from mentat.cli import app; app()"]
        record["host_reproduce"] = (
            f"cd {shlex.quote(runtime['invocation_directory'])} && "
            f"PATH={shlex.quote(runtime['invocation_path'])} {shlex.join([*launcher, *args])}"
        )
    bundle = evidence.save_run(state, record, artifacts)
    return {**record, "bundle": str(bundle)}
