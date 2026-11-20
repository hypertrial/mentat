import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from mentat.cli import app
from mentat.showcases.polaris import docker_runtime, oracle
from mentat.showcases.polaris.replay import Snapshot
from mentat.showcases.polaris.spec import PACK, load_cases, load_spec


@pytest.fixture
def candidate():
    return Snapshot("a" * 40, "b" * 64, (("command/setup.py", b"# fixture\n"),))


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(
        docker_runtime,
        "doctor",
        lambda _: {"ok": True, "client_image": "client", "server_image": "server"},
    )


def state(format_string, code, oom=False, status="exited"):
    if format_string == "{{.State.Status}} {{.State.ExitCode}}":
        return f"{status} {code}".encode()
    assert format_string == "{{json .State}}"
    return json.dumps({"Status": status, "ExitCode": code, "OOMKilled": oom, "Error": ""}).encode()


@pytest.mark.parametrize("pytest_exit,expected", [(0, "pass"), (1, "violation")])
@pytest.mark.parametrize("cleanup", ["ok", "nonzero", "raises"])
def test_pytest_cleanup_failure_cannot_report_success_or_violation(
    monkeypatch, runtime, candidate, pytest_exit, expected, cleanup
):
    calls = []

    def docker(_context, *args, **kwargs):
        calls.append(args)
        if args[0] == "start":
            return pytest_exit, f"MENTAT_PYTEST_RESULT={expected}\n".encode()
        if args[0] == "inspect":
            return 0, state(args[2], pytest_exit)
        if args[0] == "rm":
            if cleanup == "raises":
                raise RuntimeError("remove failed")
            return (1 if cleanup == "nonzero" else 0), b""
        return 0, b""

    monkeypatch.setattr(docker_runtime, "_docker", docker)
    result = docker_runtime.run_pytest(candidate, "context", "def test_ok(): pass", "pytest")
    assert result["status"] == (expected if cleanup == "ok" else "incomplete")
    assert any(call[0] == "rm" for call in calls)


@pytest.mark.parametrize(
    "source,expected",
    [
        ("def test_ok(): pass\n", "pass"),
        ("def test_failure(): assert False\n", "violation"),
        (
            "import pytest\n@pytest.fixture(autouse=True)\ndef setup(): raise RuntimeError()\n"
            "def test_ok(): pass\n",
            "incomplete",
        ),
        (
            "import pytest\n@pytest.fixture\ndef broken(): raise RuntimeError()\n"
            "def test_failure(): assert False\ndef test_setup(broken): pass\n",
            "incomplete",
        ),
        ("def test_error(): raise ValueError()\n", "incomplete"),
        (
            "from hypothesis import given, settings, strategies as st\n"
            "@settings(derandomize=True, database=None)\n"
            "@given(reverse=st.booleans())\n"
            "def test_failure(reverse):\n"
            "    if reverse:\n"
            "        assert False, 'reverse order'\n"
            "    assert False, 'forward order'\n",
            "violation",
        ),
        (
            "def test_mixed():\n"
            "    raise ExceptionGroup('mixed', [AssertionError(), RuntimeError()])\n",
            "incomplete",
        ),
        (
            "import pytest\ndef test_first(): assert False\n"
            "def test_abort(): pytest.exit('interrupted', returncode=1)\n",
            "incomplete",
        ),
        (
            "import pytest\n@pytest.fixture\ndef broken(): raise RuntimeError()\n"
            "def test_first(broken): pass\n"
            "def test_abort(): pytest.exit('interrupted', returncode=0)\n",
            "incomplete",
        ),
    ],
)
@pytest.mark.parametrize("drop_marker", [False, True])
def test_pytest_launcher_classifies_real_pytest_outcomes(
    monkeypatch, runtime, candidate, source, expected, drop_marker
):
    command = None
    test_path = None
    exit_code = None

    def docker(_context, *args, **kwargs):
        nonlocal command, test_path, exit_code
        if args[0] == "create":
            command = list(args[args.index("client") + 1 :])
            assert command[:2] == ["python", "-c"]
        elif args[0] == "cp" and args[-1].endswith(":/opt/test_candidate.py"):
            test_path = args[1]
        elif args[0] == "start":
            assert command is not None and test_path is not None
            local_command = [
                sys.executable,
                *(arg.replace("/opt/test_candidate.py", test_path) for arg in command[1:]),
            ]
            completed = subprocess.run(local_command, capture_output=True, timeout=30)
            exit_code = completed.returncode
            output = completed.stdout
            if drop_marker:
                output = b"\n".join(
                    line
                    for line in output.splitlines()
                    if not line.startswith(b"MENTAT_PYTEST_RESULT=")
                )
            return exit_code, output
        elif args[0] == "inspect":
            return 0, state(args[2], exit_code)
        return 0, b""

    monkeypatch.setattr(docker_runtime, "_docker", docker)
    result = docker_runtime.run_pytest(candidate, "context", source, "pytest")
    assert result["status"] == ("incomplete" if drop_marker else expected), result


