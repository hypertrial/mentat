"""Trusted worker tooling must be independent of the target checkout."""

import hashlib
import json
from pathlib import Path

import pytest

from mentat import autonomy_runtime, autonomy_worker


def test_trusted_assets_are_pinned_and_embedded(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")
    assets = autonomy_worker.worker_assets()
    profile = autonomy_worker.profile_identity(tmp_path)
    script = autonomy_worker.provision_script(tmp_path)
    for _, profile_key, data in assets:
        assert profile[profile_key] == hashlib.sha256(data).hexdigest()
        assert profile[profile_key].encode() in script
    requirements = assets[0][2].decode().splitlines()
    assert all("==" in line for line in requirements)
    manifest = json.loads(assets[1][2])
    lock = json.loads(assets[2][2])
    assert manifest["devDependencies"] == lock["packages"][""]["devDependencies"]
    assert all("integrity" in entry for name, entry in lock["packages"].items() if name)


def test_worker_selects_only_exact_tracked_version_and_mounts_tools():
    guest = autonomy_runtime._GUEST_RUNNER
    assert 'use_py314 = declared == b"3.14"' in guest
    assert '"/opt/mentat-tools/.venv-3.14" if use_py314' in guest
    assert '"/work/node_modules"' in guest
    assert '"NODE_PATH", "/opt/mentat-tools/node-tools/node_modules"' in guest
    assert '"PLAYWRIGHT_BROWSERS_PATH", "/opt/mentat-tools/browsers"' in guest
    assert 'assert not os.path.lexists(modules)' in guest


def test_provisioner_installs_locked_tools_without_candidate_code():
    script = autonomy_worker.PROVISION_SCRIPT.decode()
    assert 'npm ci --prefix "$root/node-tools" --ignore-scripts' in script
    assert '--no-deps -r "$root/node-tools/requirements-314.txt"' in script
    assert '"$root/.venv-3.14/bin/python"' in script
    assert '/work' not in script
    assert Path(autonomy_worker.__file__).parent.joinpath("worker_assets/package-lock.json").is_file()


@pytest.mark.parametrize(("content", "expected"), [
    (None, "3.12"), (b"3.12", "3.12"), (b"3.14\n", "3.14"),
    (b"3.14\r\n", "3.14"),
])
def test_supported_tracked_version(content, expected):
    files = {} if content is None else {".python-version": (0o644, content)}
    assert autonomy_runtime._target_python(files) == expected


@pytest.mark.parametrize("content", [b"3.14.1\n", b"3.13\n", b"", b"3.14 \n", b"3.14\n\n", b"garbage"])
def test_unsupported_or_malformed_version_fails_closed(content):
    with pytest.raises(ValueError, match="unsupported or invalid"):
        autonomy_runtime._target_python({".python-version": (0o644, content)})


def test_symlink_version_marker_is_rejected():
    with pytest.raises(ValueError, match="unsupported or invalid"):
        autonomy_runtime._target_python({".python-version": (0o120000, b"3.14")})


def test_python314_build_backend_is_fully_pinned():
    requirements = autonomy_worker.worker_assets()[0][2].decode().splitlines()
    assert {line.split("==")[0] for line in requirements} >= {
        "hatchling", "packaging", "pathspec", "pluggy", "trove-classifiers"
    }
    assert all("==" in line for line in requirements)


@pytest.mark.parametrize(("marker_payload", "service_code", "expected_code"), [
    ({"unit": "unit", "returncode": 0}, 0, 0),
    (None, 0, 125),
    ({"unit": "unit", "returncode": -15}, 125, 125),
    ({"unit": "wrong", "returncode": 0}, 0, 125),
])
def test_guest_constructs_bounded_native_test_environment(monkeypatch, marker_payload, service_code, expected_code, capsys):
    import base64
    import io
    import os
    import shutil
    import subprocess
    import sys
    import tarfile
    from types import SimpleNamespace

    source = io.BytesIO()
    with tarfile.open(fileobj=source, mode="w"):
        pass
    envelope = io.BytesIO()
    with tarfile.open(fileobj=envelope, mode="w") as archive:
        for name, data in (("source.tar", source.getvalue()), ("source.bundle", b"bundle")):
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    envelope.seek(0)
    rev = "a" * 40
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=envelope))
    monkeypatch.setattr(sys, "argv", ["guest", base64.b64encode(b'["python", "verify.py"]').decode(), "60", "unit", rev])
    monkeypatch.setattr(os, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(shutil, "copytree", lambda source, dest, **kwargs: os.mkdir(dest))
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: rev.encode())
    commands = []
    def run(cmd, **kwargs):
        commands.append(cmd)
        if cmd[0] == "git":
            os.mkdir(cmd[-1])
        elif cmd[0] == "systemd-run":
            marker = cmd[cmd.index("bwrap") - 2]
            if marker_payload is not None:
                Path(marker).write_text(json.dumps(marker_payload))
            return SimpleNamespace(returncode=service_code)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit) as exc:
        exec(autonomy_runtime._GUEST_RUNNER, {})
    assert exc.value.code == expected_code
    stderr = capsys.readouterr().err
    if expected_code:
        assert "MENTAT_INCOMPLETE:unit:" in stderr
        assert "MENTAT_FINISHED:unit:" not in stderr
    else:
        assert "MENTAT_FINISHED:unit:0" in stderr
    command = commands[-1]
    assert command[command.index("/usr/bin/python3") + 1:][:2] == ["-I", "-B"]
    assert "--property=TasksMax=256" in command
    index = command.index("TEST_JOBS")
    assert command[index - 1:index + 2] == ["--setenv", "TEST_JOBS", "1"]


