"""Verify a Git commit against a separately approved project contract."""

import os
import shlex
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import typer

from . import evidence, spec_review
from .claims import checked_claims, provenance_lines, write_claims
from .claims_notion import notion_claims
from .command import emit, finish, operator_confirm, visible_text
from .evidence_report import review_context
from .project import Project, load_project, snapshot
from .project_formal import run_formal, tool_info
from .runner import host_diagnostics, host_path, run_behavior, runner_info
from .showcases.polaris.cli import app as polaris_app

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
spec_app = typer.Typer(no_args_is_help=True)
claims_app = typer.Typer(no_args_is_help=True)
evidence_app = typer.Typer(no_args_is_help=True)
proof_app = typer.Typer(no_args_is_help=True)
showcase_app = typer.Typer(no_args_is_help=True)
app.add_typer(spec_app, name="spec", help="Review and approve a project contract.")
app.add_typer(
    claims_app, name="claims", help="Render and publish review copies of contract claims."
)
app.add_typer(evidence_app, name="evidence", help="Inspect and export run evidence.")
app.add_typer(proof_app, name="proof", help="Prepare and check approved Lean proof tasks.")
app.add_typer(showcase_app, name="showcase", help="Try Mentat on a real project.")
showcase_app.add_typer(polaris_app, name="polaris", help="Replay a Polaris configuration bug.")

Repo = Annotated[Path, typer.Option(exists=True, file_okay=False, help="Local Git repository.")]
Base = Annotated[str, typer.Option(help="Full commit ID containing the approved contract.")]
Against = Annotated[str | None, typer.Option(help="Full commit ID to compare with the base.")]
Runner = Annotated[
    Literal["docker", "host"], typer.Option(help="Execution mode to approve or use.")
]
JsonOutput = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]
_USER_ERRORS = (OSError, ValueError, subprocess.SubprocessError)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mentat {version('hypertrial-mentat')}")
        raise typer.Exit()


