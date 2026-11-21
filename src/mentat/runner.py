"""Run trusted behavior checks against a fresh candidate snapshot."""

from __future__ import annotations

import io
import json
import os
import platform
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from . import process
from .colima import validate_colima
from .evidence import decode_json
from .project import CHECKS_PREFIX, _validate_staged

_MAX_OUTPUT = 1024 * 1024
_MAX_ARTIFACT = 1024 * 1024
_MAX_RUN_OUTPUT = 16 * 1024 * 1024
_DOCKER_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _absolute_search_path(path: str, default: str) -> str:
    # A relative entry would resolve tools from the staged candidate directory.
    return (
        os.pathsep.join(part for part in path.split(os.pathsep) if os.path.isabs(part)) or default
    )


def _arguments(argv: list[str], checks: str) -> list[str]:
    return [
        checks + "/" + arg.removeprefix(CHECKS_PREFIX) if arg.startswith(CHECKS_PREFIX) else arg
        for arg in argv
    ]


def _process(argv, *, cwd=None, env=None, timeout=30, input_file=None, output_limit=_MAX_OUTPUT):
    return process.run(
        argv,
        cwd=cwd,
        env=env,
        timeout=timeout,
        stdin=input_file,
        output_limit=output_limit,
    )


def _docker(context, *args, timeout=30, input_file=None, output_limit=_MAX_OUTPUT):
    try:
        return _process(
            ["docker", "--context", context, *args],
            timeout=timeout,
            input_file=input_file,
            output_limit=output_limit,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError, RuntimeError) as exc:
        raise ValueError(f"Docker command failed: {exc}") from exc


def _local_context(context):
    if not context or context == "default" or not re.fullmatch(r"[A-Za-z0-9_.-]+", context):
        raise ValueError("select an explicit local Docker context")
    if platform.system() == "Darwin":
        validate_colima(context)
    elif platform.system() == "Linux":
        code, output, _ = _docker(
            context,
            "context",
            "inspect",
            context,
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        )
        if code or not output.strip().startswith(b"unix:///"):
            raise ValueError("Docker context must use a local Unix socket")
    else:
        raise ValueError("Docker runner requires Linux or an unshared Colima VM")


def host_path():
    return _absolute_search_path(os.environ.get("PATH", os.defpath), os.defpath)


def host_diagnostics(info):
    prerequisites = info.get("prerequisites", {})
    diagnostics = [
        f"Host prerequisite {group}: {name!r} is unavailable. {hint}"
        for group, hint in (
            ("executables", "Check the declared path or the recorded PATH."),
            ("python_distributions", "Check the python3 environment on the recorded PATH."),
        )
        for name, item in prerequisites.get(group, {}).items()
        if item["status"] != "available"
    ]
    for name, item in prerequisites.get("host_environment", {}).items():
        if item["status"] != "available":
            diagnostics.append(f"Host environment variable {name!r} is unavailable.")
    if prerequisites.get("loopback") and prerequisites["loopback"]["status"] != "available":
        diagnostics.append(
            "Host prerequisite loopback is unavailable. Check local bind permissions."
        )
    for command, item in info.get("executables", {}).items():
        if item["status"] == "missing" and item.get("checks"):
            diagnostics.append(
                f"Host executable {command!r} is unavailable for checks: "
                + ", ".join(item["checks"])
            )
    return diagnostics


def _executable(command, path):
    if not os.path.isabs(command) and ("/" in command or command.startswith("{checks}/")):
        return {"status": "deferred", "path": None}
    resolved = shutil.which(command, path=path)
    return {"status": "available" if resolved else "missing", "path": resolved}


