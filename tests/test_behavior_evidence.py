import json
import os
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mentat import runner
from mentat.project import Behavior

BASE = {
    "id": "behavior",
    "claim": "claim",
    "argv": [sys.executable, "{checks}/check.py"],
    "artifacts": ["cases.json"],
    "violation_exit_codes": [10],
    "case_report": {"artifact": "cases.json", "required_cases": ["first", "second"]},
}


def run_check(script, **overrides):
    values = {**BASE, **overrides}
    if values.get("case_report") is None:
        values.pop("case_report", None)
    check = Behavior.model_validate(values)
    project = SimpleNamespace(
        runner="host",
        contract=SimpleNamespace(behavior=[check]),
        files={"check.py": script.encode()},
    )
    snapshot = SimpleNamespace(files={"original.txt": (0o644, b"original")})
    checks, files = runner.run_behavior(
        project, snapshot, None, {"path": os.environ.get("PATH", ""), "executables": {}}, "test"
    )
    return checks[0], files


def script_for(report, code):
    return (
        "from pathlib import Path\nimport os, sys\n"
        f"Path(os.environ['MENTAT_ARTIFACTS_DIR'], 'cases.json').write_text({report!r})\n"
        f"sys.exit({code})\n"
    )


def report(**cases):
    return json.dumps({"schema_version": 1, "cases": cases})


@pytest.mark.parametrize("cancel_before_control", [False, True])
def test_cancellation_stops_next_behavior_phase(monkeypatch, cancel_before_control):
    first = Behavior.model_validate({
        **BASE,
        **({"negative_control": {"args": ["--bad"], "violation_exit_code": 10,
                                 "failing_case": "first"}} if cancel_before_control else {}),
    })
    second = first.model_copy(update={"id": "second"})
    project = SimpleNamespace(runner="host", contract=SimpleNamespace(behavior=[first, second]))
    phases = []

    def phase(_project, _snapshot, _inherited, _info, _files, _remaining,
              check, result, _prefix, _args):
        phases.append(check.id)
        result["status"] = "pass"

    def checkpoint():
        if phases:
            raise InterruptedError("cancelled")

    monkeypatch.setattr(runner, "_host_phase", phase)
    monkeypatch.setattr(runner, "_host_precheck", lambda *args: False)
    with pytest.raises(InterruptedError, match="cancelled"):
        runner.run_behavior(project, None, None, {"path": ""}, "test", checkpoint=checkpoint)
    assert phases == ["behavior"]


@pytest.mark.parametrize(
    ("case_report", "artifacts"),
    [
        ({"artifact": "other.json", "required_cases": ["first"]}, ["cases.json"]),
        ({"artifact": "cases.json", "required_cases": []}, ["cases.json"]),
        ({"artifact": "cases.json", "required_cases": ["first", "first"]}, ["cases.json"]),
        ({"artifact": "cases.json", "required_cases": [""]}, ["cases.json"]),
    ],
)
def test_case_report_contract_rejects_invalid_declarations(case_report, artifacts):
    with pytest.raises(ValidationError):
        Behavior.model_validate({**BASE, "case_report": case_report, "artifacts": artifacts})


@pytest.mark.parametrize(
    ("payload", "code", "expected"),
    [
        (report(first="pass", second="pass"), 0, "pass"),
        (report(first="pass", second="fail"), 10, "violation"),
        (report(first="pass", second="fail"), 0, "incomplete"),
        (report(first="pass", second="pass"), 10, "incomplete"),
        (report(first="skip", second="pass"), 0, "incomplete"),
        (report(first="error", second="pass"), 10, "incomplete"),
        (report(first="pass"), 0, "incomplete"),
        (report(first="pass", second="unknown"), 0, "incomplete"),
        (
            '{"schema_version":1,"cases":{"first":"pass","first":"fail","second":"pass"}}',
            0,
            "incomplete",
        ),
        ('{"schema_version":1,"cases":', 0, "incomplete"),
        (
            json.dumps({"schema_version": True, "cases": {"first": "pass", "second": "pass"}}),
            0,
            "incomplete",
        ),
    ],
)
def test_case_report_must_agree_with_process(payload, code, expected):
    result, _ = run_check(script_for(payload, code))
    assert result["status"] == expected
    if payload == report(first="pass", second="fail"):
        assert result["case_results"] == {"first": "pass", "second": "fail"}


def test_missing_case_report_and_timeout_are_incomplete():
    result, _ = run_check("pass\n")
    assert result["status"] == "incomplete"
    result, _ = run_check("import time\ntime.sleep(3)\n", timeout_seconds=1)
    assert result["status"] == "incomplete"


def test_case_report_lost_to_output_budget_is_incomplete(monkeypatch):
    monkeypatch.setattr(runner, "MAX_RUN_OUTPUT", 1)
    result, _ = run_check(script_for(report(first="pass", second="pass"), 0))
    assert result["status"] == "incomplete"


def test_negative_control_runs_in_fresh_staging_and_requires_named_failure():
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
    result, _ = run_check(
        script,
        negative_control={
            "args": ["--known-bad"],
            "violation_exit_code": 10,
            "failing_case": "first",
        },
    )
    assert result["status"] == "pass"
    assert result["case_results"] == {"first": "pass", "second": "pass"}
    assert result["negative_control"]["case_results"] == {"first": "fail", "second": "pass"}
    assert result["negative_control"]["exit_code"] == 10


