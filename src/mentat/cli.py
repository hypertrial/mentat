"""Verify a Git commit against a separately approved project contract."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import typer

from . import evidence
from .claims import checked_claims, write_claims
from .claims_notion import notion_claims
from .command import emit, finish, operator_confirm
from .project import Project, load_project, snapshot
from .project_formal import run_formal, tool_info
from .runner import run_behavior, runner_info
from .showcases.polaris.cli import app as polaris_app

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
spec_app = typer.Typer(no_args_is_help=True)
claims_app = typer.Typer(no_args_is_help=True)
evidence_app = typer.Typer(no_args_is_help=True)
showcase_app = typer.Typer(no_args_is_help=True)
app.add_typer(spec_app, name="spec", help="Review and approve a project contract.")
app.add_typer(
    claims_app, name="claims", help="Render and publish review copies of contract claims."
)
app.add_typer(evidence_app, name="evidence", help="Inspect and export run evidence.")
app.add_typer(showcase_app, name="showcase", help="Try Mentat on a real project.")
showcase_app.add_typer(polaris_app, name="polaris", help="Replay a Polaris configuration bug.")

Repo = Annotated[Path, typer.Option(exists=True, file_okay=False, help="Local Git repository.")]
Base = Annotated[str, typer.Option(help="Full commit ID containing the approved contract.")]
Runner = Annotated[
    Literal["docker", "host"], typer.Option(help="Execution mode to approve or use.")
]
JsonOutput = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]
_USER_ERRORS = (OSError, ValueError, subprocess.SubprocessError)


@app.callback()
def configure(
    ctx: typer.Context,
    state: Annotated[Path | None, typer.Option("--state", envvar="MENTAT_STATE")] = None,
    docker_context: Annotated[
        str | None, typer.Option("--docker-context", envvar="MENTAT_DOCKER_CONTEXT")
    ] = None,
) -> None:
    ctx.obj = {
        "state": state
        or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "mentat",
        "docker": docker_context,
    }


def _scope(project: Project) -> dict:
    return {
        "repo": str(project.root),
        "base": project.base,
        "runner": project.runner,
        "contract_digest": project.fingerprint,
    }


def _state(ctx: typer.Context, project: Project) -> Path:
    state = ctx.obj["state"].expanduser().resolve()
    if state.is_relative_to(project.root) or project.root.is_relative_to(state):
        raise ValueError("Mentat state must be outside the candidate repository")
    return evidence.state_directory(state)


def _approval_path(state: Path, project: Project) -> Path:
    return state / "approvals" / f"{project.fingerprint}.json"


def _approval(state: Path, project: Project) -> dict:
    try:
        record = evidence.read_json(_approval_path(state, project))
    except FileNotFoundError as exc:
        raise ValueError(
            "Approve this repository, base commit, and runner with mentat spec approve"
        ) from exc
    if (
        not isinstance(record, dict)
        or any(record.get(key) != value for key, value in _scope(project).items())
        or not isinstance(record.get("approved_at"), str)
    ):
        raise ValueError("Approval record is invalid. Check and approve the current contract")
    return record


def _error(exc: Exception, output_json: bool) -> None:
    finish({"schema_version": 2, "status": "incomplete", "detail": str(exc)}, output_json)


def _summary(project: Project) -> dict:
    return {
        "schema_version": 2,
        "status": "pass",
        **_scope(project),
        "lean_runs_on_host": any(check.engine == "lean" for check in project.contract.formal),
        "contract": project.contract.model_dump(mode="json", exclude_none=True),
        "approved_files": {name: evidence.digest(data) for name, data in project.files.items()},
    }


@claims_app.command("render")
def render_claims(repo: Repo, output_json: JsonOutput = False) -> None:
    """Regenerate working-tree .mentat/claims.md from the contract."""
    try:
        write_claims(repo)
    except _USER_ERRORS as exc:
        _error(exc, output_json)
        return
    finish({"status": "pass", "detail": ".mentat/claims.md is up to date"}, output_json)


def _review_claims(
    repo: Path, base: str | None, notion: str | None, output_json: bool, *, publish: bool
) -> None:
    try:
        if notion is not None and base is None:
            raise ValueError("Notion Claims Spec operations require --base with a full commit ID")
        content, local_matches = checked_claims(repo, base)
        matches = local_matches
        detail = ".mentat/claims.md is up to date"
        if not local_matches:
            detail = "missing or stale .mentat/claims.md; run mentat claims render"
        elif notion is not None and base is not None:
            matches = notion_claims(notion, content, base, publish=publish)
            detail = (
                "Notion Claims Spec is in sync"
                if matches
                else "Notion Claims Spec is missing or stale"
            )
    except _USER_ERRORS as exc:
        _error(exc, output_json)
        return
    finish({"status": "pass" if matches else "violation", "detail": detail}, output_json)


@claims_app.command("check")
def check_claims(
    repo: Repo,
    base: Annotated[str | None, typer.Option(help="Full commit ID to check.")] = None,
    notion: Annotated[str | None, typer.Option(help="Notion page URL or ID.")] = None,
    output_json: JsonOutput = False,
) -> None:
    """Check the local Claims Spec and optionally its Notion copy."""
    _review_claims(repo, base, notion, output_json, publish=False)


@claims_app.command("publish")
def publish_claims(
    repo: Repo,
    base: Base,
    notion: Annotated[str, typer.Option(help="Notion page URL or ID.")],
    output_json: JsonOutput = False,
) -> None:
    """Publish a committed Claims Spec to the selected Notion page."""
    _review_claims(repo, base, notion, output_json, publish=True)


@app.command()
def doctor(
    ctx: typer.Context,
    repo: Repo,
    base: Base,
    runner: Runner = "docker",
    output_json: JsonOutput = False,
) -> None:
    """Check the selected runtime and model tools."""
    try:
        project = load_project(repo, base, runner)
        runtime = runner_info(project, ctx.obj["docker"])
        tools = tool_info(project)
        result = {
            "schema_version": 2,
            "status": "pass",
            **_scope(project),
            "runtime": runtime,
            "tools": tools,
        }
        if not output_json:
            typer.echo(evidence.json_bytes({"runtime": runtime, "tools": tools}).decode().rstrip())
        finish(result, output_json)
    except _USER_ERRORS as exc:
        _error(exc, output_json)


@spec_app.command("check")
def check_spec(
    repo: Repo, base: Base, runner: Runner = "docker", output_json: JsonOutput = False
) -> None:
    """Show the committed contract and the digest to approve."""
    try:
        result = _summary(load_project(repo, base, runner))
        if not output_json:
            typer.echo(evidence.json_bytes(result).decode().rstrip())
        finish(result, output_json)
    except _USER_ERRORS as exc:
        _error(exc, output_json)


@spec_app.command("approve")
def approve_spec(
    ctx: typer.Context, digest: str, repo: Repo, base: Base, runner: Runner = "docker"
) -> None:
    """Approve the exact contract reviewed in the terminal."""
    try:
        project = load_project(repo, base, runner)
        if digest != project.fingerprint:
            raise ValueError("Approval must name the digest displayed by spec check")
        state = _state(ctx, project)
        summary = _summary(project)
        typer.echo(evidence.json_bytes(summary).decode().rstrip())
        decision = f"Approve contract {digest} for {runner} candidate execution"
        if summary["lean_runs_on_host"]:
            decision += " and allow the reviewed Lean source to execute on this host"
        operator_confirm(f"{decision}?")
        if load_project(repo, base, runner).fingerprint != digest:
            raise ValueError("Contract changed during approval")
        record = {**_scope(project), "approved_at": evidence.now()}
        evidence.write_record(_approval_path(state, project), evidence.json_bytes(record))
        finish({"status": "pass", "contract_digest": digest}, False)
    except _USER_ERRORS as exc:
        _error(exc, False)


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


@app.command()
def verify(
    ctx: typer.Context,
    repo: Repo,
    base: Base,
    rev: Annotated[str, typer.Option(help="Full candidate commit ID.")],
    runner: Runner = "docker",
    output_json: JsonOutput = False,
) -> None:
    """Run the approved formal and behavior checks for a commit."""
    try:
        project = load_project(repo, base, runner)
        state = _state(ctx, project)
        approved = _approval(state, project)
        candidate = snapshot(project.root, rev)
        run_id = uuid4().hex
        tools = tool_info(project)
        models = run_formal(project, tools)
        cases = []
        artifacts = {}
        runtime = {"runner": runner}
        context = ctx.obj["docker"]
        if len(models) != len(project.contract.formal):
            raise ValueError("Formal runner returned incomplete check results")
        if evidence.status(models, incomplete_first=True) == "pass":
            runtime = runner_info(project, context)
            behavior, artifacts = run_behavior(project, candidate, context, runtime, run_id)
            if len(behavior) != len(project.contract.behavior):
                raise ValueError("Behavior runner returned incomplete check results")
            cases = [
                {"case_id": check["id"], "status": check["status"], "checks": [check]}
                for check in behavior
            ]
        if load_project(repo, base, runner).fingerprint != project.fingerprint:
            raise ValueError("Contract or verifier changed during the run. Repeat after approval")
        if _approval(state, project) != approved:
            raise ValueError("Approval changed during the run")
        record = {
            "schema_version": 2,
            "kind": "project-verification",
            "run_id": run_id,
            "recorded_at": evidence.now(),
            **_scope(project),
            "status": evidence.status([*models, *cases], incomplete_first=True),
            "revision": candidate.revision,
            "snapshot_digest": candidate.digest,
            "approval": approved,
            "claims": [
                claim.model_dump(mode="json", exclude={"title"})
                for claim in project.contract.claims
            ],
            "model_checks": models,
            "behavior_skipped": not bool(cases),
            "cases": cases,
            "runtime": runtime,
            "tools": tools,
            "reproduce": _reproduce(project, state, candidate.revision, runtime.get("context")),
        }
        bundle = evidence.save_run(state, record, artifacts)
        finish({**record, "bundle": str(bundle)}, output_json)
    except _USER_ERRORS as exc:
        _error(exc, output_json)


@evidence_app.command("export")
def export_evidence(
    ctx: typer.Context,
    run_id: str,
    output: Annotated[Path | None, typer.Option("--out")] = None,
    output_json: JsonOutput = False,
) -> None:
    """Check a run's local integrity and optionally copy its bundle."""
    try:
        state = evidence.state_directory(ctx.obj["state"])
        bundle, record = evidence.read_run(state, run_id)
        if output is not None:
            bundle = Path(shutil.copytree(bundle, output))
        emit(
            {
                "status": "pass",
                "run_status": record["status"],
                "run_id": run_id,
                "bundle": str(bundle.resolve()),
            },
            output_json,
        )
    except (*_USER_ERRORS, KeyError) as exc:
        _error(exc, output_json)


