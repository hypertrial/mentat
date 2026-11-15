"""Local specification and verification commands."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic
from uuid import uuid4

import typer

from . import docker_runtime, evidence
from .evaluation import FIXED
from .evaluation import evaluate as evaluate_candidates
from .regression import render_regression
from .replay import run_case, snapshot
from .spec import PACK, load_cases, load_spec

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
spec_app = typer.Typer(no_args_is_help=True)
evidence_app = typer.Typer(no_args_is_help=True)
app.add_typer(spec_app, name="spec")
app.add_typer(evidence_app, name="evidence")


@app.callback()
def configure(
    ctx: typer.Context,
    spec: Path = typer.Option(PACK / "contract.json", "--spec", envvar="MENTAT_SPEC"),
    state: Path | None = typer.Option(None, "--state", envvar="MENTAT_STATE"),
    docker_context: str = typer.Option(
        "colima-mentat", "--docker-context", envvar="MENTAT_DOCKER_CONTEXT"
    ),
) -> None:
    ctx.obj = {
        "spec": spec.expanduser().resolve(),
        "state": state
        or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "mentat",
        "docker": docker_context,
    }


def emit(result: dict, output_json: bool) -> None:
    if output_json:
        typer.echo(evidence.json_bytes({"schema_version": 1, **result}).decode().rstrip())
        return
    title = result.get("status", "pass" if result.get("ok") else "incomplete")
    typer.echo(f"Mentat {title}")
    for key in ("detail", "context", "contract_digest", "run_id", "bundle", "next_action"):
        if result.get(key):
            typer.echo(f"{key.replace('_', ' ')}  {result[key]}")
    for check in result.get("model_checks", []):
        typer.echo(f"{check['kind']}  {check['status']}  {check['detail']}")
        if check.get("witness"):
            typer.echo(json.dumps(check["witness"], ensure_ascii=True, sort_keys=True))
    for case in result.get("cases", []):
        typer.echo(f"{case['case_id']}  {case['status']}")
        for check in case.get("checks", []):
            if check.get("status") != "pass":
                typer.echo(f"  {check.get('detail', check.get('id', 'check'))}")
    for method, counts in result.get("counts", {}).items():
        typer.echo(f"{method}  {json.dumps(counts, sort_keys=True)}")


def finish(result: dict, output_json: bool) -> None:
    emit(result, output_json)
    code = {"pass": 0, "violation": 1, "incomplete": 2}[result["status"]]
    if code:
        raise typer.Exit(code)


def error(exc: Exception, output_json: bool) -> None:
    finish({"status": "incomplete", "detail": str(exc)}, output_json)


def contract_inputs(ctx: typer.Context):
    spec = load_spec(ctx.obj["spec"])
    return spec, evidence.pack_digest(spec)


def operator_confirm(message: str) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Operator approval or acceptance requires an interactive terminal")
    if not typer.confirm(message, default=False):
        raise ValueError("The operator declined")


@app.command()
def doctor(
    ctx: typer.Context,
    prepare: bool = typer.Option(False, help="Pull pinned images and build the trusted runner."),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    docker = (docker_runtime.prepare if prepare else docker_runtime.doctor)(ctx.obj["docker"])
    lean = shutil.which("lean")
    lake = shutil.which("lake")
    try:
        spec, fingerprint = contract_inputs(ctx)
        models = evidence.model_checks(
            evidence.state_directory(ctx.obj["state"]), spec, fingerprint, fresh=True
        )
    except (OSError, ValueError, KeyError) as exc:
        error(exc, output_json)
        return
    ok = bool(docker["ok"] and lean and lake and evidence.status(models) == "pass")
    finish(
        {
            **docker,
            "status": "pass" if ok else "incomplete",
            "lean": lean,
            "lake": lake,
            "model_checks": models,
            "next_action": None
            if ok
            else "Check the selected Colima context and pinned Lean toolchain. Run doctor --prepare to provision images.",
        },
        output_json,
    )


@spec_app.command("check")
def check_spec(ctx: typer.Context, output_json: bool = typer.Option(False, "--json")) -> None:
    try:
        spec, fingerprint = contract_inputs(ctx)
        load_cases(PACK / "public.json", spec)
        state = evidence.state_directory(ctx.obj["state"])
        checks = evidence.model_checks(state, spec, fingerprint, fresh=True)
        finish(
            {
                "status": evidence.status(checks),
                "contract_digest": fingerprint,
                "contract": spec.as_dict(),
                "model_checks": checks,
            },
            output_json,
        )
    except (OSError, ValueError, KeyError) as exc:
        error(exc, output_json)


@spec_app.command("approve")
def approve_spec(ctx: typer.Context, digest: str) -> None:
    try:
        spec, fingerprint = contract_inputs(ctx)
        if digest != fingerprint:
            raise ValueError("Approval must name the digest displayed by spec check")
        load_cases(PACK / "public.json", spec)
        state = evidence.state_directory(ctx.obj["state"])
        checks = evidence.model_checks(state, spec, fingerprint, fresh=True)
        if evidence.status(checks) != "pass":
            raise ValueError("Required formal checks must pass before approval")
        typer.echo(evidence.json_bytes(spec.as_dict()).decode().rstrip())
        operator_confirm(f"Approve contract {fingerprint} for verification?")
        if evidence.pack_digest(load_spec(ctx.obj["spec"])) != fingerprint:
            raise ValueError("Contract changed during approval")
        evidence.write_record(
            state / "approval.json",
            evidence.json_bytes({"contract_digest": fingerprint, "approved_at": evidence.now()}),
        )
        emit({"status": "pass", "contract_digest": fingerprint}, False)
    except (OSError, ValueError, KeyError) as exc:
        error(exc, False)


def candidate_repo(path: Path, state: Path) -> Path:
    git_root = subprocess.run(
        ["git", "-C", str(path.resolve()), "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=True,
        timeout=10,
    )
    repo = Path(os.fsdecode(git_root.stdout.rstrip(b"\n"))).resolve()
    if (
        state.is_relative_to(repo)
        or repo.is_relative_to(state)
        or PACK.resolve().is_relative_to(repo)
    ):
        raise ValueError(
            "Mentat state and trusted verifier must be separate from the candidate repository"
        )
    return repo


@app.command()
def verify(
    ctx: typer.Context,
    repo: Path = typer.Option(..., exists=True, file_okay=False),
    rev: str = typer.Option(...),
    fresh: bool = typer.Option(False, help="Bypass the model result cache."),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    try:
        state = evidence.state_directory(ctx.obj["state"])
        spec, fingerprint = contract_inputs(ctx)
        approved = evidence.approval(state, fingerprint)
        repo = candidate_repo(repo, state)
        if ctx.obj["spec"].is_relative_to(repo):
            raise ValueError("The specification must be outside the candidate repository")
        candidate = snapshot(repo, rev)
        fixtures = load_cases(PACK / "public.json", spec)
        models = evidence.model_checks(state, spec, fingerprint, fresh=fresh)
        run_id = uuid4().hex
        cases = []
        regressions = []
        artifacts = {}
        if evidence.status(models) == "pass":
            for fixture in fixtures:
                result = run_case(
                    candidate,
                    ctx.obj["docker"],
                    fixture,
                    spec.selected_properties,
                    f"{run_id}-{len(cases)}",
                )
                cases.append(result)
                if result["status"] == "violation" and any(
                    check.get("status") == "violation" and check.get("claim") in spec.claims
                    for check in result["checks"]
                ):
                    source = render_regression(fixture, spec.selected_properties)
                    regression = docker_runtime.run_pytest(
                        candidate, ctx.obj["docker"], source, f"{run_id}-pytest-{len(cases)}"
                    )
                    regressions.append({**regression, "case_id": fixture.id})
                    if regression["status"] == "violation":
                        filename = (
                            "test_mentat_regression.py"
                            if len(fixtures) == 1
                            else f"test_mentat_{len(cases)}.py"
                        )
                        artifacts[filename] = source.replace(
                            "tests/test_mentat_regression.py", f"tests/{filename}", 1
                        ).encode()
        if not cases:
            cases = [
                {
                    "case_id": case.id,
                    "status": "incomplete",
                    "checks": [
                        {
                            "status": "incomplete",
                            "detail": "Live replay requires all formal checks to pass",
                        }
                    ],
                }
                for case in fixtures
            ]
        if evidence.pack_digest(load_spec(ctx.obj["spec"])) != fingerprint:
            raise ValueError("Contract or verifier changed during the run. Repeat after approval")
        record = {
            "schema_version": 1,
            "kind": "verification",
            "run_id": run_id,
            "recorded_at": evidence.now(),
            "status": evidence.status([*models, *cases]),
            "revision": candidate.revision,
            "snapshot_digest": candidate.digest,
            "contract_digest": fingerprint,
            "approval": approved,
            "fixture_digest": evidence.digest((PACK / "public.json").read_bytes()),
            "model_checks": models,
            "cases": cases,
            "regressions": regressions,
            "reproduce": shlex.join(
                [
                    "mentat",
                    "--spec",
                    str(ctx.obj["spec"]),
                    "--state",
                    str(state),
                    "--docker-context",
                    ctx.obj["docker"],
                    "verify",
                    "--repo",
                    str(repo),
                    "--rev",
                    candidate.revision,
                    "--fresh",
                ]
            ),
        }
        bundle = evidence.save_run(state, record, artifacts)
        finish({**record, "bundle": str(bundle)}, output_json)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        error(exc, output_json)


@app.command()
def evaluate(
    ctx: typer.Context,
    repo: Path = typer.Option(..., exists=True, file_okay=False),
    held_out: Path = typer.Option(..., exists=True, dir_okay=False),
    sha256: str = typer.Option(..., help="SHA-256 recorded before repair."),
    rev: str = typer.Option(FIXED, help="Committed repair to evaluate."),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    try:
        state = evidence.state_directory(ctx.obj["state"])
        spec, fingerprint = contract_inputs(ctx)
        approved = evidence.approval(state, fingerprint)
        repo = candidate_repo(repo, state)
        held_out = held_out.resolve()
        if held_out.is_relative_to(repo) or ctx.obj["spec"].is_relative_to(repo):
            raise ValueError(
                "The specification and held-out fixtures must be outside the candidate repository"
            )
        with held_out.open("rb") as stream:
            frozen = stream.read(1_000_001)
        if len(frozen) > 1_000_000 or evidence.digest(frozen) != sha256:
            raise ValueError(
                "Held-out fixture does not match its frozen digest or exceeds the size limit"
            )
        with tempfile.TemporaryDirectory(dir=state) as temp:
            fixture_path = Path(temp) / "fixtures.json"
            fixture_path.write_bytes(frozen)
            cases = load_cases(fixture_path, spec)
        if any(
            not any("." in part for ns in case.namespaces for part in ns.path) for case in cases
        ):
            raise ValueError("Each held-out fixture must exercise a literal-dot namespace")
        if any(not any(ns.properties for ns in case.namespaces) for case in cases):
            raise ValueError("Each held-out fixture must have a selected property to mutate")
        public = load_cases(PACK / "public.json", spec)[0]
        started = monotonic()
        models = evidence.model_checks(state, spec, fingerprint, fresh=True)
        formal_seconds = round(monotonic() - started, 3)
        if evidence.status(models) != "pass":
            raise ValueError("Required formal checks must pass before evaluation")
        run_id = uuid4().hex
        result = evaluate_candidates(repo, rev, ctx.obj["docker"], spec, cases, public, run_id)
        if evidence.pack_digest(load_spec(ctx.obj["spec"])) != fingerprint:
            raise ValueError("Contract or verifier changed during evaluation")
        record = {
            "schema_version": 2,
            "kind": "evaluation",
            "run_id": run_id,
            "recorded_at": evidence.now(),
            "revision": rev,
            "contract_digest": fingerprint,
            "approval": approved,
            "held_out_digest": sha256,
            "formal_seconds": formal_seconds,
            "model_checks": models,
            **result,
            "reproduce": shlex.join(
                [
                    "mentat",
                    "--spec",
                    str(ctx.obj["spec"]),
                    "--state",
                    str(state),
                    "--docker-context",
                    ctx.obj["docker"],
                    "evaluate",
                    "--repo",
                    str(repo),
                    "--held-out",
                    str(held_out),
                    "--sha256",
                    sha256,
                    "--rev",
                    rev,
                ]
            ),
        }
        bundle = evidence.save_run(state, record, {})
        finish({**record, "bundle": str(bundle)}, output_json)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        error(exc, output_json)


@evidence_app.command("export")
def export_evidence(
    ctx: typer.Context,
    run_id: str,
    output: Path | None = typer.Option(None, "--out"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
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
    except (OSError, ValueError, KeyError) as exc:
        error(exc, output_json)


@app.command()
def accept(ctx: typer.Context, run_id: str) -> None:
    try:
        state = evidence.state_directory(ctx.obj["state"])
        _, fingerprint = contract_inputs(ctx)
        evidence.approval(state, fingerprint)
        _, record = evidence.read_run(state, run_id)
        if (
            record.get("kind") != "verification"
            or record["status"] != "pass"
            or record["contract_digest"] != fingerprint
        ):
            raise ValueError(
                "Only a complete passing run under the current contract can be accepted"
            )
        receipt = (state / "receipts" / run_id).read_text()
        operator_confirm(f"Accept candidate {record['revision']} from run {run_id}?")
        if contract_inputs(ctx)[1] != fingerprint:
            raise ValueError("Contract changed during acceptance")
        evidence.read_run(state, run_id)
        if (state / "receipts" / run_id).read_text() != receipt:
            raise ValueError("Evidence changed during acceptance")
        evidence.approval(state, fingerprint)
        evidence.write_record(
            state / "acceptances" / f"{run_id}.json",
            evidence.json_bytes(
                {
                    "run_id": run_id,
                    "manifest_digest": receipt,
                    "revision": record["revision"],
                    "contract_digest": fingerprint,
                    "accepted_at": evidence.now(),
                }
            ),
        )
        emit({"status": "pass", "run_id": run_id}, False)
    except (OSError, ValueError, KeyError) as exc:
        error(exc, False)