def test_execution_identity_changes_with_runtime_or_trusted_asset():
    digest = autonomy_runtime._execution_digest
    parts = [b"runtime guest profile A", b"worker helper", b"trusted lock"]
    original = digest(parts)
    assert digest(parts) == original
    assert digest([b"runtime guest profile B", *parts[1:]]) != original
    assert digest([*parts[:2], b"changed trusted lock"]) != original
    assert digest([b"ab", b"c"]) != digest([b"a", b"bc"])
    assert len(autonomy_runtime.execution_identity()) == 64


@pytest.mark.parametrize(("payload", "service_code", "expected", "marker"), [
    ({"unit": "unit", "returncode": 0}, 0, 0, "MENTAT_FINISHED:unit:0"),
    ({"unit": "unit", "returncode": 1}, 1, 1, "MENTAT_FINISHED:unit:1"),
    (None, 0, 125, "MENTAT_INCOMPLETE:unit:"),
    ({"unit": "unit", "returncode": -15}, 0, 125, "MENTAT_INCOMPLETE:unit:"),
    ({"unit": "other", "returncode": 0}, 0, 125, "MENTAT_INCOMPLETE:unit:"),
])
def test_protected_checker_requires_normal_service_completion(monkeypatch, capsys, payload, service_code, expected, marker):
    import io
    import subprocess
    import sys
    import tarfile
    from types import SimpleNamespace

    envelope = io.BytesIO()
    with tarfile.open(fileobj=envelope, mode="w"):
        pass
    envelope.seek(0)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=envelope))
    monkeypatch.setattr(sys, "argv", ["checker", "unit", "60"])
    commands = []
    def run(cmd, **kwargs):
        commands.append(cmd)
        marker_path = cmd[cmd.index("/usr/bin/env") - 2]
        if payload is not None:
            Path(marker_path).write_text(json.dumps(payload))
        return SimpleNamespace(returncode=service_code)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit) as exc:
        exec(autonomy_runtime._CHECKER_GUEST_RUNNER, {})
    assert exc.value.code == expected
    assert marker in capsys.readouterr().err
    command = commands[0]
    assert command[command.index("/usr/bin/python3") + 1:][:2] == ["-I", "-B"]
    assert command[-6:] == ["/opt/mentat-tools/.venv/bin/pytest", "-q", "-rs", "-n", "0", "tests/test_proof_checker.py"]


def test_verification_request_schema_requires_nonempty_command():
    argv = autonomy_runtime._SCHEMA["properties"]["verification_requests"]["items"]["properties"]["argv"]
    assert argv["minItems"] == 1
    assert argv["items"]["minLength"] == 1


@pytest.mark.parametrize("argv", [[], [""], ["python", ""], ["python", "bad\0arg"]])
def test_invalid_verification_command_is_rejected(argv):
    result = {"schema_version": 1, "decision": "revise", "summary": "repair", "requirements": [],
              "claims": [], "findings": [], "defects": [], "changes": [],
              "verification_requests": [{"argv": argv, "claim_ids": [], "method": "test"}]}
    with pytest.raises(ValueError, match="verification_requests field argv"):
        autonomy_runtime._validate_result(result)
    result["verification_requests"] = []
    assert autonomy_runtime._validate_result(result) == result