def _host_info(project):
    path = host_path()
    requirements = getattr(project.contract, "host_requirements", None)
    declared = requirements.executables if requirements else []
    commands = [check.effective_argv()[0] for check in project.contract.behavior]
    executables = {command: _executable(command, path) for command in [*commands, *declared]}
    for check, command in zip(project.contract.behavior, commands):
        executables[command].setdefault("checks", []).append(check.id)
    distributions = requirements.python_distributions if requirements else []
    packages = {name: {"status": "unavailable"} for name in distributions}
    python = shutil.which("python3", path=path)
    if python and distributions:
        script = (
            "import importlib.metadata as m,json,sys\n"
            "result={}\n"
            "for name in sys.argv[1:]:\n"
            " try: result[name]={'status':'available','version':m.version(name)}\n"
            " except m.PackageNotFoundError: result[name]={'status':'missing'}\n"
            "print(json.dumps(result))\n"
        )
        try:
            with tempfile.TemporaryDirectory(prefix="mentat-host-inspect-") as temp:
                code, output, _ = _process(
                    [python, "-I", "-c", script, *distributions],
                    cwd=temp,
                    env={"PATH": path, "HOME": temp},
                    timeout=30,
                )
            if code == 0:
                packages = json.loads(output)
        except (OSError, subprocess.SubprocessError, TimeoutError, RuntimeError, ValueError):
            pass
    loopback = None
    if requirements and requirements.loopback:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
            loopback = {"status": "available"}
        except OSError:
            loopback = {"status": "missing"}
    environment = {
        name: {"status": "available" if os.environ.get(name) else "missing"}
        for check in project.contract.behavior
        for name in check.host_environment
    }
    return {
        "runner": "host",
        "platform": f"{platform.system()}/{platform.machine()}",
        "path": path,
        "executables": executables,
        "prerequisites": {
            "executables": {name: executables[name] for name in declared},
            "python_distributions": packages,
            "python_executable": python if distributions else None,
            "loopback": loopback,
            **({"host_environment": environment} if environment else {}),
        },
    }


def runner_info(project, context: str | None) -> dict:
    if project.runner == "host":
        return _host_info(project)
    if project.runner != "docker":
        raise ValueError("unsupported runner")
    context = context or ("colima-mentat" if platform.system() == "Darwin" else None)
    _local_context(context)
    reference = project.contract.docker_image
    if not isinstance(reference, str) or not re.fullmatch(
        r"[^\s@]+@sha256:[0-9a-f]{64}", reference
    ):
        raise ValueError("Docker image must be pinned by sha256 digest")
    code, output, _ = _docker(context, "image", "inspect", reference, timeout=20)
    if code:
        raise ValueError(
            f"Pinned Docker image is unavailable. Run docker --context {context} pull {reference}"
        )
    try:
        image = json.loads(output)[0]
        image_id = image["Id"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("invalid image ID")
        config = image.get("Config") or {}
        if config.get("Volumes"):
            raise ValueError("Docker image must not declare volumes")
        image_path = next(
            (
                value.removeprefix("PATH=")
                for value in config.get("Env") or []
                if value.startswith("PATH=")
            ),
            _DOCKER_PATH,
        )
        code, output, _ = _docker(
            context,
            "info",
            "--format",
            "{{.OSType}}/{{.Architecture}} {{.ServerVersion}} {{.MemoryLimit}} {{.CPUCfsQuota}} {{.PidsLimit}}",
            timeout=20,
        )
        engine = output.split()
        if (
            code
            or len(engine) != 5
            or engine[0]
            not in (
                b"linux/x86_64",
                b"linux/amd64",
                b"linux/aarch64",
                b"linux/arm64",
            )
        ):
            raise ValueError("Docker engine must run Linux on x86_64 or arm64")
        if engine[2:] != [b"true", b"true", b"true"]:
            raise ValueError("Docker engine must enforce memory, CPU, and process limits")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Could not inspect Docker image") from exc
    return {
        "runner": "docker",
        "context": context,
        "image": reference,
        "image_id": image_id,
        "platform": engine[0].decode("ascii"),
        "engine_version": engine[1].decode("ascii"),
        "path": _absolute_search_path(image_path, _DOCKER_PATH),
    }


def _relative(name):
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or any(p in ("", ".", "..") for p in name.split("/"))
        or "\\" in name
    ):
        raise ValueError("invalid staged path")
    return path


