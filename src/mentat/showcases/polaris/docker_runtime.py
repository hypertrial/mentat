"""Docker execution for committed candidate code; all failures are fail-closed."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import selectors
import subprocess
import tempfile
import time
from pathlib import Path

from mentat.colima import validate_colima as _local_context

_IMAGE = "mentat-trusted-polaris-cli:1.8.0"
_SERVER = "apache/polaris@sha256:74e6416c75f24563fc62f4ed52699d4fb872741b6a3e0af77fdc551b4ee72fce"
_RUNTIME = Path(__file__).parent / "pack" / "runtime"
_RECIPE_LABEL = "org.hypertrial.mentat.runner-recipe"
_TIMEOUT = 180
_MAX_OUTPUT = 1024 * 1024


def _docker(context: str, *args: str, timeout: int = _TIMEOUT) -> tuple[int, bytes]:
    if not context or context == "default" or not re.fullmatch(r"[A-Za-z0-9_.-]+", context):
        raise ValueError("an explicit dedicated Docker context is required")
    try:
        process = subprocess.Popen(
            ["docker", "--context", context, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        output = bytearray()
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise RuntimeError("Docker command timed out")
                chunk = process.stdout.read1(65536)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > _MAX_OUTPUT:
                    raise RuntimeError("Docker output exceeded limit")
        return process.wait(timeout=max(0.01, deadline - time.monotonic())), bytes(output)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Docker command unavailable or timed out") from exc
    finally:
        if "process" in locals() and process.poll() is None:
            process.kill()
            process.wait()


def doctor(context: str) -> dict:
    try:
        _local_context(context)
        code, engine = _docker(
            context,
            "info",
            "--format",
            "{{.ServerVersion}} {{.OSType}}/{{.Architecture}}",
            timeout=20,
        )
        image_code, image = _docker(
            context,
            "image",
            "inspect",
            _IMAGE,
            "--format",
            '{{.Id}} {{index .Config.Labels "' + _RECIPE_LABEL + '"}}',
            timeout=20,
        )
        server_code, server = _docker(
            context, "image", "inspect", _SERVER, "--format", "{{.Id}}", timeout=20
        )
        engine_info = engine.decode("ascii", errors="replace").split()
        image_info = image.decode("ascii", errors="replace").split()
        current_runner = image_code == 0 and len(image_info) == 2 and image_info[1] == _recipe()
        return {
            "ok": code == 0
            and len(engine_info) == 2
            and engine_info[1] in {"linux/aarch64", "linux/arm64"}
            and current_runner
            and server_code == 0,
            "context": context,
            "docker_engine": engine_info[0] if engine_info else None,
            "server_image": server.decode("ascii", errors="replace").strip()
            if server_code == 0
            else None,
            "server_reference": _SERVER,
            "client_image": image_info[0] if current_runner else None,
        }
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError):
        return {"ok": False, "context": context, "client_image": None}


def _recipe() -> str:
    return hashlib.sha256(
        (_RUNTIME / "Dockerfile").read_bytes()
        + b"\0"
        + (_RUNTIME / "requirements.txt").read_bytes()
    ).hexdigest()


def prepare(context: str) -> dict:
    try:
        _local_context(context)
        code, _ = _docker(context, "pull", _SERVER, timeout=600)
        if code:
            return {"ok": False, "context": context}
        code, _ = _docker(
            context,
            "build",
            "--pull",
            "--label",
            f"{_RECIPE_LABEL}={_recipe()}",
            "-t",
            _IMAGE,
            str(_RUNTIME),
            timeout=600,
        )
        return doctor(context) if code == 0 else {"ok": False, "context": context}
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError):
        return {"ok": False, "context": context}


def _name(run_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("invalid run ID")
    return "mentat-" + hashlib.sha256(run_id.encode()).hexdigest()[:24]


def _stage_cli(snapshot, cli: Path) -> None:
    seen = set()
    prefixes = {}
    total = 0
    for relative, content in snapshot.files:
        if (
            not re.fullmatch(r"[A-Za-z0-9_/-]+\.py", relative)
            or any(part in ("", ".", "..") for part in relative.split("/"))
            or relative in seen
            or not isinstance(content, bytes)
        ):
            raise ValueError("invalid candidate source")
        seen.add(relative)
        for part in (Path(relative), *Path(relative).parents):
            spelling = part.as_posix()
            if prefixes.setdefault(spelling.lower(), spelling) != spelling:
                raise ValueError("candidate paths collide on a case-insensitive filesystem")
        total += len(content)
        if len(content) > 2 * 1024 * 1024 or total > 16 * 1024 * 1024:
            raise ValueError("candidate source exceeds limit")
        path = cli / relative
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        path.write_bytes(content)
        path.chmod(0o644)
    if "command/setup.py" not in seen:
        raise ValueError("missing candidate setup command")
    # Docker copies these modes into a container that runs as an unprivileged user.
    cli.chmod(0o755)
    for path in cli.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)


def run_pytest(
    snapshot,
    context: str,
    test_source: str,
    run_id: str,
    support_files: dict[str, str] | None = None,
) -> dict:
    result = {
        "status": "incomplete",
        "checks": [],
        "revision": snapshot.revision,
        "snapshot_digest": snapshot.digest,
        "runtime": doctor(context),
    }
    if not result["runtime"]["ok"]:
        return result
    name = None
    created = False
    try:
        if len(test_source.encode()) > _MAX_OUTPUT:
            raise ValueError("test exceeds limit")
        name = _name(run_id)
        with tempfile.TemporaryDirectory(prefix="mentat-") as temp:
            root = Path(temp)
            cli = root / "cli"
            cli.mkdir(mode=0o755)
            _stage_cli(snapshot, cli)
            total = sum(len(content) for _, content in snapshot.files)
            test = root / "test_candidate.py"
            test.write_text(test_source, encoding="utf-8")
            test.chmod(0o644)
            support_paths = []
            for filename, source in (support_files or {}).items():
                if (
                    not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", filename)
                    or filename == test.name
                    or len(source.encode()) > _MAX_OUTPUT
                ):
                    raise ValueError("invalid support file")
                total += len(source.encode())
                if total > 16 * 1024 * 1024:
                    raise ValueError("support files exceed limit")
                path = root / filename
                path.write_text(source, encoding="utf-8")
                path.chmod(0o644)
                support_paths.append(path)
            code, _ = _docker(
                context,
                "create",
                "--name",
                name,
                "--label",
                f"org.hypertrial.mentat.run={run_id}",
                "--network",
                "none",
                "--log-opt",
                "max-size=1m",
                "--log-opt",
                "max-file=1",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "128",
                "--memory",
                "1g",
                "--cpus",
                "1",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--user",
                "65534:65534",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                result["runtime"]["client_image"],
                "python",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "/opt/test_candidate.py",
            )
            if code:
                return result
            created = True
            if _docker(context, "cp", str(cli) + "/.", f"{name}:/opt/polaris/apache_polaris/cli/")[
                0
            ]:
                return result
            if _docker(context, "cp", str(test), f"{name}:/opt/test_candidate.py")[0]:
                return result
            for path in support_paths:
                if _docker(context, "cp", str(path), f"{name}:/opt/{path.name}")[0]:
                    return result
            code, _ = _docker(context, "start", "-a", name, timeout=120)
            inspect_code, state = _docker(
                context,
                "inspect",
                "--format",
                "{{.State.Status}} {{.State.ExitCode}}",
                name,
                timeout=20,
            )
            if inspect_code or state.decode("ascii", errors="replace").strip() != f"exited {code}":
                raise RuntimeError("container did not finish normally")
            result["status"] = "pass" if code == 0 else "violation" if code == 1 else "incomplete"
            result["checks"].append({"name": "pytest", "status": result["status"]})
    except (ValueError, RuntimeError, OSError):
        pass
    finally:
        if created and name:
            try:
                _docker(context, "rm", "-f", name, timeout=20)
            except (ValueError, RuntimeError):
                pass
    return result


def run_case(
    snapshot, context: str, case, selected_properties: tuple[str, ...], run_id: str
) -> dict:
    from .oracle import case_payload, compare, validate_export

    result = {
        "case_id": case.id,
        "status": "incomplete",
        "checks": [],
        "revision": snapshot.revision,
        "snapshot_digest": snapshot.digest,
        "runtime": doctor(context),
    }
    if not result["runtime"]["ok"]:
        return result
    containers: list[str] = []
    networks: list[str] = []
    phase = "initialization"
    try:
        prefix = _name(run_id) + "-" + secrets.token_hex(6)
        image = result["runtime"]["client_image"]
        server_image = result["runtime"]["server_image"]
        passwords = [secrets.token_hex(24), secrets.token_hex(24)]
        with tempfile.TemporaryDirectory(prefix="mentat-replay-") as temp:
            root = Path(temp)
            cli = root / "cli"
            cli.mkdir(mode=0o755)
            _stage_cli(snapshot, cli)
            fixture = root / "fixture.json"
            fixture.write_bytes(case_payload(case))
            fixture.chmod(0o644)
            oracle = root / "oracle_cli.py"
            oracle.write_bytes((_RUNTIME / "oracle_cli.py").read_bytes())
            oracle.chmod(0o644)

            def create(name: str, *arguments: str) -> None:
                containers.append(name)
                if _docker(
                    context,
                    "create",
                    "--name",
                    name,
                    "--label",
                    f"org.hypertrial.mentat.run={run_id}",
                    *arguments,
                )[0]:
                    raise RuntimeError("container creation failed")

            def client(
                name: str,
                network: str,
                password: str,
                command: list[str],
                copies: list[tuple[Path, str]],
                *,
                candidate: bool = False,
                timeout: int = 120,
            ) -> tuple[int, bytes]:
                create(
                    name,
                    "--network",
                    network,
                    "--log-opt",
                    "max-size=1m",
                    "--log-opt",
                    "max-file=1",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    "128",
                    "--memory",
                    "1g",
                    "--cpus",
                    "1",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=64m",
                    "--user",
                    "65534:65534",
                    "--env",
                    "HOME=/tmp",
                    "--env",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "--env",
                    "POLARIS_HOST=http://polaris:8181",
                    "--env",
                    "POLARIS_CLIENT_ID=root",
                    "--env",
                    f"POLARIS_CLIENT_SECRET={password}",
                    image,
                    *command,
                )
                if candidate:
                    copies = [(cli / ".", "/opt/polaris/apache_polaris/cli/")] + copies
                for source, target in copies:
                    source_arg = str(source) + ("/." if source == cli else "")
                    if _docker(context, "cp", source_arg, f"{name}:{target}")[0]:
                        raise RuntimeError("container copy failed")
                code, output = _docker(context, "start", "-a", name, timeout=timeout)
                inspect_code, state = _docker(
                    context,
                    "inspect",
                    "--format",
                    "{{.State.Status}} {{.State.ExitCode}}",
                    name,
                    timeout=20,
                )
                if (
                    inspect_code
                    or state.decode("ascii", errors="replace").strip() != f"exited {code}"
                ):
                    raise RuntimeError("container did not finish normally")
                return code, output

            def oracle_call(index: int, action: str) -> bytes:
                name = f"{prefix}-oracle-{index}-{action}-{len(containers)}"
                code, output = client(
                    name,
                    networks[index],
                    passwords[index],
                    ["python", "/opt/oracle_cli.py", action],
                    [(oracle, "/opt/oracle_cli.py"), (fixture, "/opt/fixture.json")],
                )
                if code:
                    raise RuntimeError("oracle request failed")
                return output

            phase = "server startup"
            for i in range(2):
                network = f"{prefix}-net-{i}"
                networks.append(network)
                if _docker(
                    context,
                    "network",
                    "create",
                    "--internal",
                    "--label",
                    f"org.hypertrial.mentat.run={run_id}",
                    network,
                )[0]:
                    raise RuntimeError("network creation failed")
                server = f"{prefix}-server-{i}"
                create(
                    server,
                    "--network",
                    network,
                    "--network-alias",
                    "polaris",
                    "--log-opt",
                    "max-size=1m",
                    "--log-opt",
                    "max-file=1",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    "512",
                    "--memory",
                    "2g",
                    "--cpus",
                    "2",
                    "--tmpfs",
                    "/tmp:rw,nosuid,size=256m",
                    "--env",
                    f"POLARIS_BOOTSTRAP_CREDENTIALS=POLARIS,root,{passwords[i]}",
                    "--env",
                    "polaris.realm-context.realms=POLARIS",
                    "--env",
                    "quarkus.otel.sdk.disabled=true",
                    "--env",
                    'polaris.features."ALLOW_INSECURE_STORAGE_TYPES"=true',
                    "--env",
                    'polaris.features."SUPPORTED_CATALOG_STORAGE_TYPES"=["FILE"]',
                    "--env",
                    "polaris.readiness.ignore-severe-issues=true",
                    server_image,
                )
                if _docker(context, "start", server, timeout=30)[0]:
                    raise RuntimeError("server start failed")
                probe = (
                    "import time,urllib.request,sys\n"
                    "for _ in range(90):\n"
                    " try:\n"
                    "  urllib.request.urlopen('http://polaris:8182/q/health',timeout=2).close();sys.exit(0)\n"
                    " except Exception: time.sleep(1)\n"
                    "sys.exit(1)\n"
                )
                if client(
                    f"{prefix}-probe-{i}",
                    network,
                    passwords[i],
                    ["python", "-c", probe],
                    [],
                    timeout=120,
                )[0]:
                    raise RuntimeError("server readiness failed")

            phase = "source seed"
            oracle_call(0, "seed")
            phase = "source verification"
            source = json.loads(oracle_call(0, "read"))
            source_checks = compare(case, source, selected_properties, "source")
            if not source_checks or any(check.get("status") != "pass" for check in source_checks):
                raise RuntimeError("source fixture verification failed")
            result["checks"].extend(source_checks)
            phase = "destination precondition"
            blank = json.loads(oracle_call(1, "read"))
            if blank != {"catalog_exists": False, "namespaces": []}:
                raise RuntimeError("destination must start without the fixture catalog")

            def candidate(index: int, action: str, copies: list[tuple[Path, str]] = ()):
                args = [
                    "python",
                    "-m",
                    "apache_polaris.cli.polaris_cli",
                    "--host",
                    "polaris",
                    "--port",
                    "8181",
                    "--client-id",
                    "root",
                    "--client-secret",
                    passwords[index],
                    "setup",
                    action,
                ]
                if action == "apply":
                    args.append("/opt/export.yaml")
                return client(
                    f"{prefix}-candidate-{index}-{action}",
                    networks[index],
                    passwords[index],
                    args,
                    list(copies),
                    candidate=True,
                )

            phase = "export"
            code, exported = candidate(0, "export")
            if code:
                result["checks"].append(
                    {
                        "id": "namespace.identity",
                        "stage": phase,
                        "status": "violation",
                        "detail": "Candidate export failed",
                    }
                )
                return result
            phase = "source after export"
            after_export = json.loads(oracle_call(0, "read"))
            result["checks"].extend(compare(case, after_export, selected_properties, phase))
            phase = "export"
            try:
                projection = validate_export(exported, selected_properties)
            except ValueError:
                result["checks"].append(
                    {
                        "id": "namespace.identity",
                        "stage": phase,
                        "status": "violation",
                        "detail": "Candidate export was invalid or unsafe",
                    }
                )
                return result
            result["checks"].extend(compare(case, projection, selected_properties, "export"))
            export_file = root / "export.yaml"
            export_file.write_bytes(exported)
            export_file.chmod(0o644)
            phase = "apply"
            apply_code, _ = candidate(1, "apply", [(export_file, "/opt/export.yaml")])
            if apply_code:
                result["checks"].append(
                    {
                        "id": "namespace.identity",
                        "stage": phase,
                        "status": "violation",
                        "detail": "Candidate apply failed",
                    }
                )
            phase = "destination verification"
            destination = json.loads(oracle_call(1, "read"))
            result["checks"].extend(compare(case, destination, selected_properties, "restore"))
            if not destination.get("catalog_exists"):
                return result
            phase = "re-export"
            code, reexported = candidate(1, "export")
            if code:
                result["checks"].append(
                    {
                        "id": "namespace.identity",
                        "stage": phase,
                        "status": "violation",
                        "detail": "Candidate re-export failed",
                    }
                )
                return result
            try:
                reprojection = validate_export(reexported, selected_properties)
            except ValueError:
                result["checks"].append(
                    {
                        "id": "namespace.identity",
                        "stage": phase,
                        "status": "violation",
                        "detail": "Candidate re-export was invalid or unsafe",
                    }
                )
                return result
            result["checks"].extend(compare(case, reprojection, selected_properties, "re-export"))
            phase = "destination after re-export"
            final_source = json.loads(oracle_call(1, "read"))
            result["checks"].extend(compare(case, final_source, selected_properties, phase))
    except (ValueError, RuntimeError, OSError, TypeError, KeyError):
        result["checks"].append(
            {
                "id": "namespace.identity",
                "stage": phase,
                "status": "incomplete",
                "detail": "Replay could not complete this stage",
            }
        )
    finally:
        cleanup_ok = True
        for container in reversed(containers):
            try:
                if _docker(context, "rm", "-f", container, timeout=20)[0]:
                    cleanup_ok = False
            except (ValueError, RuntimeError):
                cleanup_ok = False
        for network in reversed(networks):
            try:
                if _docker(context, "network", "rm", network, timeout=20)[0]:
                    cleanup_ok = False
            except (ValueError, RuntimeError):
                cleanup_ok = False
        if not cleanup_ok:
            result["checks"].append(
                {
                    "id": "namespace.identity",
                    "stage": "cleanup",
                    "status": "incomplete",
                    "detail": "Replay resources could not all be removed",
                }
            )
        statuses = {check.get("status") for check in result["checks"]}
        result["status"] = (
            "violation"
            if "violation" in statuses
            else "incomplete"
            if not statuses or statuses != {"pass"}
            else "pass"
        )
    return result