@app.callback()
def configure(
    ctx: typer.Context,
    version_option: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
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
    return evidence.state_directory(ctx.obj["state"], candidate_root=project.root)


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


def _summary(project: Project, against: str | None = None) -> dict:
    result = {
        "schema_version": 2,
        "status": "pass",
        **_scope(project),
        **spec_review.summary(project),
    }
    if against is not None:
        previous = load_project(project.root, against, project.runner)
        result["delta"] = spec_review.compare(project, result, previous)
    return result


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
        problems = host_diagnostics(runtime) if runner == "host" else []
        try:
            tools = tool_info(project)
        except _USER_ERRORS as exc:
            tools = {}
            problems.append(str(exc))
        result = {
            "schema_version": 2,
            "status": "incomplete" if problems else "pass",
            **_scope(project),
            "runtime": runtime,
            "tools": tools,
        }
        if problems:
            result["diagnostics"] = problems
            result["detail"] = "\n".join(problems)
        if not output_json:
            typer.echo(evidence.json_bytes({"runtime": runtime, "tools": tools}).decode().rstrip())
        finish(result, output_json)
    except _USER_ERRORS as exc:
        _error(exc, output_json)


@spec_app.command("check")
def check_spec(
    repo: Repo,
    base: Base,
    runner: Runner = "docker",
    output_json: JsonOutput = False,
    against: Against = None,
) -> None:
    """Show the committed contract and the digest to approve."""
    try:
        result = _summary(load_project(repo, base, runner), against)
        if not output_json:
            spec_review.show(result)
        finish(result, output_json)
    except _USER_ERRORS as exc:
        _error(exc, output_json)


@spec_app.command("import-openspec")
def import_openspec(
    repo: Repo,
    base: Annotated[str, typer.Option(help="Full commit ID containing OpenSpec capability specs.")],
    output_json: JsonOutput = False,
) -> None:
    """Draft claims from committed OpenSpec specs for operator review."""
    from .openspec import import_spec

    try:
        result = import_spec(repo, base)
    except _USER_ERRORS as exc:
        _error(exc, output_json)
        return
    if not output_json:
        typer.echo(f"OpenSpec draft from {result['revision']}")
        for entry in result["claims"]:
            claim = entry["claim"]
            typer.echo(f"\n{claim['id']} · {visible_text(claim['title'])} · needs a behavior check")
            typer.echo(visible_text(claim["statement"], multiline=True))
            for line in provenance_lines(claim["provenance"]):
                typer.echo(visible_text(line))
        for item in result["diagnostics"]:
            typer.echo(visible_text(f"{item['path']}:{item['line']} · {item['detail']}"))
    finish(result, output_json)


@spec_app.command("approve")
def approve_spec(
    ctx: typer.Context,
    digest: str,
    repo: Repo,
    base: Base,
    runner: Runner = "docker",
    against: Against = None,
    trusted_chat_approval: bool = typer.Option(
        False, "--trusted-chat-approval", help="Trust an unverified agent relay; bypass the terminal prompt."
    ),
) -> None:
    """Approve the exact contract reviewed in the terminal."""
    try:
        project = load_project(repo, base, runner)
        if digest != project.fingerprint:
            raise ValueError("Approval must name the digest displayed by spec check")
        state = _state(ctx, project)
        summary = _summary(project, against)
        spec_review.show(summary)
        decision = f"Approve contract {digest} for {runner} candidate execution"
        if summary["lean_runs_on_host"]:
            decision += " and allow the reviewed Lean source to execute on this host"
        if not trusted_chat_approval:
            operator_confirm(f"{decision}?")
        if load_project(repo, base, runner).fingerprint != digest:
            raise ValueError("Contract changed during approval")
        record = {**_scope(project), "approved_at": evidence.now()}
        if trusted_chat_approval:
            record["decision_source"] = "unverified_agent_relay"
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


@proof_app.command("prepare")
def prepare_proof(
    ctx: typer.Context,
    repo: Repo,
    base: Base,
    rev: Annotated[str, typer.Option(help="Full candidate commit ID.")],
    output: Annotated[Path, typer.Option("--out", help="New editable task directory.")],
    runner: Runner = "docker",
    target: Annotated[
        list[str] | None,
        typer.Option(help="Selected formal check ID; repeat for multiple targets."),
    ] = None,
    output_json: JsonOutput = False,
) -> None:
    """Freeze approved targets and create separate editable source tasks."""
    from . import proof

    try:
        project = load_project(repo, base, runner)
        state = _state(ctx, project)
        result = proof.prepare(
            state,
            project,
            snapshot(project.root, rev),
            _approval(state, project),
            output,
            targets=target,
        )
        finish(result, output_json)
    except _USER_ERRORS as exc:
        _error(exc, output_json)


@proof_app.command("check")
def check_proof(
    ctx: typer.Context,
    campaign_id: str,
    submission: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    output_json: JsonOutput = False,
) -> None:
    """Snapshot source submissions and record an independently checked attempt."""
    from . import proof

    try:
        state = evidence.state_directory(ctx.obj["state"], create=False)
        finish(proof.check(state, campaign_id, submission), output_json)
    except _USER_ERRORS as exc:
        _error(exc, output_json)


@app.command()
def verify(
    ctx: typer.Context,
    repo: Repo,
    base: Base,
    rev: Annotated[str, typer.Option(help="Full candidate commit ID.")],
    runner: Runner = "docker",
    proof_campaign: Annotated[
        list[str] | None,
        typer.Option(help="Recheck a matching proof campaign; repeat for disjoint targets."),
    ] = None,
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
        qualifications = []
        artifacts = {}
        if proof_campaign:
            from . import proof

            checked, artifacts = proof.recheck(state, project, candidate, approved, proof_campaign)
            by_id = {item["id"]: item for item in checked}
            if len(by_id) != len(checked) or set(by_id) - {
                check.id for check in project.contract.formal if check.engine == "lean-comparator"
            }:
                raise ValueError("Proof recheck returned unexpected formal obligations")
            models = [by_id.get(item["id"], item) for item in models]
            from .runner import _MAX_RUN_OUTPUT

            # Source evidence is required; proof diagnostics are optional.
            retained = {}
            remaining = _MAX_RUN_OUTPUT
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
            artifacts = retained
        runtime = {"runner": runner}
        context = ctx.obj["docker"]
        if len(models) != len(project.contract.formal):
            raise ValueError("Formal runner returned incomplete check results")
        if evidence.status(models, incomplete_first=True) == "pass":
            runtime = runner_info(project, context)
            if artifacts:
                from .runner import _MAX_RUN_OUTPUT

                behavior, behavior_artifacts = run_behavior(
                    project,
                    candidate,
                    context,
                    runtime,
                    run_id,
                    output_budget=_MAX_RUN_OUTPUT - sum(map(len, artifacts.values())),
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
                    0, behavior_runner._MAX_RUN_OUTPUT - sum(map(len, artifacts.values()))
                ),
            )
            artifacts.update(mutation_artifacts)
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
            "status": evidence.status([*models, *cases, *qualifications], incomplete_first=True),
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
                    "required_cases": checks[mutation["check"]].case_report.required_cases,
                    "violation_exit_codes": checks[mutation["check"]].violation_exit_codes,
                }
                for mutation in approved_mutations(project.contract, project.files)
            ]
            record["source_qualifications"] = qualifications
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


