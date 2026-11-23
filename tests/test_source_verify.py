import subprocess

import pytest

from mentat.source_verify import verify_source


@pytest.fixture
def repository(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)

    def commit(source):
        (tmp_path / "candidate.py").write_text(source)
        subprocess.run(["git", "-C", str(tmp_path), "add", "candidate.py"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
        ).strip()

    return tmp_path, commit


def verify(repo, rev, ensures="result == x + 1", requires="True", inputs=None, path="candidate.py"):
    return verify_source(
        repo, rev, path, "f", {"x": "int"} if inputs is None else inputs, requires, ensures
    )


def test_proof_counterexample_and_committed_revision(repository):
    repo, commit = repository
    old = commit("def f(x):\n    return x + 1\n")
    new = commit("def f(x):\n    return x - 1\n")
    result = verify(repo, old)
    assert result["status"] == "proved", result
    assert result["source_identity"]["revision"] == old
    result = verify(repo, new)
    assert result["status"] == "counterexample", result
    assert type(result["counterexample"]["x"]) is int
    assert verify(repo, "HEAD")["status"] == "incomplete"


def test_branch_and_strings(repository):
    repo, commit = repository
    rev = commit(
        'def f(x):\n    if x:\n        return "survived"\n    else:\n        return "died"\n'
    )
    result = verify(
        repo,
        rev,
        '(x and result == "survived") or (not x and result == "died")',
        inputs={"x": "bool"},
    )
    assert result["status"] == "proved", result


@pytest.mark.parametrize(
    "source,requires,ensures",
    [
        (
            "def f(x):\n    if x > 0:\n        return x\n    else:\n        x + 1\n",
            "True",
            "result == x",
        ),
        ("def f(x):\n    return x + True\n", "True", "True"),
        ("def f(x):\n    return dangerous(x)\n", "True", "True"),
        ("def f(x):\n    return x\n", "__import__('os')", "True"),
        ("def f(x):\n    return x\n", "result == x", "True"),
        ("def f(x):\n    return x\n", "x > 0 and x < 0", "True"),
        ("def f(x):\n    return x\n", "True", "x.__class__ == result"),
        ("def f(x):\n    return x\n", "True", "x == True"),
    ],
)
def test_rejects_unsupported_or_vacuous(repository, source, requires, ensures):
    repo, commit = repository
    result = verify(repo, commit(source), ensures, requires)
    assert result["status"] == "incomplete", result
    assert result.get("reason")


def test_rejects_paths_symlinks_and_limits(repository):
    repo, commit = repository
    rev = commit("def f(x):\n    return x\n")
    assert verify(repo, rev, path="../candidate.py")["status"] == "incomplete"
    assert verify(repo, rev, requires="True " * 1000)["status"] == "incomplete"
    (repo / "link.py").symlink_to("candidate.py")
    subprocess.run(["git", "-C", str(repo), "add", "link.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "link"], check=True)
    rev = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    assert verify(repo, rev, path="link.py")["status"] == "incomplete"


@pytest.mark.parametrize("literal", [r"'\\u{61}'", r"'\x00'", r"'\ud800'", "'é'"])
def test_unsafe_string_literals_rejected_in_source_and_contract(repository, literal):
    repo, commit = repository
    rev = commit(f"def f(x):\n    return {literal}\n")
    result = verify(repo, rev, "result == 'a'")
    assert result["status"] == "incomplete", result
    rev = commit("def f(x):\n    return 'a'\n")
    result = verify(repo, rev, f"result == {literal}")
    assert result["status"] == "incomplete", result


def test_unreachable_yield_and_call_rejected(repository):
    repo, commit = repository
    for tail in ["yield 1", "dangerous()"]:
        rev = commit(f"def f(x):\n    return x\n    {tail}\n")
        assert verify(repo, rev, "result == x")["status"] == "incomplete"


def test_worker_ignores_cwd_and_pythonpath(repository, monkeypatch, tmp_path):
    repo, commit = repository
    rev = commit("def f(x):\n    return x + 1\n")
    trap = tmp_path / "z3.py"
    trap.write_text("raise RuntimeError('shadowed')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    result = verify(repo, rev)
    assert result["status"] == "proved", result


def test_worker_cannot_replace_evidence():
    from mentat import source_verify

    with pytest.raises(source_verify.Unsupported):
        source_verify._validate_worker_result(
            {"status": "proved", "source_identity": {}}, {"x": "int"}
        )


def test_rejects_contextually_invalid_module(repository):
    repo, commit = repository
    rev = commit("def f(x):\n    return x\nbreak\n")
    result = verify(repo, rev, "result == x")
    assert result["status"] == "incomplete", result
    assert result["reason"] == "source cannot be compiled"
