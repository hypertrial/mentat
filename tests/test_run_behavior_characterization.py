"""Pins run_behavior results, artifacts and Docker operations for fixed inputs.

Every expectation was recorded from the runner before its split into per-runner phases; the
phases must keep them byte-identical. Container names are random, so they are replaced by
"<container-N>" (N counts creations) before comparison.

Host scenarios run sys.executable, but no interpreter path, platform or Docker runtime value is
pinned. The only timing dependence is the two timeout-style scenarios, which use a one-second
limit against a three-second sleep.
"""

import io
import json
import os
import sys
import tarfile
from types import SimpleNamespace

import pytest

from mentat import runner
from mentat.project import Behavior

CASES = {"schema_version": 1, "cases": {"first": "pass", "second": "pass"}}
REPORT = {"artifact": "cases.json", "required_cases": ["first", "second"]}
PATH = os.environ.get("PATH", "")
SNAPSHOT = SimpleNamespace(files={"original.txt": (0o644, b"original")})
DOCKER_INFO = {"context": "test", "path": "/bin", "image_id": "image"}


def _check(identifier, script_name="check.py", **overrides):
    values = {
        "id": identifier,
        "claim": "claim",
        "argv": [sys.executable, "{checks}/" + script_name],
        **overrides,
    }
    return Behavior.model_validate(values)


def _report(**cases):
    return json.dumps({"schema_version": 1, "cases": cases})


def _host_run(behaviors, files, *, budget=None, info=None):
    project = SimpleNamespace(
        runner="host", contract=SimpleNamespace(behavior=behaviors), files=files
    )
    info = info or {"path": PATH, "executables": {}}
    results, produced = runner.run_behavior(
        project, SNAPSHOT, None, info, "run", output_budget=budget
    )
    return {"results": results, "files": produced}


def _tar(name, data):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        item = tarfile.TarInfo(name)
        item.size = len(data)
        tar.addfile(item, io.BytesIO(data))
    return buffer.getvalue()


def _scrub(value, names):
    if isinstance(value, str):
        for index, name in enumerate(names, start=1):
            value = value.replace(name, f"<container-{index}>")
        return value
    if isinstance(value, dict):
        return {key: _scrub(item, names) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub(item, names) for item in value)
    return value


class Docker:
    """Scripted runner._docker that records every operation, in order, with its options.

    `responses` maps an operation (create, cp-in, start, inspect, rm, cp-out) to a
    (code, stdout, stderr) tuple, or to a callable receiving this stub. `artifacts` maps an
    artifact name to bytes, or is a callable receiving (stub, name).
    """

    def __init__(self, monkeypatch, responses=None, artifacts=None):
        self.calls = []
        self.names = []
        self.exit_code = 0
        self.responses = responses or {}
        self.artifacts = artifacts or {}
        monkeypatch.setattr(runner, "_docker", self)

    @property
    def control(self):
        create = [args for kind, args, _ in self.calls if kind == "create"][-1]
        return "--known-bad" in create

    def __call__(self, context, *args, timeout=30, input_file=None, output_limit=None):
        kind = args[0]
        if kind == "cp":
            kind = "cp-in" if args[1] == "-" else "cp-out"
        options = {"timeout": timeout}
        if input_file is not None:
            archive = input_file.read()
            input_file.seek(0)
            members = tarfile.open(fileobj=io.BytesIO(archive)).getmembers()
            options["members"] = [member.name for member in members]
        if output_limit is not None:
            options["output_limit"] = output_limit
        if kind == "create":
            self.names.append(args[args.index("--name") + 1])
        self.calls.append((kind, args, options))
        if kind == "cp-out":
            return self._artifact(args[1].split("/")[-1])
        if kind == "inspect" and "inspect" not in self.responses:
            state = {"Status": "exited", "ExitCode": self.exit_code}
            return 0, json.dumps(state).encode(), b""
        response = self.responses.get(kind, (0, b"", b""))
        result = response(self) if callable(response) else response
        if kind == "start":
            self.exit_code = result[0]
        return result

    def _artifact(self, name):
        data = self.artifacts(self, name) if callable(self.artifacts) else self.artifacts.get(name)
        if data is None:
            return 1, b"", b"no such file"
        return 0, _tar(name, data), b""

    def scrubbed(self):
        calls = []
        for kind, args, options in self.calls:
            if kind == "create":
                # The full option list is pinned once in test_docker_create_options_are_pinned.
                start, end = args.index("--network"), args.index("--entrypoint")
                args = (*args[:start], "<create options>", *args[end:])
            calls.append((kind, args, options))
        return _scrub(calls, self.names)


