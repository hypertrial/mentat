#!/bin/sh
set -eu

repo=${1:?Usage: create.sh /path/to/new-demo-repository}
mkdir "$repo"
git init -q "$repo"
git -C "$repo" config user.name "Mentat Example"
git -C "$repo" config user.email "example@example.invalid"
mkdir -p "$repo/.mentat" "$repo/src/clamp" "$repo/tests/fixtures"
cat > "$repo/.mentat/contract.json" <<'JSON'
{
  "version": 1,
  "claims": [{"id": "clamp", "statement": "Clamp integers to the range 0 through 2."}],
  "formal": [{"id": "clamp-model", "claim": "clamp", "engine": "z3", "source": "clamp.smt2", "expected": "unsat", "counterexample": "counterexample.smt2"}],
  "behavior": [{"id": "clamp-tests", "claim": "clamp", "argv": ["python3", "-I", "{checks}/check.py", "{selectors}"], "selectors": ["test_clamp.py::test_clamp"]}],
  "base_files": ["tests/conftest.py", "tests/test_clamp.py", "tests/fixtures/cases.json"],
  "host_requirements": {"executables": ["python3"], "python_distributions": ["pytest"], "loopback": false},
  "allow_host": true
}
JSON
cat > "$repo/.mentat/clamp.smt2" <<'SMT'
(declare-const x Int)
(define-fun clamp ((n Int)) Int (ite (< n 0) 0 (ite (> n 2) 2 n)))
SMT
cat > "$repo/.mentat/counterexample.smt2" <<'SMT'
(assert (or (and (< x 0) (distinct (clamp x) 0))
            (and (<= 0 x) (<= x 2) (distinct (clamp x) x))
            (and (> x 2) (distinct (clamp x) 2))))
SMT
cat > "$repo/.mentat/check.py" <<'PY'
import os
from pathlib import Path
import sys

os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ.pop("PYTEST_ADDOPTS", None)
import pytest

checks = Path(os.environ["MENTAT_CHECKS_DIR"])
tests = checks / "base" / "tests"
sys.path.insert(0, str(Path(os.environ["MENTAT_CANDIDATE_DIR"]) / "src"))
selectors = [str(tests / value) for value in sys.argv[1:]]
raise SystemExit(pytest.main([
    "-c", os.devnull, "--rootdir", str(tests), "--confcutdir", str(tests),
    "--import-mode=importlib", "-p", "no:cacheprovider", *selectors,
]))
PY
cat > "$repo/tests/conftest.py" <<'PY'
import json
from pathlib import Path

import pytest


@pytest.fixture
def cases():
    return json.loads((Path(__file__).parent / "fixtures" / "cases.json").read_text())
PY
cat > "$repo/tests/fixtures/cases.json" <<'JSON'
[[-3, 0], [0, 0], [1, 1], [2, 2], [3, 2]]
JSON
cat > "$repo/tests/test_clamp.py" <<'PY'
from clamp import clamp


def test_clamp(cases):
    for value, expected in cases:
        assert clamp(value) == expected
PY
cat > "$repo/src/clamp/__init__.py" <<'PY'
def clamp(value):
    return min(2, max(0, value))
PY
git -C "$repo" add .
git -C "$repo" -c commit.gpgsign=false commit -qm "Add contract and working clamp"
git -C "$repo" -c tag.gpgSign=false tag mentat-demo-base
cat > "$repo/src/clamp/__init__.py" <<'PY'
def clamp(value):
    return min(2, value)
PY
cat > "$repo/tests/test_clamp.py" <<'PY'
from clamp import clamp


def test_clamp(cases):
    assert clamp(1) == 1
PY
git -C "$repo" add src tests
git -C "$repo" -c commit.gpgsign=false commit -qm "Introduce bug and weaken candidate test"
git -C "$repo" -c tag.gpgSign=false tag mentat-demo-bad
