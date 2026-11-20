import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _cases():
    # Read the approved contract so every declared behavior check is exercised.
    contract = json.loads((ROOT / ".mentat/contract.json").read_text())
    for check in contract["behavior"]:
        if "{selectors}" in check["argv"]:
            yield from check["selectors"]
        else:
            yield check["argv"][-1]


@pytest.mark.parametrize("case", sorted(set(_cases())))
def test_selfcheck_case_passes_against_working_tree(case, tmp_path):
    # Like a host behavior check: a clean environment, so the operator's Git
    # configuration cannot change the result. -B keeps bytecode out of the tree.
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(ROOT / ".mentat/selfcheck.py"), case],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "MENTAT_CANDIDATE_DIR": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-4000:]