def _docker_run(monkeypatch, behaviors, files, *, responses=None, artifacts=None, budget=None):
    stub = Docker(monkeypatch, responses, artifacts)
    project = SimpleNamespace(
        runner="docker", contract=SimpleNamespace(behavior=behaviors), files=files
    )
    results, produced = runner.run_behavior(
        project, SNAPSHOT, None, dict(DOCKER_INFO), "run", output_budget=budget
    )
    return {
        "results": _scrub(results, stub.names),
        "files": produced,
        "calls": stub.scrubbed(),
    }


OK_SCRIPT = (
    "import os, sys\n"
    "from pathlib import Path\n"
    "out = Path(os.environ['MENTAT_ARTIFACTS_DIR'])\n"
    "print('out text')\n"
    "print('err text', file=sys.stderr)\n"
    f"(out / 'cases.json').write_text({json.dumps(CASES)!r})\n"
    "(out / 'extra.txt').write_text('extra')\n"
)


def _host_scenario_success(monkeypatch):
    check = _check("ok", "ok.py", artifacts=["cases.json", "extra.txt"], case_report=REPORT)
    return _host_run([check], {"ok.py": OK_SCRIPT.encode()})


def _host_scenario_violation(monkeypatch):
    script = (
        "import os, sys\nfrom pathlib import Path\n"
        f"Path(os.environ['MENTAT_ARTIFACTS_DIR'], 'cases.json').write_text({_report(first='fail', second='pass')!r})\n"
        "sys.exit(10)\n"
    )
    check = _check(
        "bad", "bad.py", artifacts=["cases.json"], case_report=REPORT, violation_exit_codes=[10]
    )
    return _host_run([check], {"bad.py": script.encode()})


def _host_scenario_unexpected_exit(monkeypatch):
    check = _check("exit", "exit.py")
    return _host_run([check], {"exit.py": b"import sys\nprint('partial')\nsys.exit(3)\n"})


def _host_scenario_timeout(monkeypatch):
    check = _check("slow", "slow.py", timeout_seconds=1)
    return _host_run([check], {"slow.py": b"import time\ntime.sleep(3)\n"})


def _host_scenario_output_cap(monkeypatch):
    script = b"import sys\nsys.stdout.write('x' * (1024 * 1024 + 1))\n"
    return _host_run([_check("loud", "loud.py")], {"loud.py": script})


def _host_scenario_budget_across_checks(monkeypatch):
    first = (
        "import os\nfrom pathlib import Path\n"
        "print('a' * 40)\n"
        "Path(os.environ['MENTAT_ARTIFACTS_DIR'], 'a.txt').write_bytes(b'a' * 30)\n"
    )
    second = (
        "import os\nfrom pathlib import Path\n"
        "print('b' * 40)\n"
        "Path(os.environ['MENTAT_ARTIFACTS_DIR'], 'b.txt').write_bytes(b'b' * 31)\n"
    )
    third = "print('c')\n"
    behaviors = [
        _check("first", "first.py", artifacts=["a.txt"]),
        _check("second", "second.py", artifacts=["b.txt"]),
        _check("third", "third.py"),
    ]
    files = {"first.py": first.encode(), "second.py": second.encode(), "third.py": third.encode()}
    return _host_run(behaviors, files, budget=100)


