import subprocess

import pytest

from mentat import source_verify


def test_identity_matches_real_source_proof(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "candidate.py").write_text("def f(x):\n    return x + 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "candidate.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    rev = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    proof = source_verify.verify_source(
        tmp_path, rev, "candidate.py", "f", {"x": "int"}, "True", "result == x + 1"
    )
    assert proof["status"] == "proved", proof
    identity = source_verify.source_tool_identity()
    assert set(identity) == {
        "frontend_sha256",
        "python_version",
        "python_implementation",
        "solver_version",
        "solver_binding_sha256",
        "solver_library_sha256",
    }
    assert identity == {key: proof[key] for key in identity}
    assert {**identity, "frontend_sha256": "0" * 64} != identity


def test_identity_ignores_cwd_and_pythonpath(tmp_path, monkeypatch):
    marker = tmp_path / "executed"
    (tmp_path / "z3.py").write_text(f"open({str(marker)!r}, 'w').write('executed')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    identity = source_verify.source_tool_identity()
    assert identity["solver_version"]
    assert not marker.exists()


def test_identity_rejects_bad_worker_response(monkeypatch):
    monkeypatch.setattr(
        source_verify.process, "run", lambda *args, **kwargs: (0, b'{"solver_version": 7}', b"")
    )
    with pytest.raises(ValueError):
        source_verify.source_tool_identity()


def test_binding_digest_covers_high_level_package_code(tmp_path):
    package = tmp_path / "z3"
    package.mkdir()
    (package / "__init__.py").write_text("from .z3 import *\n")
    (package / "z3core.py").write_text("core = 1\n")
    high_level = package / "z3.py"
    high_level.write_text("semantics = 1\n")
    before = source_verify._binding_digest(package)
    high_level.write_text("semantics = 2\n")
    after = source_verify._binding_digest(package)
    assert before != after


def test_identity_worker_ignores_stale_bytecode(tmp_path, monkeypatch):
    import os
    import py_compile

    package = tmp_path / "mentat"
    package.mkdir()
    module = package / "source_verify.py"
    template = (
        "import json\n"
        "def _identity_main():\n"
        ' print(json.dumps({"python_version":"1", "python_implementation":"CPython", '
        '"solver_version":"VERSION", "solver_binding_sha256":"a"*64, '
        '"solver_library_sha256":"b"*64}))\n'
    )
    timestamp = 1700000000
    module.write_text(template.replace("VERSION", "1"))
    os.utime(module, (timestamp, timestamp))
    py_compile.compile(str(module), doraise=True)
    module.write_text(template.replace("VERSION", "2"))
    os.utime(module, (timestamp, timestamp))
    monkeypatch.setattr(source_verify, "__file__", str(module))
    assert source_verify.source_tool_identity()["solver_version"] == "2"