@app.command()
def accept(
    ctx: typer.Context, run_id: str, repo: Repo, base: Base, runner: Runner = "docker"
) -> None:
    """Accept a complete passing run under the current approval."""
    try:
        project = load_project(repo, base, runner)
        state = _state(ctx, project)
        approved = _approval(state, project)
        _, record = evidence.read_run(state, run_id)
        if (
            record.get("kind") != "project-verification"
            or record["status"] != "pass"
            or any(record.get(key) != value for key, value in _scope(project).items())
            or record.get("approval") != approved
        ):
            raise ValueError(
                "Only a complete passing run under the current approval can be accepted"
            )
        receipt = (state / "receipts" / run_id).read_text()
        operator_confirm(f"Accept candidate {record['revision']} from run {run_id}?")
        if load_project(repo, base, runner).fingerprint != project.fingerprint:
            raise ValueError("Contract changed during acceptance")
        evidence.read_run(state, run_id)
        if (state / "receipts" / run_id).read_text() != receipt or _approval(
            state, project
        ) != approved:
            raise ValueError("Evidence or approval changed during acceptance")
        evidence.write_record(
            state / "acceptances" / f"{run_id}.json",
            evidence.json_bytes(
                {
                    "run_id": run_id,
                    "manifest_digest": receipt,
                    "revision": record["revision"],
                    "contract_digest": project.fingerprint,
                    "accepted_at": evidence.now(),
                }
            ),
        )
        finish({"status": "pass", "run_id": run_id}, False)
    except (*_USER_ERRORS, KeyError) as exc:
        _error(exc, False)