def _host_scenario_zero_budget(monkeypatch):
    script = (
        "import os\nfrom pathlib import Path\n"
        "print('x')\n"
        "Path(os.environ['MENTAT_ARTIFACTS_DIR'], 'a.txt').write_text('a')\n"
    )
    check = _check("zero", "zero.py", artifacts=["a.txt"])
    return _host_run([check], {"zero.py": script.encode()}, budget=0)


def _host_scenario_negative_control(monkeypatch):
    script = """import json, os, sys
from pathlib import Path
marker = Path('marker')
assert not marker.exists()
assert Path('original.txt').read_text() == 'original'
marker.write_text('changed')
control = sys.argv[1:] == ['--known-bad']
cases = {'first': 'fail' if control else 'pass', 'second': 'pass'}
Path(os.environ['MENTAT_ARTIFACTS_DIR'], 'cases.json').write_text(json.dumps({'schema_version': 1, 'cases': cases}))
sys.exit(10 if control else 0)
"""
    check = _check(
        "control",
        "control.py",
        artifacts=["cases.json"],
        case_report=REPORT,
        violation_exit_codes=[10],
        negative_control={
            "args": ["--known-bad"],
            "violation_exit_code": 10,
            "failing_case": "first",
        },
    )
    return _host_run([check], {"control.py": script.encode()})


def _host_scenario_artifact_omissions(monkeypatch):
    script = (
        "import os\nfrom pathlib import Path\n"
        "(Path(os.environ['MENTAT_ARTIFACTS_DIR']) / 'dir.txt').mkdir()\n"
    )
    check = _check("omit", "omit.py", artifacts=["missing.txt", "dir.txt"])
    return _host_run([check], {"omit.py": script.encode()})


def _host_scenario_invalid_case_report(monkeypatch):
    script = (
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['MENTAT_ARTIFACTS_DIR'], 'cases.json').write_text('not json')\n"
    )
    check = _check("invalid", "invalid.py", artifacts=["cases.json"], case_report=REPORT)
    return _host_run([check], {"invalid.py": script.encode()})


def _host_scenario_missing_executable(monkeypatch):
    check = _check("tool", "tool.py", argv=["mentat-missing-tool", "{checks}/tool.py"])
    info = {
        "path": PATH,
        "executables": {"mentat-missing-tool": {"status": "missing", "path": None}},
    }
    return _host_run([check], {"tool.py": b"pass\n"}, info=info)


def _host_scenario_missing_prerequisite(monkeypatch):
    check = _check("prereq", "prereq.py")
    info = {
        "path": PATH,
        "executables": {},
        "prerequisites": {"executables": {"tool": {"status": "missing"}}},
    }
    return _host_run([check], {"prereq.py": b"pass\n"}, info=info)


def _host_scenario_missing_environment(monkeypatch):
    monkeypatch.delenv("PINNED_ABSENT_VARIABLE", raising=False)
    check = _check("env", "env.py", host_environment=["PINNED_ABSENT_VARIABLE"])
    return _host_run([check], {"env.py": b"pass\n"})


def _docker_scenario_success(monkeypatch):
    check = _check(
        "ok",
        "ok.py",
        argv=["python3", "{checks}/ok.py"],
        artifacts=["cases.json", "extra.txt"],
        case_report=REPORT,
    )
    return _docker_run(
        monkeypatch,
        [check],
        {"ok.py": OK_SCRIPT.encode()},
        responses={"start": (0, b"out text\n", b"err text\n")},
        artifacts={"cases.json": json.dumps(CASES).encode(), "extra.txt": b"extra"},
    )


def _docker_check(**overrides):
    return _check("docker", "check.py", argv=["python3", "{checks}/check.py"], **overrides)


