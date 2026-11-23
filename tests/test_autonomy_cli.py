"""Exercise autonomy through the public CLI without remote services."""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from mentat import autonomy_cli
from mentat.cli import app

runner = CliRunner()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    return path


def invoke(repo, *args):
    return runner.invoke(app, ["--state", str(repo.parent / "state"), *args])


def commit(repo, source):
    (repo / "candidate.py").write_text(source)
    subprocess.run(["git", "-C", str(repo), "add", "candidate.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def test_help_and_version_keep_existing_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for command in ("verify", "spec", "claims", "evidence", "init", "run", "resume", "status", "ui", "worker", "source-verify"):
        assert command in result.output
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0 and version.output.startswith("mentat ")


def test_init_writes_reviewable_configuration_without_credentials(repo):
    result = invoke(repo, "init", "--repo", str(repo))
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output["status"] == "initialized"
    config = json.loads((repo / ".mentat/workspace.json").read_text())
    assert output["configuration"] == config
    assert config["model"] == "gpt-6.1-terra"
    assert not {"api_key", "token", "password", "credentials"} & set(config)
    assert "commit" in output["next"].lower()


@pytest.mark.parametrize("args", [("status", "--json"), ("resume", "--no-ui"), ("ui",)])
def test_missing_session_is_incomplete_json(repo, args):
    result = invoke(repo, *args, "--repo", str(repo))
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["status"] == "incomplete"



def test_run_with_missing_committed_config_returns_incomplete(repo):
    commit(repo, "def f(x):\n    return x\n")
    result = invoke(repo, "run", "--repo", str(repo), "--no-ui")
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["status"] == "incomplete"

def test_source_verify_real_committed_clamp_and_unsupported_source(repo, tmp_path):
    revision = commit(repo, "def clamp(x):\n    if x < 0:\n        return 0\n    else:\n        return x\n")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"inputs": {"x": "int"}, "requires": "True", "ensures": "result >= 0 and ((x < 0 and result == 0) or (x >= 0 and result == x))"}))
    args = ("source-verify", "--repo", str(repo), revision, "candidate.py", "clamp", "--spec", str(spec))
    result = invoke(repo, *args)
    assert result.exit_code == 0, result.output
    proof = json.loads(result.stdout)
    assert proof["status"] == "proved" and proof["source_identity"]["revision"] == revision
    spec.write_text(json.dumps({"inputs": {"x": "int"}, "requires": "True", "ensures": "result == x"}))
    counterexample = invoke(repo, *args)
    assert counterexample.exit_code == 1, counterexample.output
    assert json.loads(counterexample.stdout)["status"] == "counterexample"
    revision = commit(repo, "def clamp(x):\n    return abs(x)\n")
    result = invoke(repo, "source-verify", "--repo", str(repo), revision, "candidate.py", "clamp", "--spec", str(spec))
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["status"] == "incomplete"


def test_run_resume_and_ui_lifecycle_with_mocked_controller(repo, monkeypatch):
    calls = []
    servers = []

    class FakeCoordinator:
        def __init__(self, path, store, agent, worker, github):
            self.store = store
            self.session_id = None

        def run(self, *, resume):
            calls.append(resume)
            self.session_id = resume or self.store.new_session(str(repo), "base", "head", {}, 1, 1000)
            return self.store.snapshot(self.session_id)

        def control(self, action, payload):
            return {"status": "recorded"}

    class FakeServer:
        url = "http://127.0.0.1:1234/#capability"

        def __init__(self):
            self.closed = False
            self.stopped = False

        def serve_forever(self):
            return None

        def shutdown(self):
            self.stopped = True

        def server_close(self):
            self.closed = True

    def server(snapshot, control, **kwargs):
        result = FakeServer()
        servers.append(result)
        return result

    monkeypatch.setattr(autonomy_cli, "Coordinator", FakeCoordinator)
    monkeypatch.setattr(autonomy_cli, "create_server", server)
    result = invoke(repo, "run", "--repo", str(repo))
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert calls == [None] and output["status"] == "running"
    assert (repo.parent / "state").exists()
    assert "Autonomy report" in open(output["report"]).read()
    assert servers[0].stopped and servers[0].closed
    result = invoke(repo, "status", "--repo", str(repo), "--json")
    assert result.exit_code == 0 and json.loads(result.stdout)["session"]["id"] == output["session_id"]
    result = invoke(repo, "resume", "--repo", str(repo), "--no-ui")
    assert result.exit_code == 0 and calls[-1] == output["session_id"]
    result = invoke(repo, "ui", "--repo", str(repo))
    assert result.exit_code == 0 and servers[-1].closed


def test_worker_doctor_reports_mocked_diagnostics(repo, monkeypatch):
    monkeypatch.setattr(autonomy_cli, "diagnostics", lambda path: {"status": "ready"})
    monkeypatch.setattr(autonomy_cli, "LocalWorker", lambda: type("Worker", (), {"diagnostics": lambda self: {"status": "missing"}})())
    result = invoke(repo, "worker", "doctor", "--repo", str(repo))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["worker"]["status"] == "missing"