@pytest.mark.parametrize(
    "exit_code,output",
    [
        (0, b"MENTAT_PYTEST_RESULT=violation\n"),
        (1, b"MENTAT_PYTEST_RESULT=pass\n"),
        (1, b"MENTAT_PYTEST_RESULT=unknown\n"),
        (1, b"MENTAT_PYTEST_RESULT=violation\nextra output\n"),
    ],
)
def test_pytest_rejects_inconsistent_completion_marker(
    monkeypatch, runtime, candidate, exit_code, output
):
    def docker(_context, *args, **kwargs):
        if args[0] == "start":
            return exit_code, output
        if args[0] == "inspect":
            return 0, state(args[2], exit_code)
        return 0, b""

    monkeypatch.setattr(docker_runtime, "_docker", docker)
    result = docker_runtime.run_pytest(candidate, "context", "def test_ok(): pass", "pytest")
    assert result["status"] == "incomplete"


@pytest.mark.parametrize("create_raises", [False, True])
def test_pytest_removes_container_when_create_fails(monkeypatch, runtime, candidate, create_raises):
    created = []
    removed = []

    def docker(_context, *args, **kwargs):
        if args[0] == "create":
            created.append(args[args.index("--name") + 1])
            if create_raises:
                raise RuntimeError("create response lost")
            return 1, b""
        if args[0] == "rm":
            removed.append(args[-1])
        return 0, b""

    monkeypatch.setattr(docker_runtime, "_docker", docker)
    for _ in range(2):
        result = docker_runtime.run_pytest(candidate, "context", "def test_ok(): pass", "pytest")
        assert result["status"] == "incomplete"
    assert len(set(created)) == 2
    assert removed == created


def replay_docker(stage, exit_code, oom, server="running"):
    current = {}
    affected_servers = set()

    def docker(_context, *args, **kwargs):
        if args[0] == "start" and args[1] == "-a":
            name = args[-1]
            action = (
                "re-export"
                if "candidate-1-export" in name
                else (
                    "apply"
                    if "candidate-1-apply" in name
                    else "export"
                    if "candidate-0-export" in name
                    else None
                )
            )
            code = exit_code if action == stage else 0
            if action == stage:
                affected_servers.add("0" if action == "export" else "1")
            current[name] = (code, oom if action == stage else False)
            if "oracle-1-read" in name:
                reads = sum("oracle-1-read" in n for n in current)
                return code, json.dumps({"catalog_exists": reads > 1, "namespaces": []}).encode()
            if action == stage and code == 0 and server != "running":
                return code, b"invalid export"
            return code, b"{}"
        elif args[0] == "inspect":
            if "-server-" in args[-1]:
                if server == "running" or args[-1].rsplit("-", 1)[-1] not in affected_servers:
                    return 0, state(args[2], 0, status="running")
                return 0, state(args[2], 137, oom=(server == "oom"))
            code, killed = current[args[-1]]
            return 0, state(args[2], code, killed)
        return 0, b""

    return docker


@pytest.mark.parametrize("stage", ["export", "apply", "re-export"])
@pytest.mark.parametrize(
    "exit_code,oom,server,expected",
    [
        (1, False, "running", "violation"),
        (137, True, "running", "incomplete"),
        (1, True, "running", "incomplete"),
        (137, False, "running", "incomplete"),
        (1, False, "oom", "incomplete"),
        (1, False, "exited", "incomplete"),
        (0, False, "oom", "incomplete"),
    ],
)
def test_replay_distinguishes_command_failure_from_runtime_failure(
    monkeypatch, runtime, candidate, stage, exit_code, oom, server, expected
):
    spec = load_spec(PACK / "contract.json")
    case = load_cases(PACK / "public.json", spec)[0]
    monkeypatch.setattr(oracle, "compare", lambda *args: [{"status": "pass"}])

    def validate_export(raw, _selected):
        if raw == b"invalid export":
            raise ValueError("invalid export")
        return {"catalog_exists": True}

    monkeypatch.setattr(oracle, "validate_export", validate_export)
    docker = replay_docker(stage, exit_code, oom, server)
    monkeypatch.setattr(docker_runtime, "_docker", docker)
    result = docker_runtime.run_case(candidate, "context", case, spec.selected_properties, "run")
    assert result["status"] == expected, result
    assert any(
        check.get("stage") == stage and check["status"] == expected for check in result["checks"]
    )


