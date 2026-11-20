import subprocess
from pathlib import Path

import pytest

CONTRACT = Path(__file__).resolve().parents[1] / "examples/iceberg-go/contract"


def test_z3_preservation_and_missing_whole_file_assumption():
    z3 = pytest.importorskip("z3")
    model = (CONTRACT / "ideal.smt2").read_text()
    counterexample = (CONTRACT / "ideal-counterexample.smt2").read_text()
    assumption = "(assert (= selected source))"
    assert model.count(assumption) == 1

    def solve(source):
        solver = z3.Solver()
        solver.add(z3.parse_smt2_string(source))
        return str(solver.check())

    assert solve(model + "\n(assert (> source 0))") == "sat"
    assert solve(model + "\n" + counterexample) == "unsat"
    assert solve(model.replace(assumption, "") + "\n" + counterexample) == "sat"


@pytest.mark.xdist_group("lean")
def test_lean_model_compiles_without_sorry(tmp_path, lean_tools):
    lean = lean_tools["lean"]["executable"]
    model = (CONTRACT / "Model.lean").read_text()
    audit = tmp_path / "Audit.lean"
    audit.write_text(model + "\n#print axioms IcebergCompaction.rows_preserved\n")
    result = subprocess.run([lean, str(audit)], text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sorryAx" not in result.stdout + result.stderr
    assert "rows_preserved" in result.stdout