@evidence_app.command("render")
def render_evidence(
    ctx: typer.Context,
    run_id: str,
    output: Annotated[Path, typer.Option("--out", help="New HTML report path.")],
) -> None:
    """Render an intact saved project run as an offline HTML report."""
    from .evidence_html import render_run

    try:
        render_run(evidence.state_directory(ctx.obj["state"], create=False), run_id, output)
        typer.echo(f"Report written to {output}")
    except _USER_ERRORS as exc:
        _error(exc, False)


@evidence_app.command("compare")
def compare_evidence(
    ctx: typer.Context,
    before_run: str,
    after_run: str,
    output_json: JsonOutput = False,
    output: Annotated[
        Path | None, typer.Option("--out", help="New offline HTML comparison path.")
    ] = None,
    across_contracts: Annotated[
        bool, typer.Option(help="Review saved runs across approved contract revisions.")
    ] = False,
) -> None:
    """Compare intact verification runs by claim."""

    try:
        state = evidence.state_directory(ctx.obj["state"], create=False)
        result = evidence.compare_runs(
            state, before_run, after_run, across_contracts=across_contracts
        )
        if output is not None:
            from .evidence_html import render_comparison

            render_comparison(
                state, before_run, after_run, output, across_contracts=across_contracts
            )
            result = {
                **result,
                "comparison_status": result["status"],
                "status": "pass",
                "report": str(output),
            }
    except _USER_ERRORS as exc:
        _error(exc, output_json)
        return
    except (KeyError, TypeError):
        _error(ValueError("Invalid saved verification record"), output_json)
        return
    if not output_json:
        for side in ("before", "after"):
            run = result[side]
            typer.echo(f"{side.title()}  {run['revision']}  {run['status']}  (run {run['run_id']})")
            if across_contracts:
                typer.echo(f"  Base {run['base']}  Approval {run['contract_digest']}")
            typer.echo(f"  Report  {run['report']}")
            if run["behavior_skipped"]:
                typer.echo("  Behavior skipped because formal checks did not pass.")
        if result["environment_changed"]:
            typer.echo("Recorded tools or runner metadata differ between the runs.")
        if across_contracts:
            typer.echo("Contract history review")
            for key, value in result["input_changes"].items():
                typer.echo(f"  {key.replace('_', ' ')}  {value}")
            for claim in result["claims"]:
                typer.echo(f"Claim {claim['id']}  Definition {claim['definition']}")
                for side in ("before", "after"):
                    item = claim[side]
                    if item is None:
                        typer.echo(f"  {side}  absent")
                        continue
                    typer.echo(f"  {side}  {visible_text(item['statement'])}")
                    for check in item["checks"]:
                        typer.echo(f"    {check['kind']} {check['id']}  {check['status']}")
            finish(result, False)
            return
        for claim in result["claims"]:
            typer.echo(f"Claim {claim['id']}  {visible_text(claim['statement'])}")
            for check in claim["checks"]:
                typer.echo(
                    f"  {check['kind']} {check['id']}  "
                    f"{check['before']['status']} -> {check['after']['status']}"
                )
                for side in ("before", "after"):
                    bundle = Path(result[side]["bundle"])
                    for group in ("artifacts", "logs"):
                        for name, filename in check[side].get(group, {}).items():
                            typer.echo(f"    {side} {name}  {bundle / filename}")
    finish(result, output_json)


@evidence_app.command("pr-summary")
def summarize_pr(
    ctx: typer.Context,
    run_id: str,
    head: Annotated[str, typer.Option(help="Exact PR head commit, matching the saved run.")],
    report_url: Annotated[str, typer.Option(help="Operator-authorized HTTPS report URL.")],
    ci_status: Annotated[
        Literal["pass", "fail", "incomplete", "unavailable"],
        typer.Option(help="Exact-head lightweight CI result supplied by the host."),
    ] = "unavailable",
) -> None:
    """Print a Markdown PR summary; preserve existing artifact access controls."""
    from .evidence_html import _accepted, _validate
    from .evidence_report import pr_summary

    try:
        state = evidence.state_directory(ctx.obj["state"], create=False)
        _, record, digest = evidence.read_run_snapshot(state, run_id)
        _validate(record)
        typer.echo(
            pr_summary(record, head, report_url, _accepted(state, record, digest), ci_status)
        )
    except _USER_ERRORS as exc:
        _error(exc, False)


@app.command()
def accept(
    ctx: typer.Context, run_id: str, repo: Repo, base: Base, runner: Runner = "docker",
    trusted_chat_approval: bool = typer.Option(
        False, "--trusted-chat-approval", help="Trust an unverified agent relay; bypass the terminal prompt."
    ),
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
        if not trusted_chat_approval:
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
                    **({"decision_source": "unverified_agent_relay"} if trusted_chat_approval else {}),
                }
            ),
        )
        finish({"status": "pass", "run_id": run_id}, False)
    except (*_USER_ERRORS, KeyError) as exc:
        _error(exc, False)
