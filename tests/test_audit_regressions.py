"""Regression tests for trusted execution identity and host-wide proof exclusion."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import mentat


def _python(code, *, package_root, env=None):
    child_env = {**os.environ, "PYTHONPATH": str(package_root), "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
    child_env = {key: value for key, value in child_env.items() if value is not None}
    return subprocess.run(
        [sys.executable, "-c", code], cwd=package_root, env=child_env,
        text=True, capture_output=True, check=True,
    ).stdout.strip()


@pytest.mark.parametrize("helper", ["autonomy_source_corrections.py", "autonomy_review_inputs.py"])
def test_execution_identity_tracks_trusted_helper_and_rejects_loaded_drift(tmp_path, helper):
    package = tmp_path / "mentat"
    shutil.copytree(Path(mentat.__file__).parent, package, ignore=shutil.ignore_patterns("__pycache__"))
    identity = "from mentat.autonomy_runtime import execution_identity; print(execution_identity())"
    before = _python(identity, package_root=tmp_path)
    unrelated = package / "unrelated_audit_fixture.py"
    unrelated.write_text("# unrelated\n")
    assert _python(identity, package_root=tmp_path) == before

    drift = f'''\
from pathlib import Path
from mentat.autonomy_runtime import execution_identity
path = Path({str(package / helper)!r})
path.write_bytes(path.read_bytes() + b"\\n# changed after import\\n")
try:
    execution_identity()
except ValueError as exc:
    assert "changed after import" in str(exc)
else:
    raise AssertionError("changed trusted helper was accepted")
'''
    _python(drift, package_root=tmp_path)
    assert _python(identity, package_root=tmp_path) != before


def test_host_lock_is_shared_across_temporary_directories(tmp_path):
    package_root = Path(mentat.__file__).parent.parent
    first, second = tmp_path / "temp-one", tmp_path / "temp-two"
    first.mkdir()
    second.mkdir()
    path_code = "from mentat.proof import _host_lock_path; print(_host_lock_path())"
    default = _python(path_code, package_root=package_root, env={"TMPDIR": None})
    assert _python(path_code, package_root=package_root, env={"TMPDIR": str(first)}) == default
    assert _python(path_code, package_root=package_root, env={"TMPDIR": str(second)}) == default

    # Preserve the complete computed lock path under a private root, avoiding the real host lock.
    lock_code = '''\
import sys
from pathlib import Path
from mentat import proof
original = proof._host_lock_path
root = Path(sys.argv[1])
def isolated_path():
    path = root / str(original()).lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
proof._host_lock_path = isolated_path
try:
    with proof._host_lock():
        print("acquired", flush=True)
        sys.stdin.readline()
except ValueError as exc:
    print(str(exc), flush=True)
'''
    env = {**os.environ, "PYTHONPATH": str(package_root), "PYTHONDONTWRITEBYTECODE": "1"}
    with subprocess.Popen(
        [sys.executable, "-c", lock_code, str(tmp_path / "locks")],
        cwd=package_root, env={**env, "TMPDIR": str(first)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as holder:
        try:
            assert holder.stdout.readline().strip() == "acquired"
            contender = subprocess.run(
                [sys.executable, "-c", lock_code, str(tmp_path / "locks")],
                cwd=package_root, env={**env, "TMPDIR": str(second)}, input="\n",
                capture_output=True, text=True, timeout=20, check=True,
            )
            assert "Another protected proof check is running" in contender.stdout
        finally:
            holder.stdin.close()
        assert holder.wait(timeout=20) == 0