def _stage(root, files, *, trusted=False):
    entries, links = _validate_staged(files, trusted=trusted)
    root.mkdir(mode=0o755)
    for name, (mode, data) in entries.items():
        if mode == 0o120000:
            continue
        relative = _relative(name)
        path = root.joinpath(*relative.parts)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(mode if not trusted else 0o555)
    for name, target in links.items():
        path = root.joinpath(*_relative(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        path.symlink_to(target)
    if trusted:
        for path in root.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)


def _stage_archive(archive, candidate_files, trusted_files):
    candidate_entries, links = _validate_staged(candidate_files)
    trusted_entries, _ = _validate_staged(trusted_files, trusted=True)
    with tarfile.open(fileobj=archive, mode="w") as tar:
        directories = set()

        def directory(name, mode):
            if name not in directories:
                item = tarfile.TarInfo(name)
                item.type = tarfile.DIRTYPE
                item.mode = mode
                item.uid = item.gid = 0
                tar.addfile(item)
                directories.add(name)

        directory("mentat", 0o755)
        directory("mentat/candidate", 0o777)
        directory("mentat/checks", 0o755)
        directory("mentat/artifacts", 0o777)
        for prefix, entries, trusted in (
            ("candidate", candidate_entries, False),
            ("checks", trusted_entries, True),
        ):
            for name, entry in entries.items():
                relative = _relative(name)
                mode, data = entry
                base = "mentat/" + prefix
                for part in relative.parts[:-1]:
                    base += "/" + part
                    directory(base, 0o755 if trusted else 0o777)
                item = tarfile.TarInfo(base + "/" + relative.name)
                item.mode = 0o555 if trusted else mode | 0o222
                item.uid = item.gid = 0
                if mode == 0o120000:
                    item.type = tarfile.SYMTYPE
                    item.linkname = links[name]
                    item.mode = 0o777
                    tar.addfile(item)
                else:
                    item.size = len(data)
                    tar.addfile(item, io.BytesIO(data))
    archive.seek(0)


def _artifact(path):
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory,
        )
    finally:
        os.close(directory)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("artifact is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(_MAX_ARTIFACT + 1)
        if len(data) > _MAX_ARTIFACT:
            raise ValueError("artifact exceeds limit")
        return data
    finally:
        os.close(fd)


def _docker_artifact(context, name, artifact):
    code, archive, _ = _docker(
        context,
        "cp",
        f"{name}:/mentat/artifacts/{artifact}",
        "-",
        output_limit=_MAX_ARTIFACT + 128 * 1024,
    )
    if code:
        return None
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        members = tar.getmembers()
        if (
            len(members) != 1
            or members[0].name != artifact
            or not members[0].isfile()
            or members[0].size > _MAX_ARTIFACT
        ):
            raise ValueError("invalid artifact archive")
        return tar.extractfile(members[0]).read(_MAX_ARTIFACT + 1)


def run_behavior(
    project, snapshot, context, info, run_id, *, output_budget=None
) -> tuple[list[dict], dict[str, bytes]]:
    if output_budget is not None and (
        type(output_budget) is not int or not 0 <= output_budget <= _MAX_RUN_OUTPUT
    ):
        raise ValueError("Invalid remaining run output budget")
    names = (
        dict.fromkeys(
            name for check in project.contract.behavior for name in check.host_environment
        )
        if project.runner == "host"
        else {}
    )
    inherited = {name: os.environ.get(name) for name in names}
    results, files = [], {}
    remaining = _MAX_RUN_OUTPUT if output_budget is None else output_budget
    if project.runner == "docker":
        context = info["context"]
    elif "path" not in info:
        info = runner_info(project, context)
    prerequisites = info.get("prerequisites", {})
    if inherited:
        prerequisites = info.setdefault("prerequisites", {})
        prerequisites["host_environment"] = {
            name: {"status": "available" if value else "missing"}
            for name, value in inherited.items()
        }
    missing = [
        name
        for group in ("executables", "python_distributions")
        for name, item in prerequisites.get(group, {}).items()
        if item["status"] != "available"
    ]
    if prerequisites.get("loopback") and prerequisites["loopback"]["status"] != "available":
        missing.append("loopback")

    def phase(check, result, prefix, args):
        nonlocal remaining
        name = "mentat-" + secrets.token_hex(16)
        created = False
        try:
            with tempfile.TemporaryDirectory(prefix="mentat-run-") as temp:
                root = Path(temp)
                candidate, checks, artifacts = (
                    root / p for p in ("candidate", "checks", "artifacts")
                )
                if project.runner == "host":
                    _stage(candidate, snapshot.files)
                    _stage(checks, project.files, trusted=True)
                    artifacts.mkdir(mode=0o777)
                    artifacts.chmod(0o777)
                    home = root / "home"
                    home.mkdir()
                    env = {
                        "PATH": info["path"],
                        "HOME": str(home),
                        "MENTAT_CANDIDATE_DIR": str(candidate),
                        "MENTAT_CHECKS_DIR": str(checks),
                        "MENTAT_ARTIFACTS_DIR": str(artifacts),
                        **{name: inherited[name] for name in check.host_environment},
                    }
                    argv = _arguments([*check.effective_argv(), *args], str(checks))
                    code, stdout, stderr = _process(
                        argv, cwd=candidate, env=env, timeout=check.timeout_seconds
                    )
                    oom = False
                else:
                    argv = _arguments([*check.effective_argv(), *args], "/mentat/checks")
                    command = [
                        "create",
                        "--name",
                        name,
                        "--label",
                        f"org.hypertrial.mentat.run={run_id}",
                        "--network",
                        "none",
                        "--pull",
                        "never",
                        "--no-healthcheck",
                        "--log-driver",
                        "none",
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
                        "--user",
                        "65534:65534",
                        "--workdir",
                        "/mentat/candidate",
                        "--env",
                        "HOME=/tmp",
                        "--env",
                        f"PATH={info['path']}",
                        "--env",
                        "MENTAT_CANDIDATE_DIR=/mentat/candidate",
                        "--env",
                        "MENTAT_CHECKS_DIR=/mentat/checks",
                        "--env",
                        "MENTAT_ARTIFACTS_DIR=/mentat/artifacts",
                        "--entrypoint",
                        argv[0],
                        info["image_id"],
                        *argv[1:],
                    ]
                    # Track the name even if the CLI is interrupted after Docker creates it.
                    created = True
                    create_code, _, diagnostic = _docker(context, *command)
                    if create_code:
                        raise RuntimeError(
                            "container creation failed: "
                            + diagnostic.decode(errors="replace")[:2048]
                        )
                    with tempfile.TemporaryFile() as archive:
                        _stage_archive(archive, snapshot.files, project.files)
                        copy_code, _, diagnostic = _docker(
                            context, "cp", "-", f"{name}:/", input_file=archive
                        )
                        if copy_code:
                            raise RuntimeError(
                                "container staging failed: "
                                + diagnostic.decode(errors="replace")[:2048]
                            )
                    code, stdout, stderr = _docker(
                        context, "start", "-a", name, timeout=check.timeout_seconds
                    )
                    inspect_code, state, _ = _docker(
                        context, "inspect", "--format", "{{json .State}}", name
                    )
                    state = json.loads(state) if not inspect_code else {}
                    oom = (
                        state.get("OOMKilled")
                        or state.get("Error")
                        or state.get("Status") != "exited"
                        or state.get("ExitCode") != code
                    )
                result["exit_code"] = code
                if oom or code < 0 or (project.runner == "docker" and code in (125, 126, 127, 137)):
                    result["detail"] = "Check terminated abnormally"
                elif code == 0:
                    result.update(status="pass", detail="Check passed")
                elif code in check.violation_exit_codes:
                    result.update(status="violation", detail="Check reported a violation")
                else:
                    result["detail"] = "Check exited unexpectedly"
                if check.case_report or check.negative_control:
                    result["command_status"] = result["status"]

                def collect_artifact(number, artifact):
                    nonlocal remaining
                    try:
                        if remaining == 0:
                            raise ValueError("run output limit reached")
                        data = (
                            _artifact(artifacts / artifact)
                            if project.runner == "host"
                            else _docker_artifact(context, name, artifact)
                        )
                        if data is None or len(data) > remaining:
                            raise ValueError("artifact missing or exceeds run output limit")
                        key = f"{prefix}.artifact-{number:03d}.{artifact}"
                        files[key] = data
                        result.setdefault("artifacts", {})[artifact] = key
                        remaining -= len(data)
                        return data
                    except (OSError, ValueError, RuntimeError, tarfile.TarError):
                        result.setdefault("artifact_omissions", []).append(artifact)
                        return None

                report_data = None
                if check.case_report:
                    number = check.artifacts.index(check.case_report.artifact) + 1
                    report_data = collect_artifact(number, check.case_report.artifact)
                if check.case_report:
                    try:
                        report = decode_json(report_data) if report_data is not None else None
                        if isinstance(report, dict) and isinstance(report.get("cases"), dict):
                            result["case_results"] = {
                                case: value
                                for case, value in report["cases"].items()
                                if case in check.case_report.required_cases
                                and isinstance(value, str)
                                and value in ("pass", "fail", "skip", "error")
                            }
                        if (
                            not isinstance(report, dict)
                            or set(report) != {"schema_version", "cases"}
                            or type(report["schema_version"]) is not int
                            or report["schema_version"] != 1
                            or not isinstance(report["cases"], dict)
                            or set(report["cases"]) != set(check.case_report.required_cases)
                            or any(
                                value not in ("pass", "fail", "skip", "error")
                                for value in report["cases"].values()
                            )
                        ):
                            raise ValueError("invalid case report")
                        result["case_results"] = report["cases"]
                        outcomes = set(report["cases"].values())
                        if (
                            outcomes & {"skip", "error"}
                            or (result["status"] == "pass" and "fail" in outcomes)
                            or (result["status"] == "violation" and "fail" not in outcomes)
                        ):
                            result.update(
                                status="incomplete",
                                detail="Case report is inconclusive or contradicts exit code",
                            )
                    except (ValueError, TypeError, UnicodeError):
                        result.update(status="incomplete", detail="Missing or invalid case report")
                for suffix, data in (("stdout", stdout), ("stderr", stderr)):
                    if data:
                        if len(data) > remaining:
                            result.setdefault("log_omissions", []).append(suffix)
                            continue
                        key = f"{prefix}.{suffix}.log"
                        files[key] = data
                        result[suffix] = key
                        remaining -= len(data)
                for number, artifact in enumerate(check.artifacts, start=1):
                    if not check.case_report or artifact != check.case_report.artifact:
                        collect_artifact(number, artifact)

        except (
            OSError,
            ValueError,
            RuntimeError,
            TimeoutError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
            tarfile.TarError,
        ) as exc:
            result.update(status="incomplete", detail=str(exc) or "Check could not complete")
        finally:
            if created:
                try:
                    cleanup, _, diagnostic = _docker(context, "rm", "-f", "-v", name, timeout=20)
                    cleanup_failed = bool(cleanup and b"No such container" not in diagnostic)
                except (RuntimeError, ValueError):
                    cleanup_failed = True
                if cleanup_failed:
                    result.update(
                        status="incomplete",
                        detail=f"{result['detail']}. Container cleanup failed ({name})",
                    )

    for index, check in enumerate(project.contract.behavior, start=1):
        prefix = f"check-{index:03d}"
        result = {
            "id": check.id,
            "claim": check.claim,
            "status": "incomplete",
            "detail": "Check did not complete",
            "exit_code": None,
        }
        if check.case_report or check.negative_control:
            result["command_status"] = "incomplete"
        results.append(result)
        if check.case_report:
            result["required_cases"] = check.case_report.required_cases
        if project.runner == "host":
            if missing:
                result["detail"] = (
                    f"Missing host prerequisites: {', '.join(missing)}. Run mentat doctor for details."
                )
                continue
            missing_environment = [name for name in check.host_environment if not inherited[name]]
            if missing_environment:
                result["detail"] = (
                    "Missing host environment variables: " + ", ".join(missing_environment) + "."
                )
                continue
            command = check.effective_argv()[0]
            executable = info["executables"].get(command)
            if executable and executable["status"] == "missing":
                result["detail"] = f"Host executable not found: {command!r}"
                continue
        phase(check, result, prefix, [])
        if check.negative_control and result["status"] == "pass":
            control = {
                "status": "incomplete",
                "detail": "Check did not complete",
                "exit_code": None,
            }
            control["command_status"] = "incomplete"
            if check.case_report:
                control["required_cases"] = check.case_report.required_cases
            result["negative_control"] = control
            phase(check, control, prefix + ".negative-control", check.negative_control.args)
            expected = (
                control["status"] == "violation"
                and control["exit_code"] == check.negative_control.violation_exit_code
            )
            if check.case_report and expected:
                expected = all(
                    value == ("fail" if case == check.negative_control.failing_case else "pass")
                    for case, value in control["case_results"].items()
                )
            control["qualified"] = expected
            if not expected:
                result.update(
                    status="incomplete",
                    detail="Negative control did not demonstrate the expected violation",
                )
    return results, files