@pytest.mark.parametrize(
    ("control_cases", "control_code"),
    [
        ({"first": "pass", "second": "pass"}, 0),
        ({"first": "pass", "second": "fail"}, 10),
        ({"first": "fail", "second": "fail"}, 10),
        ({"first": "fail", "second": "skip"}, 10),
        ({"first": "fail", "second": "pass"}, 0),
    ],
)
def test_negative_control_must_demonstrate_specific_violation(control_cases, control_code):
    script = (
        "import json, os, sys\nfrom pathlib import Path\n"
        f"cases = {control_cases!r} if len(sys.argv) > 1 else {{'first': 'pass', 'second': 'pass'}}\n"
        "Path(os.environ['MENTAT_ARTIFACTS_DIR'], 'cases.json').write_text(json.dumps({'schema_version': 1, 'cases': cases}))\n"
        f"sys.exit({control_code} if len(sys.argv) > 1 else 0)\n"
    )
    result, _ = run_check(
        script,
        negative_control={
            "args": ["--known-bad"],
            "violation_exit_code": 10,
            "failing_case": "first",
        },
    )
    assert result["status"] == "incomplete"


def test_negative_control_is_not_run_after_primary_violation():
    script = "import sys\nassert len(sys.argv) == 1\n" + script_for(
        report(first="fail", second="pass"), 10
    )
    result, _ = run_check(
        script,
        negative_control={
            "args": ["--known-bad"],
            "violation_exit_code": 10,
            "failing_case": "first",
        },
    )
    assert result["status"] == "violation"


@pytest.mark.parametrize(
    "control",
    [
        {"args": ["--bad"], "violation_exit_code": 1, "failing_case": "first"},
        {"args": ["--bad"], "violation_exit_code": 10, "failing_case": "absent"},
        {"args": ["--bad"], "violation_exit_code": 10},
        {"args": [], "violation_exit_code": 10, "failing_case": "first"},
        {"args": ["{selectors}"], "violation_exit_code": 10, "failing_case": "first"},
    ],
)
def test_negative_control_contract_rejects_unreserved_code_or_unknown_case(control):
    with pytest.raises(ValidationError):
        Behavior.model_validate({**BASE, "negative_control": control})


def test_negative_control_host_setup_failure_is_incomplete(monkeypatch):
    stage = runner._stage
    calls = 0

    def fail_second_candidate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("control staging failed")
        return stage(*args, **kwargs)

    monkeypatch.setattr(runner, "_stage", fail_second_candidate)
    result, _ = run_check(
        script_for(report(first="pass", second="pass"), 0),
        negative_control={
            "args": ["--known-bad"],
            "violation_exit_code": 10,
            "failing_case": "first",
        },
    )
    assert result["status"] == "incomplete"


def test_negative_control_timeout_is_incomplete():
    script = "import sys, time\nif len(sys.argv) > 1: time.sleep(3)\n" + script_for(
        report(first="pass", second="pass"), 0
    )
    result, _ = run_check(
        script,
        timeout_seconds=1,
        negative_control={
            "args": ["--known-bad"],
            "violation_exit_code": 10,
            "failing_case": "first",
        },
    )
    assert result["status"] == "incomplete"


def test_negative_control_without_case_report():
    script = "import sys\nsys.exit(10 if sys.argv[1:] == ['--bad'] else 0)\n"
    result, _ = run_check(
        script,
        case_report=None,
        artifacts=[],
        negative_control={"args": ["--bad"], "violation_exit_code": 10},
    )
    assert result["status"] == "pass"


@pytest.mark.parametrize("fail_control_cleanup", [False, True])
def test_docker_control_uses_separate_staged_container_and_cleans_up(
    monkeypatch, fail_control_cleanup
):
    check = Behavior.model_validate(
        {
            **BASE,
            "negative_control": {
                "args": ["--known-bad"],
                "violation_exit_code": 10,
                "failing_case": "first",
            },
        }
    )
    project = SimpleNamespace(
        runner="docker", contract=SimpleNamespace(behavior=[check]), files={"check.py": b"trusted"}
    )
    snapshot = SimpleNamespace(files={"original.txt": (0o644, b"original")})
    calls, containers, staged = [], [], []
    stage_archive = runner._stage_archive

    def stage(archive, candidate_files, trusted_files):
        staged.append((dict(candidate_files), dict(trusted_files)))
        return stage_archive(archive, candidate_files, trusted_files)

    def docker(context, *args, **kwargs):
        calls.append(args)
        if args[0] == "create":
            containers.append(args[args.index("--name") + 1])
            assert ("--known-bad" in args) == (len(containers) == 2)
            return 0, b"", b""
        if args[0] == "start":
            return (10 if len(containers) == 2 and args[-1] == containers[1] else 0), b"", b""
        if args[0] == "inspect":
            code = 10 if len(containers) == 2 and args[-1] == containers[1] else 0
            return 0, json.dumps({"Status": "exited", "ExitCode": code}).encode(), b""
        if (
            args[0] == "rm"
            and fail_control_cleanup
            and len(containers) == 2
            and args[-1] == containers[1]
        ):
            return 1, b"", b"cleanup failed"
        return 0, b"", b""

    def artifact(context, name, artifact_name):
        assert artifact_name == "cases.json"
        return report(
            first="fail" if len(containers) == 2 and name == containers[1] else "pass",
            second="pass",
        ).encode()

    monkeypatch.setattr(runner, "_docker", docker)
    monkeypatch.setattr(runner, "_docker_artifact", artifact)
    monkeypatch.setattr(runner, "_stage_archive", stage)
    checks, _ = runner.run_behavior(
        project, snapshot, None, {"context": "test", "path": "/bin", "image_id": "image"}, "run"
    )
    assert checks[0]["status"] == ("incomplete" if fail_control_cleanup else "pass")
    assert len(containers) == 2 and containers[0] != containers[1]
    assert staged == [(snapshot.files, project.files), (snapshot.files, project.files)]
    assert [args[-1] for args in calls if args[0] == "rm"] == containers