@pytest.mark.parametrize("native_status", ["pass", "violation", "incomplete"])
def test_verify_reproduces_operational_replay_violation_before_emitting_artifact(
    tmp_path, monkeypatch, candidate, runtime, native_status
):
    from mentat.showcases.polaris import cli

    spec = load_spec(PACK / "contract.json")
    fixture = load_cases(PACK / "public.json", spec)[0]
    monkeypatch.setattr(cli, "contract_inputs", lambda ctx: (spec, "fingerprint"))
    monkeypatch.setattr(cli.model_evidence, "pack_digest", lambda spec: "fingerprint")
    monkeypatch.setattr(cli.model_evidence, "approval", lambda *args: {})
    monkeypatch.setattr(cli, "run_formal", lambda *args: [{"status": "pass"}])
    monkeypatch.setattr(cli, "candidate_repo", lambda repo, state: repo)
    monkeypatch.setattr(cli, "snapshot", lambda *args: candidate)
    monkeypatch.setattr(cli, "load_cases", lambda *args: [fixture])
    monkeypatch.setattr(oracle, "compare", lambda *args: [{"status": "pass"}])
    monkeypatch.setattr(oracle, "validate_export", lambda *args: {"catalog_exists": True})
    monkeypatch.setattr(docker_runtime, "_docker", replay_docker("export", 1, False))
    # Exercise the real replay, including its operational failure check.
    monkeypatch.setattr(cli, "run_case", docker_runtime.run_case)
    called = []
    monkeypatch.setattr(
        docker_runtime, "run_pytest", lambda *args: called.append(args) or {"status": native_status}
    )
    saved = []
    monkeypatch.setattr(
        cli.evidence,
        "save_run",
        lambda state, record, artifacts: saved.append((record, artifacts)) or state,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    result = CliRunner().invoke(
        app,
        [
            "--state",
            str(tmp_path / "state"),
            "showcase",
            "polaris",
            "verify",
            "--repo",
            str(repo),
            "--rev",
            candidate.revision,
            "--json",
        ],
    )
    assert result.exit_code == 1, result.output
    assert len(called) == 1
    assert saved[0][0]["regressions"][0]["status"] == native_status
    assert bool(saved[0][1]) == (native_status == "violation")


def test_verify_prioritizes_violation_over_incomplete(tmp_path, monkeypatch, candidate):
    from mentat.showcases.polaris import cli

    spec = load_spec(PACK / "contract.json")
    fixture = load_cases(PACK / "public.json", spec)[0]
    monkeypatch.setattr(cli, "contract_inputs", lambda ctx: (spec, "fingerprint"))
    monkeypatch.setattr(cli.model_evidence, "pack_digest", lambda spec: "fingerprint")
    monkeypatch.setattr(cli.model_evidence, "approval", lambda *args: {})
    monkeypatch.setattr(
        cli,
        "run_formal",
        lambda *args: [
            {"status": "violation", "kind": "z3", "detail": "failed"},
            {"status": "incomplete", "kind": "lean", "detail": "unavailable"},
        ],
    )
    monkeypatch.setattr(cli, "candidate_repo", lambda repo, state: repo)
    monkeypatch.setattr(cli, "snapshot", lambda *args: candidate)
    monkeypatch.setattr(cli, "load_cases", lambda *args: [fixture])
    repo = tmp_path / "repo"
    repo.mkdir()
    result = CliRunner().invoke(
        app,
        [
            "--state",
            str(tmp_path / "state"),
            "showcase",
            "polaris",
            "verify",
            "--repo",
            str(repo),
            "--rev",
            candidate.revision,
            "--fresh",
            "--json",
        ],
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["status"] == "violation"


def test_polaris_trusted_chat_relay_validates_and_records_decisions(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from mentat import evidence
    from mentat.showcases.polaris import cli

    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    digest = "a" * 64
    spec = SimpleNamespace(as_dict=lambda: {})
    monkeypatch.setattr(cli, "contract_inputs", lambda _: (spec, digest))
    monkeypatch.setattr(cli, "load_spec", lambda _: spec)
    monkeypatch.setattr(cli.model_evidence, "pack_digest", lambda _: digest)
    monkeypatch.setattr(cli, "load_cases", lambda *_: None)
    monkeypatch.setattr(cli, "run_formal", lambda *_: [{"status": "pass"}])
    runner = CliRunner()
    prefix = ["--state", str(state_dir), "showcase", "polaris"]
    assert runner.invoke(app, prefix + ["spec", "approve", digest]).exit_code != 0
    assert runner.invoke(app, prefix + ["spec", "approve", "bad", "--trusted-chat-approval"]).exit_code != 0
    result = runner.invoke(app, prefix + ["spec", "approve", digest, "--trusted-chat-approval"])
    assert result.exit_code == 0, result.output
    assert evidence.read_json(state_dir / "approval.json")["decision_source"] == "unverified_agent_relay"
    run_id = "b" * 32
    evidence.save_run(state_dir, {"run_id": run_id, "kind": "verification", "status": "pass",
                      "revision": "c" * 40, "contract_digest": digest, "reproduce": "example"}, {})
    assert runner.invoke(app, prefix + ["accept", run_id]).exit_code != 0
    result = runner.invoke(app, prefix + ["accept", run_id, "--trusted-chat-approval"])
    assert result.exit_code == 0, result.output
    assert evidence.read_json(state_dir / "acceptances" / f"{run_id}.json")["decision_source"] == "unverified_agent_relay"
    (state_dir / "runs" / run_id / "report.md").write_text("tampered")
    assert runner.invoke(app, prefix + ["accept", run_id, "--trusted-chat-approval"]).exit_code != 0
