import subprocess
from pathlib import Path

import pytest

from mentat.source_verify import verify_source


@pytest.fixture
def check_clamp(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    source = (Path(__file__).resolve().parents[1] / "examples/source-proof/clamp.py").read_text()

    def check(text):
        (tmp_path / "clamp.py").write_text(text)
        subprocess.run(["git", "-C", str(tmp_path), "add", "clamp.py"], check=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-qm", "fixture"], check=True
        )
        rev = subprocess.check_output(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
        ).strip()
        return verify_source(
            tmp_path,
            rev,
            "clamp.py",
            "clamp",
            {"value": "int", "lower": "int", "upper": "int"},
            "lower <= upper",
            "(value < lower and result == lower) or (value > upper and result == upper) or (value >= lower and value <= upper and result == value)",
        )

    return source, check


def test_early_returns_prove_real_clamp_fixture(check_clamp):
    source, check = check_clamp
    result = check(source)
    assert result["status"] == "proved", result


def test_early_return_mutant_has_counterexample(check_clamp):
    source, check = check_clamp
    result = check(source.replace("return upper", "return lower"))
    assert result["status"] == "counterexample", result


def test_early_return_missing_fallthrough_stays_incomplete(check_clamp):
    source, check = check_clamp
    result = check(source.replace("    return value\n", ""))
    assert result["status"] == "incomplete", result
    assert result["reason"] == "all paths must return"
