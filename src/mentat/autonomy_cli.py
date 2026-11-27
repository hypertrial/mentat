"""Additive CLI for session-scoped autonomous work and direct source evidence."""

import json
import os
import stat
import subprocess
import time
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from .autonomy import Coordinator, initialize, state_path
from .autonomy_integration import create_adapter
from .autonomy_policy import pin_policy
from .autonomy_projection import project
from .autonomy_runtime import CodexAgent, GitHub, LocalWorker, diagnostics, git
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


def _trial_store(ctx: typer.Context, repo: Path) -> Store:
    if not ctx.obj["state_explicit"]:
        raise ValueError("trial-run requires an explicit private --state directory per request")
    path = state_path(repo, ctx.obj["state"])
    root = Path(ctx.obj["state"]).expanduser().resolve()
    for directory in (root, path.parent.parent, path.parent):
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("trial state must be owned by the operator and inaccessible to other users")
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("unsafe trial database")
    return Store(path)


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

    def execute(ctx: typer.Context, repo: Path, resume: str | None, integration_file: Path | None,
                allow_origin: list[str], spec_surface: str | None = None, *, local_trial: dict | None = None) -> None:
        adapter = None
        closed_trial = False
        try:
            repo = repo.resolve()
            store = _trial_store(ctx, repo) if local_trial else _store(ctx, repo)
            options = {"local_trial": local_trial} if local_trial is not None else {}
            coordinator = Coordinator(repo, store, CodexAgent(), LocalWorker(), GitHub(), **options)
            if local_trial is not None:
                from .autonomy_policy import pin_local_trial_policy

                if (git(repo, "rev-parse", "--show-toplevel") != str(repo)
                        or git(repo, "rev-parse", "HEAD") != local_trial["base"]
                        or git(repo, "remote") or git(repo, "status", "--porcelain", "--untracked-files=all")):
                    raise ValueError("trial-run requires a clean standalone checkout at the exact base with no Git remotes")
                pin_local_trial_policy(store, repo, coordinator.local_trial)
            if integration_file is not None:
                adapter = create_adapter(repo=repo.resolve(), store=store, session_id=lambda: coordinator.session_id,
                                         project=lambda raw, timeout: project(raw, store_path=store.path, timeout=timeout),
                                         connection_file=integration_file, allowed_origins=allow_origin,
                                         stop=lambda: coordinator.control("stop", {}),
                                         is_active=lambda: coordinator.session_id is not None and not coordinator.stop.is_set())
                adapter.start()
            elif allow_origin:
                raise ValueError("--allow-origin requires --integration-file")
            while True:
                result = coordinator.run(resume=resume, **({"spec_surface": spec_surface} if spec_surface is not None else {}))
                session = result["session"]
                _emit({"schema_version": 1, "status": session["status"],
                       "session_id": session["id"], "jobs": len(result["jobs"]),
                       "requirements": len(result["requirements"]), "claims": len(result["claims"]),
                       "formal_completion": "inspect individual evidence and gaps"})
                if local_trial is None or adapter is None:
                    break
                # Keep the same loopback generation for inspection and clarification.
                # A newer fixture base is a different Cockpit scope and state directory.
                deadline = store.session(session["id"])["deadline"]
                while time.time() < deadline and git(repo, "rev-parse", "HEAD") == local_trial["base"]:
                    if session["status"] in {"completed", "blocked"} and store.pending_feedback(session["id"], limit=1):
                        break
                    time.sleep(1)
                else:
                    closed_trial = (not any(job["status"] == "running" for job in result["jobs"])
                                    and not any(not task.done() for task in coordinator._pending_tasks)
                                    and not store.path.with_suffix(".termination-unconfirmed").exists())
                    break
        except KeyboardInterrupt:
            if local_trial is None:
                raise
        except _ERRORS as exc:
            _failure(exc)
        finally:
            if adapter is not None:
                adapter.close()
        if closed_trial and local_trial is not None:
            _emit({"schema_version": 1, "status": "closed", "base": local_trial["base"],
                   "request_id": local_trial["request_id"]})

    @app.command("run")
    def run_workspace(ctx: typer.Context, repo: Repo, integration_file: Path | None = None,
                      allow_origin: Annotated[list[str] | None, typer.Option()] = None,
                      spec_surface: Annotated[str | None, typer.Option(help="Review one committed FAS surface without agent file edits.")] = None) -> None:
        """Run autonomous SCL, IVL and IIL within one bounded coding session."""
        execute(ctx, repo, None, integration_file, allow_origin or [], spec_surface)

    @app.command("trial-run")
    def run_local_trial(ctx: typer.Context, repo: Repo, base: Annotated[str, typer.Option()],
                        request_id: Annotated[str, typer.Option()], integration_file: Path | None = None,
                        repair_path: Annotated[list[str] | None, typer.Option()] = None) -> None:
        """Analyze an isolated local checkout under a bounded, durable trial request."""
        execute(ctx, repo, None, integration_file, [], local_trial={
            "base": base, "request_id": request_id, "repair_paths": tuple(repair_path or ()),
        })

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
