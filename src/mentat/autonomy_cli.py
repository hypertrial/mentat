"""Additive CLI for session-scoped autonomous work and direct source evidence."""

import json
import subprocess
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from .autonomy import Coordinator, initialize, state_path
from .autonomy_integration import create_adapter
from .autonomy_policy import pin_policy
from .autonomy_projection import project
from .autonomy_runtime import CodexAgent, GitHub, LocalWorker, diagnostics
from .autonomy_state import Store
from .source_verify import verify_source

Repo = Annotated[Path, typer.Option(exists=True, file_okay=False)]
_ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)


def _emit(value: dict) -> None:
    typer.echo(json.dumps(value, sort_keys=True, allow_nan=False))


def _failure(exc: Exception) -> NoReturn:
    _emit({"schema_version": 1, "status": "incomplete", "detail": str(exc)})
    raise typer.Exit(2)


def _store(ctx: typer.Context, repo: Path) -> Store:
    return Store(state_path(repo.resolve(), ctx.obj["state"]))


def register(app: typer.Typer) -> None:
    worker_app = typer.Typer(no_args_is_help=True, help="Manage the isolated local Linux worker.")
    app.add_typer(worker_app, name="worker")
    integration_app = typer.Typer(no_args_is_help=True, help="Serve the local integration contract.")
    app.add_typer(integration_app, name="integration")

    @app.command("init")
    def init_workspace(ctx: typer.Context, repo: Repo, merge_policy: Annotated[str, typer.Option(help="Trusted merge enforcement: github-protected or explicit controller-private.")] = "github-protected", trust_configured_checks: Annotated[bool, typer.Option(help="Trust the reviewed configured checks as mandatory gates.")] = False) -> None:
        """Initialize a versioned autonomy policy without altering application runtime."""
        try:
            config = initialize(repo)
            options = {"trust_configured_checks": True} if trust_configured_checks else {}
            policy = pin_policy(repo, ctx.obj["state"], config, merge_policy=merge_policy, **options)
            _emit({"schema_version": 1, "status": "initialized", "configuration": config,
                   "local_policy": policy,
                   "next": "Review and commit .mentat/workspace.json, then run mentat run --repo PATH."})
        except _ERRORS as exc:
            _failure(exc)

    def execute(ctx: typer.Context, repo: Path, resume: str | None, integration_file: Path | None, allow_origin: list[str]) -> None:
        adapter = None
        try:
            store = _store(ctx, repo)
            coordinator = Coordinator(repo, store, CodexAgent(), LocalWorker(), GitHub())
            if integration_file is not None:
                adapter = create_adapter(repo=repo.resolve(), store=store, session_id=lambda: coordinator.session_id,
                                         project=lambda raw, timeout: project(raw, store_path=store.path, timeout=timeout),
                                         connection_file=integration_file, allowed_origins=allow_origin,
                                         stop=lambda: coordinator.control("stop", {}),
                                         is_active=lambda: coordinator.session_id is not None and not coordinator.stop.is_set())
                adapter.start()
            elif allow_origin:
                raise ValueError("--allow-origin requires --integration-file")
            result = coordinator.run(resume=resume)
            _emit({"schema_version": 1, "status": result["session"]["status"],
                   "session_id": result["session"]["id"], "jobs": len(result["jobs"]),
                   "requirements": len(result["requirements"]), "claims": len(result["claims"]),
                   "formal_completion": "inspect individual evidence and gaps"})
        except _ERRORS as exc:
            _failure(exc)
        finally:
            if adapter is not None:
                adapter.close()

    @app.command("run")
    def run_workspace(ctx: typer.Context, repo: Repo, integration_file: Path | None = None,
                      allow_origin: Annotated[list[str] | None, typer.Option()] = None) -> None:
        """Run autonomous SCL, IVL and IIL within one bounded coding session."""
        execute(ctx, repo, None, integration_file, allow_origin or [])

    @app.command("resume")
    def resume_workspace(ctx: typer.Context, repo: Repo, session: str | None = None,
                         integration_file: Path | None = None,
                         allow_origin: Annotated[list[str] | None, typer.Option()] = None) -> None:
        """Resume durable work without discarding prior attempts or findings."""
        try:
            session_id = session or _store(ctx, repo).snapshot()["session"]["id"]
        except _ERRORS as exc:
            _failure(exc)
        execute(ctx, repo, session_id, integration_file, allow_origin or [])

    @app.command("status")
    def workspace_status(ctx: typer.Context, repo: Repo, session: str | None = None,
                         output_json: Annotated[bool, typer.Option("--json")] = False) -> None:
        """Inspect the exact revision and work retained by a session."""
        try:
            store = _store(ctx, repo)
            selected = session or store.integration_latest_session()
            value = project(store.integration_snapshot(selected), store_path=store.path)
            if output_json:
                _emit(value)
            else:
                typer.echo(f"Session {value['session']['id']}: {value['session']['status']}")
                typer.echo(f"Revision: {value['session']['head']}")
                typer.echo(f"{len(value['requirements'])} requirements; {len(value['claims'])} claims; {len(value['jobs'])} jobs")
                typer.echo("Formal assurance is reported per claim with explicit gaps.")
        except _ERRORS as exc:
            _failure(exc)

    @integration_app.command("serve")
    def integration_serve(ctx: typer.Context, repo: Repo, session: Annotated[str, typer.Option()],
                          connection_file: Annotated[Path, typer.Option()],
                          allow_origin: Annotated[list[str] | None, typer.Option()] = None) -> None:
        """Serve a saved session for inspection and optional feedback."""
        adapter = None
        try:
            store = _store(ctx, repo)
            selected = store.integration_snapshot(session)["session"]["id"]
            adapter = create_adapter(repo=repo.resolve(), store=store, session_id=lambda: selected,
                                     project=lambda raw, timeout: project(raw, store_path=store.path, timeout=timeout),
                                     connection_file=connection_file, allowed_origins=allow_origin or ())
            adapter.start()
            adapter.wait()
        except KeyboardInterrupt:
            pass
        except _ERRORS as exc:
            _failure(exc)
        finally:
            if adapter is not None:
                adapter.close()

    @app.command("source-verify")
    def source_verification(repo: Repo, rev: str, path: str, function: str,
                            spec: Annotated[Path, typer.Option(exists=True, dir_okay=False)]) -> None:
        """Prove a supported committed Python function under an explicit sidecar contract."""
        try:
            if spec.stat().st_size > 16384:
                raise ValueError("source contract is too large")
            contract = json.loads(spec.read_text())
            if not isinstance(contract, dict) or set(contract) != {"inputs", "requires", "ensures"}:
                raise ValueError("source contract needs inputs, requires, and ensures")
            result = verify_source(repo, rev, path, function, **contract)
        except _ERRORS as exc:
            _failure(exc)
        _emit(result)
        raise typer.Exit({"proved": 0, "counterexample": 1, "incomplete": 2}[result["status"]])

    @worker_app.command("doctor")
    def worker_doctor(repo: Repo) -> None:
        """Inspect existing Codex/GitHub authentication and local worker prerequisites."""
        try:
            _emit({"schema_version": 1, "runtime": diagnostics(repo), "worker": LocalWorker().diagnostics()})
        except _ERRORS as exc:
            _failure(exc)

    @worker_app.command("provision")
    def worker_provision() -> None:
        """Provision the dedicated local worker without mounting application or home directories."""
        try:
            result = LocalWorker().provision()
        except _ERRORS as exc:
            _failure(exc)
        _emit(result)
        if result.get("status") not in {"ready", "succeeded"}:
            raise typer.Exit(2)