def _docker_scenario_create_fails(monkeypatch):
    return _docker_run(
        monkeypatch,
        [_docker_check()],
        {"check.py": b"pass\n"},
        responses={"create": (1, b"", b"boom")},
    )


def _docker_scenario_create_fails_without_container(monkeypatch):
    return _docker_run(
        monkeypatch,
        [_docker_check()],
        {"check.py": b"pass\n"},
        responses={
            "create": (1, b"", b"create refused"),
            "rm": (1, b"", b"No such container: gone"),
        },
    )


def _docker_scenario_staging_fails(monkeypatch):
    return _docker_run(
        monkeypatch,
        [_docker_check()],
        {"check.py": b"pass\n"},
        responses={"cp-in": (1, b"", b"denied")},
    )


def _docker_scenario_cleanup_fails(monkeypatch):
    return _docker_run(
        monkeypatch,
        [_docker_check()],
        {"check.py": b"pass\n"},
        responses={"rm": (1, b"", b"busy")},
    )


def _docker_scenario_cleanup_raises(monkeypatch):
    def rm(stub):
        raise ValueError("Docker command failed: no daemon")

    return _docker_run(
        monkeypatch, [_docker_check()], {"check.py": b"pass\n"}, responses={"rm": rm}
    )


def _docker_scenario_start_raises(monkeypatch):
    def start(stub):
        raise ValueError("Docker command failed: timed out")

    return _docker_run(
        monkeypatch, [_docker_check()], {"check.py": b"pass\n"}, responses={"start": start}
    )


def _docker_scenario_exit_125(monkeypatch):
    return _docker_run(
        monkeypatch,
        [_docker_check()],
        {"check.py": b"pass\n"},
        responses={"start": (125, b"", b"runtime error")},
    )


def _docker_scenario_oom(monkeypatch):
    state = {"Status": "exited", "ExitCode": 0, "OOMKilled": True}
    return _docker_run(
        monkeypatch,
        [_docker_check()],
        {"check.py": b"pass\n"},
        responses={"inspect": (0, json.dumps(state).encode(), b"")},
    )


def _docker_scenario_inspect_fails(monkeypatch):
    return _docker_run(
        monkeypatch,
        [_docker_check()],
        {"check.py": b"pass\n"},
        responses={"inspect": (1, b"", b"no such container")},
    )


def _docker_scenario_inspect_invalid_json(monkeypatch):
    return _docker_run(
        monkeypatch,
        [_docker_check()],
        {"check.py": b"pass\n"},
        responses={"inspect": (0, b"{", b"")},
    )


def _docker_scenario_zero_budget(monkeypatch):
    check = _docker_check(artifacts=["cases.json", "extra.txt"], case_report=REPORT)
    return _docker_run(
        monkeypatch,
        [check],
        {"check.py": b"pass\n"},
        responses={"start": (0, b"out", b"err")},
        artifacts={"cases.json": json.dumps(CASES).encode(), "extra.txt": b"extra"},
        budget=0,
    )


def _docker_scenario_budget_across_checks(monkeypatch):
    behaviors = [
        _check("first", "first.py", argv=["python3", "{checks}/first.py"], artifacts=["a.txt"]),
        _check("second", "second.py", argv=["python3", "{checks}/second.py"], artifacts=["b.txt"]),
    ]
    return _docker_run(
        monkeypatch,
        behaviors,
        {"first.py": b"pass\n", "second.py": b"pass\n"},
        responses={"start": (0, b"o" * 40, b"")},
        artifacts={"a.txt": b"a" * 30, "b.txt": b"b" * 31},
        budget=100,
    )


def _docker_scenario_negative_control(monkeypatch):
    check = _docker_check(
        artifacts=["cases.json"],
        case_report=REPORT,
        violation_exit_codes=[10],
        negative_control={
            "args": ["--known-bad"],
            "violation_exit_code": 10,
            "failing_case": "first",
        },
    )

    def start(stub):
        return (10, b"", b"") if stub.control else (0, b"", b"")

    def artifact(stub, name):
        first = "fail" if stub.control else "pass"
        return _report(first=first, second="pass").encode()

    return _docker_run(
        monkeypatch,
        [check],
        {"check.py": b"pass\n"},
        responses={"start": start},
        artifacts=artifact,
    )


