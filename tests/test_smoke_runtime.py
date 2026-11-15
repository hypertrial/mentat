import importlib.util
import subprocess
from pathlib import Path

import pytest

from mentat.showcases.polaris import formal
from mentat.showcases.polaris.spec import PACK

spec = importlib.util.spec_from_file_location(
    "smoke_runtime", Path(__file__).parents[1] / "scripts" / "smoke_runtime.py"
)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def test_cleanup_runs_after_unexpected_result(monkeypatch):
    calls = []
    monkeypatch.setattr(smoke, "check_cleanup", lambda context, run_id: calls.append(run_id))
    with pytest.raises(RuntimeError, match="expected pass"):
        smoke.run_checked("context", "run", "pass", lambda: {"status": "incomplete"})
    assert calls == ["run"]


def test_cleanup_failure_preserves_unexpected_result(monkeypatch, capsys):
    def fail(*_):
        raise RuntimeError("cleanup could not be verified")

    monkeypatch.setattr(smoke, "check_cleanup", fail)
    with pytest.raises(RuntimeError, match="expected pass.*cleanup could not be verified"):
        smoke.run_checked("context", "run", "pass", lambda: {"status": "incomplete"})
    with pytest.raises(RuntimeError, match="cleanup could not be verified"):
        smoke.run_checked("context", "run", "pass", lambda: {"status": "pass"})
    assert capsys.readouterr().out == ""


def test_cleanup_rejects_leftover_network(monkeypatch):
    monkeypatch.setattr(
        smoke.docker_runtime,
        "_docker",
        lambda context, *args, **kwargs: (0, b"orphan\n" if args[0] == "network" else b""),
    )
    with pytest.raises(RuntimeError, match="cleanup could not be verified"):
        smoke.check_cleanup("context", "run")


def test_missing_prerequisite_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(smoke.sys, "argv", ["smoke_runtime.py", "--repo", str(tmp_path)])
    monkeypatch.setattr(smoke.shutil, "which", lambda _: None)
    assert smoke.main() == 1
    assert "missing required tool: git" in capsys.readouterr().err


def test_mismatched_lean_toolchain_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(smoke.sys, "argv", ["smoke_runtime.py", "--repo", str(tmp_path)])
    tools = {"lean": "/lean-a/bin/lean", "lake": "/lean-b/bin/lake"}
    monkeypatch.setattr(
        smoke.shutil, "which", lambda tool: None if tool == "elan" else tools.get(tool, tool)
    )
    assert smoke.main() == 1
    assert "same installed toolchain" in capsys.readouterr().err


def test_lean_check_disables_lake_toolchain_override(monkeypatch):
    monkeypatch.setenv("LAKE_OVERRIDE_LEAN", "true")
    monkeypatch.setenv("LEAN_SYSROOT", "/alternate")

    def run(args, **kwargs):
        if args[0] == "lean":
            output = "Lean (version 4.31.0, test)"
        else:
            assert "LAKE_OVERRIDE_LEAN" not in kwargs["env"]
            output = "'restore_lookup' does not depend on any axioms"
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(formal.subprocess, "run", run)
    assert formal.run_lean(PACK / "lean")[0]["status"] == "pass"


def test_cleanup_checks_network_after_container_query_error(monkeypatch):
    calls = []

    def docker(context, *args, **kwargs):
        calls.append(args[0])
        if args[0] == "ps":
            raise RuntimeError("unavailable")
        return 0, b""

    monkeypatch.setattr(smoke.docker_runtime, "_docker", docker)
    with pytest.raises(RuntimeError, match="cleanup could not be verified"):
        smoke.check_cleanup("context", "run")
    assert calls == ["ps", "network"]
