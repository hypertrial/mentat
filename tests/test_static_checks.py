"""Type-check the core package with pyright (basic mode, configured in pyproject.toml).

The `pyright` wheel bundles its Node engine, and the `nodejs` extra supplies Node, so the check
runs offline after `uv sync`. One test runs one pyright process, so parallel workers do not repeat it.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 180


def test_pyright_reports_no_errors_in_core_package():
    command = [
        sys.executable,
        "-m",
        "pyright",
        "--project",
        str(ROOT / "pyproject.toml"),
        "--pythonpath",
        sys.executable,
    ]
    # Skips the wrapper's PyPI version lookup, which would otherwise make a network call.
    env = {**os.environ, "PYRIGHT_PYTHON_IGNORE_WARNINGS": "1"}
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"pyright did not finish within {TIMEOUT_SECONDS} seconds") from exc
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"pyright exited with {result.returncode}:\n{output}"