SCENARIOS = {
    "host-success": _host_scenario_success,
    "host-violation": _host_scenario_violation,
    "host-unexpected-exit": _host_scenario_unexpected_exit,
    "host-timeout": _host_scenario_timeout,
    "host-output-cap": _host_scenario_output_cap,
    "host-budget-across-checks": _host_scenario_budget_across_checks,
    "host-zero-budget": _host_scenario_zero_budget,
    "host-negative-control": _host_scenario_negative_control,
    "host-artifact-omissions": _host_scenario_artifact_omissions,
    "host-invalid-case-report": _host_scenario_invalid_case_report,
    "host-missing-executable": _host_scenario_missing_executable,
    "host-missing-prerequisite": _host_scenario_missing_prerequisite,
    "host-missing-environment": _host_scenario_missing_environment,
    "docker-success": _docker_scenario_success,
    "docker-create-fails": _docker_scenario_create_fails,
    "docker-create-fails-without-container": _docker_scenario_create_fails_without_container,
    "docker-staging-fails": _docker_scenario_staging_fails,
    "docker-cleanup-fails": _docker_scenario_cleanup_fails,
    "docker-cleanup-raises": _docker_scenario_cleanup_raises,
    "docker-start-raises": _docker_scenario_start_raises,
    "docker-exit-125": _docker_scenario_exit_125,
    "docker-oom": _docker_scenario_oom,
    "docker-inspect-fails": _docker_scenario_inspect_fails,
    "docker-inspect-invalid-json": _docker_scenario_inspect_invalid_json,
    "docker-zero-budget": _docker_scenario_zero_budget,
    "docker-budget-across-checks": _docker_scenario_budget_across_checks,
    "docker-negative-control": _docker_scenario_negative_control,
}

