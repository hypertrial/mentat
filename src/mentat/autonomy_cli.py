"""Additive CLI for session-scoped autonomous work and direct source evidence."""

import copy
import json
import subprocess
import threading
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from .autonomy import Coordinator, digest, initialize, project_snapshot, state_path
from .autonomy_policy import load_policy_path, pin_policy
from .autonomy_runtime import CodexAgent, GitHub, LocalWorker, diagnostics, git
from .autonomy_state import Store
from .autonomy_ui import create_server, render_report
from .source_verify import source_tool_identity, verify_source

Repo = Annotated[Path, typer.Option(exists=True, file_okay=False)]
_ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)


def _emit(value: dict) -> None:
    typer.echo(json.dumps(value, sort_keys=True, allow_nan=False))


def _failure(exc: Exception) -> NoReturn:
    _emit({"schema_version": 1, "status": "incomplete", "detail": str(exc)})
    raise typer.Exit(2)


def _store(ctx: typer.Context, repo: Path) -> Store:
    return Store(state_path(repo.resolve(), ctx.obj["state"]))


def _project(snapshot: dict, *, store_path: Path | None = None) -> dict:
    snapshot = copy.deepcopy(snapshot)
    session = snapshot["session"]
    try:
        repo = Path(session["repo"])
        current_head = git(repo, "rev-parse", "HEAD")
        dirty = bool(git(repo, "status", "--porcelain=v1", "--untracked-files=all"))
        changed = current_head != session["head"] or dirty
    except (*_ERRORS, KeyError):
        current_head = None
        changed = True
    authorities = [event["payload"].get("digest") for event in snapshot.get("events", [])
                   if event.get("kind") == "authority_pinned"]
    try:
        changed |= store_path is None or authorities != [digest(load_policy_path(store_path))]
    except (*_ERRORS, KeyError):
        changed = True
    if changed:
        session["historical_status"] = session.get("status")
        session["status"] = "invalidated"
        for key in ("requirements", "claims", "evidence"):
            for record in snapshot.get(key, []):
                record["freshness"] = "stale"
    try:
        identity = source_tool_identity()
    except _ERRORS:
        identity = {}
    projected = project_snapshot(snapshot, identity=identity)
    # A proposal can be proved before it is adopted into the checked-out tree.
    # Keep that evidence visible, but do not present it as current-tree assurance.
    for claim in projected.get("claims", []):
        scope = claim.get("proof_scope", {})
        if scope.get("revision") and scope["revision"] != current_head:
            claim["freshness"] = "stale"
    return projected


def register(app: typer.Typer) -> None:
    worker_app = typer.Typer(no_args_is_help=True, help="Manage the isolated local Linux worker.")
    app.add_typer(worker_app, name="worker")

    @app.command("init")
    def init_workspace(ctx: typer.Context, repo: Repo) -> None:
        """Initialize a versioned autonomy policy without altering application runtime."""
        try:
            config = initialize(repo)
            policy = pin_policy(repo, ctx.obj["state"], config)
            _emit({"schema_version": 1, "status": "initialized", "configuration": config,
                   "local_policy": policy,
                   "next": "Review and commit .mentat/workspace.json, then run mentat run --repo PATH."})
        except _ERRORS as exc:
            _failure(exc)

    def execute(ctx: typer.Context, repo: Path, resume: str | None, ui: bool) -> None:
        server = None
        try:
            store = _store(ctx, repo)
            coordinator = Coordinator(repo, store, CodexAgent(), LocalWorker(), GitHub())

            def snapshot() -> dict:
                if coordinator.session_id is None:
                    return {"schema_version": 1, "revision": 0, "session": {"status": "starting"}}
                return _project(store.snapshot(coordinator.session_id), store_path=store.path)

            if ui:
                server = create_server(snapshot, coordinator.control)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                typer.echo(f"Monitor this session: {server.url}", err=True)
            result = coordinator.run(resume=resume)
            report = store.path.parent / f"{result['session']['id']}.html"
            report.write_text(render_report(_project(store.snapshot(result['session']['id']), store_path=store.path)), encoding="utf-8")
            _emit({"schema_version": 1, "status": result["session"]["status"],
                   "session_id": result["session"]["id"], "report": str(report),
                   "jobs": len(result["jobs"]), "requirements": len(result["requirements"]),
                   "claims": len(result["claims"]), "formal_completion": "inspect individual evidence and gaps"})
        except _ERRORS as exc:
            _failure(exc)
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()

    @app.command("run")
    def run_workspace(ctx: typer.Context, repo: Repo, ui: bool = True) -> None:
        """Run autonomous SCL, IVL and IIL within one bounded coding session."""
        execute(ctx, repo, None, ui)

    @app.command("resume")
    def resume_workspace(ctx: typer.Context, repo: Repo, session: str | None = None, ui: bool = True) -> None:
        """Resume durable work without discarding prior attempts or findings."""
        try:
            session_id = session or _store(ctx, repo).snapshot()["session"]["id"]
        except _ERRORS as exc:
            _failure(exc)
        execute(ctx, repo, session_id, ui)

    @app.command("status")
    def workspace_status(ctx: typer.Context, repo: Repo, session: str | None = None,
                         output_json: Annotated[bool, typer.Option("--json")] = False) -> None:
        """Inspect the exact revision and work retained by a session."""
        try:
            store = _store(ctx, repo)
            value = _project(store.snapshot(session), store_path=store.path)
            if output_json:
                _emit(value)
            else:
                typer.echo(f"Session {value['session']['id']}: {value['session']['status']}")
                typer.echo(f"Revision: {value['session']['head']}")
                typer.echo(f"{len(value['requirements'])} requirements; {len(value['claims'])} claims; {len(value['jobs'])} jobs")
                typer.echo("Formal assurance is reported per claim with explicit gaps.")
        except _ERRORS as exc:
            _failure(exc)

    @app.command("ui")
    def workspace_ui(ctx: typer.Context, repo: Repo, session: str | None = None, port: int = 0) -> None:
        """Browse saved activity; feedback is retained for subsequent iterations."""
        server = None
        try:
            store = _store(ctx, repo)
            selected = store.snapshot(session)["session"]["id"]

            def control(action: str, payload: dict) -> dict:
                if action == "feedback":
                    store.append_event(selected, "human_feedback", payload)
                    return {"status": "recorded", "detail": "Feedback retained for the next active iteration."}
                raise ValueError("saved-session viewer cannot control another process")

            server = create_server(lambda: _project(store.snapshot(selected), store_path=store.path), control, port=port)
            typer.echo(f"Monitor: {server.url}")
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        except _ERRORS as exc:
            _failure(exc)
        finally:
            if server is not None:
                server.server_close()

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
