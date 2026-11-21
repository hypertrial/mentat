"""Type-check the core package with pyright (basic mode, configured in pyproject.toml).

The `pyright` wheel bundles its Node engine, and the `nodejs` extra supplies Node, so the check
runs offline after `uv sync`. One test runs one pyright process, so parallel workers do not repeat it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from mentat import process

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 180
OUTPUT_LIMIT = 16 * 1024 * 1024


def _describe(diagnostic):
    start = diagnostic["range"]["start"]
    rule = f" ({diagnostic['rule']})" if diagnostic.get("rule") else ""
    return (
        f"{diagnostic['file']}:{start['line'] + 1}:{start['character'] + 1} - "
        f"{diagnostic['severity']}: {diagnostic['message']}{rule}"
    )


def test_pyright_reports_no_errors_in_core_package():
    command = [
        sys.executable,
        "-m",
        "pyright",
        "--outputjson",
        "--project",
        str(ROOT / "pyproject.toml"),
        "--pythonpath",
        sys.executable,
    ]
    # Inherited PYRIGHT_PYTHON_* settings could redirect the engine to a download, so drop them.
    # IGNORE_WARNINGS skips the wrapper's PyPI version lookup.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYRIGHT_PYTHON_")}
    env["PYRIGHT_PYTHON_IGNORE_WARNINGS"] = "1"
    try:
        code, stdout, stderr = process.run(
            command, output_limit=OUTPUT_LIMIT, timeout=TIMEOUT_SECONDS, cwd=ROOT, env=env
        )
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        raise AssertionError(f"pyright did not finish within {TIMEOUT_SECONDS} seconds") from exc
    output = (stdout + stderr).decode("utf-8", "replace")
    try:
        report = json.loads(stdout)
    except ValueError as exc:
        raise AssertionError(f"pyright printed no JSON report (exit {code}):\n{output}") from exc
    summary = report["summary"]
    diagnostics = "\n".join(_describe(item) for item in report["generalDiagnostics"])
    assert summary["filesAnalyzed"] > 0, f"pyright analyzed no files:\n{output}"
    assert code == 0 and summary["errorCount"] == 0, (
        f"pyright reported {summary['errorCount']} errors (exit {code}):\n{diagnostics}\n{output}"
    )