PINNED = {
    "docker-budget-across-checks": {
        "results": [
            {
                "id": "first",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "stdout": "check-001.stdout.log",
                "artifacts": {"a.txt": "check-001.artifact-001.a.txt"},
            },
            {
                "id": "second",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "log_omissions": ["stdout"],
                "artifact_omissions": ["b.txt"],
            },
        ],
        "files": {
            "check-001.stdout.log": b"oooooooooooooooooooooooooooooooooooooooo",
            "check-001.artifact-001.a.txt": b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        },
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/first.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/first.py",
                        "mentat/checks/second.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-1>"),
                {"timeout": 30},
            ),
            (
                "cp-out",
                ("cp", "<container-1>:/mentat/artifacts/a.txt", "-"),
                {"timeout": 30, "output_limit": 1179648},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-2>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/second.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-2>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/first.py",
                        "mentat/checks/second.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-2>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-2>"),
                {"timeout": 30},
            ),
            (
                "cp-out",
                ("cp", "<container-2>:/mentat/artifacts/b.txt", "-"),
                {"timeout": 30, "output_limit": 1179648},
            ),
            ("rm", ("rm", "-f", "-v", "<container-2>"), {"timeout": 20}),
        ],
    },
    "docker-cleanup-fails": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Check passed. Container cleanup failed (<container-1>)",
                "exit_code": 0,
            }
        ],
        "files": {},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-1>"),
                {"timeout": 30},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-cleanup-raises": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Check passed. Container cleanup failed (<container-1>)",
                "exit_code": 0,
            }
        ],
        "files": {},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-1>"),
                {"timeout": 30},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-create-fails": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "container creation failed: boom",
                "exit_code": None,
            }
        ],
        "files": {},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-create-fails-without-container": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "container creation failed: create refused",
                "exit_code": None,
            }
        ],
        "files": {},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-exit-125": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Check terminated abnormally",
                "exit_code": 125,
                "stderr": "check-001.stderr.log",
            }
        ],
        "files": {"check-001.stderr.log": b"runtime error"},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-1>"),
                {"timeout": 30},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-inspect-fails": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Check terminated abnormally",
                "exit_code": 0,
            }
        ],
        "files": {},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-1>"),
                {"timeout": 30},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-inspect-invalid-json": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Expecting property name enclosed in double quotes: line 1 column 2 (char "
                "1)",
                "exit_code": None,
            }
        ],
        "files": {},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-1>"),
                {"timeout": 30},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-negative-control": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "command_status": "pass",
                "required_cases": ["first", "second"],
                "artifacts": {"cases.json": "check-001.artifact-001.cases.json"},
                "case_results": {"first": "pass", "second": "pass"},
                "negative_control": {
                    "status": "violation",
                    "detail": "Check reported a violation",
                    "exit_code": 10,
                    "command_status": "violation",
                    "required_cases": ["first", "second"],
                    "artifacts": {
                        "cases.json": "check-001.negative-control.artifact-001.cases.json"
                    },
                    "case_results": {"first": "fail", "second": "pass"},
                    "qualified": True,
                },
            }
        ],
        "files": {
            "check-001.artifact-001.cases.json": b'{"schema_version": 1, "cases": {"first": "pass",'
            b' "second": "pass"}}',
            "check-001.negative-control.artifact-001.cases.json": b'{"schema_version": 1, "cases": {'
            b'"first": "fail", "second": "pass'
            b'"}}',
        },
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-1>"),
                {"timeout": 30},
            ),
            (
                "cp-out",
                ("cp", "<container-1>:/mentat/artifacts/cases.json", "-"),
                {"timeout": 30, "output_limit": 1179648},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-2>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                    "--known-bad",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-2>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-2>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-2>"),
                {"timeout": 30},
            ),
            (
                "cp-out",
                ("cp", "<container-2>:/mentat/artifacts/cases.json", "-"),
                {"timeout": 30, "output_limit": 1179648},
            ),
            ("rm", ("rm", "-f", "-v", "<container-2>"), {"timeout": 20}),
        ],
    },
    "docker-oom": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Check terminated abnormally",
                "exit_code": 0,
            }
        ],
        "files": {},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-1>"),
                {"timeout": 30},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-staging-fails": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "container staging failed: denied",
                "exit_code": None,
            }
        ],
        "files": {},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-start-raises": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Docker command failed: timed out",
                "exit_code": None,
            }
        ],
        "files": {},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-success": {
        "results": [
            {
                "id": "ok",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "command_status": "pass",
                "required_cases": ["first", "second"],
                "artifacts": {
                    "cases.json": "check-001.artifact-001.cases.json",
                    "extra.txt": "check-001.artifact-002.extra.txt",
                },
                "case_results": {"first": "pass", "second": "pass"},
                "stdout": "check-001.stdout.log",
                "stderr": "check-001.stderr.log",
            }
        ],
        "files": {
            "check-001.artifact-001.cases.json": b'{"schema_version": 1, "cases": {"first": "pass",'
            b' "second": "pass"}}',
            "check-001.stdout.log": b"out text\n",
            "check-001.stderr.log": b"err text\n",
            "check-001.artifact-002.extra.txt": b"extra",
        },
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/ok.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/ok.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-1>"),
                {"timeout": 30},
            ),
            (
                "cp-out",
                ("cp", "<container-1>:/mentat/artifacts/cases.json", "-"),
                {"timeout": 30, "output_limit": 1179648},
            ),
            (
                "cp-out",
                ("cp", "<container-1>:/mentat/artifacts/extra.txt", "-"),
                {"timeout": 30, "output_limit": 1179648},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "docker-zero-budget": {
        "results": [
            {
                "id": "docker",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Missing or invalid case report",
                "exit_code": 0,
                "command_status": "pass",
                "required_cases": ["first", "second"],
                "artifact_omissions": ["cases.json", "extra.txt"],
                "log_omissions": ["stdout", "stderr"],
            }
        ],
        "files": {},
        "calls": [
            (
                "create",
                (
                    "create",
                    "--name",
                    "<container-1>",
                    "--label",
                    "org.hypertrial.mentat.run=run",
                    "<create options>",
                    "--entrypoint",
                    "python3",
                    "image",
                    "/mentat/checks/check.py",
                ),
                {"timeout": 30},
            ),
            (
                "cp-in",
                ("cp", "-", "<container-1>:/"),
                {
                    "timeout": 30,
                    "members": [
                        "mentat",
                        "mentat/candidate",
                        "mentat/checks",
                        "mentat/artifacts",
                        "mentat/candidate/original.txt",
                        "mentat/checks/check.py",
                    ],
                },
            ),
            ("start", ("start", "-a", "<container-1>"), {"timeout": 60}),
            (
                "inspect",
                ("inspect", "--format", "{{json .State}}", "<container-1>"),
                {"timeout": 30},
            ),
            ("rm", ("rm", "-f", "-v", "<container-1>"), {"timeout": 20}),
        ],
    },
    "host-artifact-omissions": {
        "results": [
            {
                "id": "omit",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "artifact_omissions": ["missing.txt", "dir.txt"],
            }
        ],
        "files": {},
    },
    "host-budget-across-checks": {
        "results": [
            {
                "id": "first",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "stdout": "check-001.stdout.log",
                "artifacts": {"a.txt": "check-001.artifact-001.a.txt"},
            },
            {
                "id": "second",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "log_omissions": ["stdout"],
                "artifact_omissions": ["b.txt"],
            },
            {
                "id": "third",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "stdout": "check-003.stdout.log",
            },
        ],
        "files": {
            "check-001.stdout.log": b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
            "check-001.artifact-001.a.txt": b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "check-003.stdout.log": b"c\n",
        },
    },
    "host-invalid-case-report": {
        "results": [
            {
                "id": "invalid",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Missing or invalid case report",
                "exit_code": 0,
                "command_status": "pass",
                "required_cases": ["first", "second"],
                "artifacts": {"cases.json": "check-001.artifact-001.cases.json"},
            }
        ],
        "files": {"check-001.artifact-001.cases.json": b"not json"},
    },
    "host-missing-environment": {
        "results": [
            {
                "id": "env",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Missing host environment variables: PINNED_ABSENT_VARIABLE.",
                "exit_code": None,
            }
        ],
        "files": {},
    },
    "host-missing-executable": {
        "results": [
            {
                "id": "tool",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Host executable not found: 'mentat-missing-tool'",
                "exit_code": None,
            }
        ],
        "files": {},
    },
    "host-missing-prerequisite": {
        "results": [
            {
                "id": "prereq",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Missing host prerequisites: tool. Run mentat doctor for details.",
                "exit_code": None,
            }
        ],
        "files": {},
    },
    "host-negative-control": {
        "results": [
            {
                "id": "control",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "command_status": "pass",
                "required_cases": ["first", "second"],
                "artifacts": {"cases.json": "check-001.artifact-001.cases.json"},
                "case_results": {"first": "pass", "second": "pass"},
                "negative_control": {
                    "status": "violation",
                    "detail": "Check reported a violation",
                    "exit_code": 10,
                    "command_status": "violation",
                    "required_cases": ["first", "second"],
                    "artifacts": {
                        "cases.json": "check-001.negative-control.artifact-001.cases.json"
                    },
                    "case_results": {"first": "fail", "second": "pass"},
                    "qualified": True,
                },
            }
        ],
        "files": {
            "check-001.artifact-001.cases.json": b'{"schema_version": 1, "cases": {"first": "pass",'
            b' "second": "pass"}}',
            "check-001.negative-control.artifact-001.cases.json": b'{"schema_version": 1, "cases": {'
            b'"first": "fail", "second": "pass'
            b'"}}',
        },
    },
    "host-output-cap": {
        "results": [
            {
                "id": "loud",
                "claim": "claim",
                "status": "incomplete",
                "detail": "execution output exceeded limit",
                "exit_code": None,
            }
        ],
        "files": {},
    },
    "host-success": {
        "results": [
            {
                "id": "ok",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "command_status": "pass",
                "required_cases": ["first", "second"],
                "artifacts": {
                    "cases.json": "check-001.artifact-001.cases.json",
                    "extra.txt": "check-001.artifact-002.extra.txt",
                },
                "case_results": {"first": "pass", "second": "pass"},
                "stdout": "check-001.stdout.log",
                "stderr": "check-001.stderr.log",
            }
        ],
        "files": {
            "check-001.artifact-001.cases.json": b'{"schema_version": 1, "cases": {"first": "pass",'
            b' "second": "pass"}}',
            "check-001.stdout.log": b"out text\n",
            "check-001.stderr.log": b"err text\n",
            "check-001.artifact-002.extra.txt": b"extra",
        },
    },
    "host-timeout": {
        "results": [
            {
                "id": "slow",
                "claim": "claim",
                "status": "incomplete",
                "detail": "execution timed out",
                "exit_code": None,
            }
        ],
        "files": {},
    },
    "host-unexpected-exit": {
        "results": [
            {
                "id": "exit",
                "claim": "claim",
                "status": "incomplete",
                "detail": "Check exited unexpectedly",
                "exit_code": 3,
                "stdout": "check-001.stdout.log",
            }
        ],
        "files": {"check-001.stdout.log": b"partial\n"},
    },
    "host-violation": {
        "results": [
            {
                "id": "bad",
                "claim": "claim",
                "status": "violation",
                "detail": "Check reported a violation",
                "exit_code": 10,
                "command_status": "violation",
                "required_cases": ["first", "second"],
                "artifacts": {"cases.json": "check-001.artifact-001.cases.json"},
                "case_results": {"first": "fail", "second": "pass"},
            }
        ],
        "files": {
            "check-001.artifact-001.cases.json": b'{"schema_version": 1, "cases": {"first": "fail",'
            b' "second": "pass"}}'
        },
    },
    "host-zero-budget": {
        "results": [
            {
                "id": "zero",
                "claim": "claim",
                "status": "pass",
                "detail": "Check passed",
                "exit_code": 0,
                "log_omissions": ["stdout"],
                "artifact_omissions": ["a.txt"],
            }
        ],
        "files": {},
    },
}


def test_docker_create_options_are_pinned(monkeypatch):
    stub = Docker(monkeypatch)
    check = _docker_check()
    project = SimpleNamespace(
        runner="docker", contract=SimpleNamespace(behavior=[check]), files={"check.py": b"pass\n"}
    )
    runner.run_behavior(project, SNAPSHOT, None, dict(DOCKER_INFO), "run")
    [args] = [args for kind, args, _ in stub.calls if kind == "create"]
    assert _scrub(list(args), stub.names) == [
        "create",
        "--name",
        "<container-1>",
        "--label",
        "org.hypertrial.mentat.run=run",
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
        "PATH=/bin",
        "--env",
        "MENTAT_CANDIDATE_DIR=/mentat/candidate",
        "--env",
        "MENTAT_CHECKS_DIR=/mentat/checks",
        "--env",
        "MENTAT_ARTIFACTS_DIR=/mentat/artifacts",
        "--entrypoint",
        "python3",
        "image",
        "/mentat/checks/check.py",
    ]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_run_behavior_outputs_are_pinned(name, monkeypatch):
    assert SCENARIOS[name](monkeypatch) == PINNED[name]
