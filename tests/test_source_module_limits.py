import subprocess

import pytest

from mentat.source_verify import verify_source


@pytest.fixture
def prove(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)

    def run(source, ensures="result == x + 1"):
        (tmp_path / "candidate.py").write_text(source)
        subprocess.run(["git", "-C", str(tmp_path), "add", "candidate.py"], check=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-qm", "fixture"], check=True
        )
        rev = subprocess.check_output(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
        ).strip()
        return verify_source(tmp_path, rev, "candidate.py", "f", {"x": "int"}, "True", ensures)

    return run


def test_large_unrelated_module_does_not_block_small_function(prove):
    source = "HTML = " + repr("<div>\\n" * 1000) + "\n"
    source += "".join(f"def helper_{i}():\n    return {i}\n" for i in range(450))
    source += "def f(x):\n    return x + 1\n"
    result = prove(source)
    assert result["status"] == "proved", result


def test_selected_function_and_predicate_limits_remain_enforced(prove):
    result = prove("def f(x):\n    return " + repr("a" * 4097) + "\n")
    assert result["status"] == "incomplete"
    assert "string literal limit" in result["reason"]
    result = prove("def f(x):\n    return x + 1\n", "True " * 1000)
    assert result["status"] == "incomplete"
    assert "predicate size limit" in result["reason"]


def test_selected_function_and_predicate_ast_limits_remain_enforced(prove):
    source = "def f(x):\n" + "    x = x + 1\n" * 300 + "    return x\n"
    result = prove(source)
    assert result["status"] == "incomplete"
    assert "AST size or depth limit" in result["reason"]
    result = prove("def f(x):\n    return x + 1\n", " or ".join(["x == 0"] * 300))
    assert result["status"] == "incomplete"
    assert "AST size or depth limit" in result["reason"]


def test_invalid_unrelated_module_still_rejected(prove):
    result = prove("break\ndef f(x):\n    return x + 1\n")
    assert result["status"] == "incomplete"
    assert "cannot be compiled" in result["reason"]
