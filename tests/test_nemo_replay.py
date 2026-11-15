import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPLAY = Path(__file__).resolve().parents[1] / "examples/nemo/contract/replay.py"


def case(name="sample", grouped=False, groups=None):
    return {
        "name": name,
        "grouped": grouped,
        "groups": groups or [{"id": 0, "values": [{"type": "integer", "half_units": 2}]}],
    }


def replay(tmp_path, cases, responses):
    fixture = tmp_path / "cases.json"
    fixture.write_text(json.dumps(cases))
    fake = tmp_path / "nmo"
    fake_script = tmp_path / "fake_nmo.py"
    fake_script.write_text(
        "import os, sys\n"
        f"responses = {responses!r}\n"
        "output, code = responses[os.path.basename(os.getcwd())]\n"
        "sys.stdout.write(output)\n"
        "sys.exit(code)\n"
    )
    fake.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fake_script))} \"$@\"\n")
    fake.chmod(0o755)
    env = os.environ.copy()
    env.pop("MENTAT_ARTIFACTS_DIR", None)
    proc = subprocess.run(
        [sys.executable, str(REPLAY), "--nmo", str(fake), "--cases", str(fixture)],
        capture_output=True, text=True, env=env, check=False,
    )
    return proc.returncode, json.loads(proc.stdout)


@pytest.mark.parametrize(
    "output",
    [
        'out("1"^^<http://www.w3.org/2001/XMLSchema#double>).\n',
        "",
        "out(1).\nout(1).\n",
    ],
    ids=["wrong-type", "missing-total", "duplicate-total"],
)
def test_wrong_type_missing_or_duplicate_total_is_violation(tmp_path, output):
    code, result = replay(tmp_path, [case()], {"sample": (output, 0)})
    assert code == 1
    assert result["status"] == result["results"][0]["status"] == "violation"


def test_group_totals_match_independent_of_output_order(tmp_path):
    groups = [
        {"id": 0, "values": [{"type": "integer", "half_units": 2}]},
        {"id": 99, "values": [{"type": "integer", "half_units": 4}]},
    ]
    code, result = replay(
        tmp_path, [case(grouped=True, groups=groups)],
        {"sample": ("out(99, 2).\nout(0, 1).\n", 0)},
    )
    assert code == 0
    assert result["status"] == result["results"][0]["status"] == "pass"


@pytest.mark.parametrize(
    ("output", "exit_code"), [("out(1).\n", 1), ("out(not-a-number).\n", 0)]
)
def test_failed_child_or_malformed_output_is_incomplete(tmp_path, output, exit_code):
    code, result = replay(tmp_path, [case()], {"sample": (output, exit_code)})
    assert code == 2
    assert result["status"] == result["results"][0]["status"] == "incomplete"


def test_incomplete_takes_precedence_over_violation(tmp_path):
    code, result = replay(
        tmp_path, [case("bad-total"), case("failed-child")],
        {"bad-total": ("out(2).\n", 0), "failed-child": ("", 1)},
    )
    assert code == 2
    assert result["status"] == "incomplete"
    assert [item["status"] for item in result["results"]] == ["violation", "incomplete"]
