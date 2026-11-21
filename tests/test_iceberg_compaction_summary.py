import copy
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from mentat import evidence

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples/iceberg-go/summarize.py"
BEFORE = "410b789ed781852bbb4376e4f5cb57e76da12c56"
FIXED = "3fb910983b9674efc6092418af90a1c2b821dd42"
PRIMARY = "split=true/partial=true/filtered=false"
CASES = {f"split={s}/partial={p}/filtered={f}" for s in ("false", "true") for p in ("false", "true") for f in ("false", "true")}
ROWS = [[n, "a"] for n in range(1, 13)]


def row_dicts(rows):
    return [{"id": row[0], "data": row[1]} for row in rows]


def save_pair(tmp_path, mutate=None):
    state = evidence.state_directory(tmp_path / "state")
    ids = ("a" * 32, "b" * 32)
    for index, run_id in enumerate(ids):
        old = index == 0
        outcome = "violation" if old else "pass"
        cases = {name: "fail" if old and name == PRIMARY else "pass" for name in CASES}
        witness = {"schema_version": 1, "cases": {
            name: {"expected_rows": ROWS, "committed_rows": [ROWS],
                   "actual_rows": ROWS[:1] if old and name == PRIMARY else ROWS,
                   "operation_failed": False, "status": cases[name]}
            for name in CASES}}
        other = sorted(CASES - {PRIMARY})[0]
        errors = {PRIMARY: "primary operation failed", other: "other operation failed"}
        for name in errors:
            witness["cases"][name]["operation_failed"] = True
        report = {"schema_version": 1, "cases": cases}
        control_cases = {name: "fail" if name == PRIMARY else "pass" for name in CASES}
        control_witness = copy.deepcopy(witness)
        control_witness["cases"][PRIMARY]["actual_rows"] = ROWS[:1]
        control_witness["cases"][PRIMARY]["status"] = "fail"
        control = {"status": "violation", "command_status": "violation", "exit_code": 10,
                   "qualified": True, "detail": "control", "case_results": control_cases,
                   "artifacts": {"case-report.json": "control-report.json", "witness.json": "control-witness.json"}}
        check = {"id": "compaction-rows", "claim": "compaction.rows", "status": outcome,
                 "command_status": outcome, "exit_code": 10 if old else 0, "detail": outcome,
                 "required_cases": sorted(CASES), "case_results": cases,
                 "artifacts": {"case-report.json": "cases.json", "witness.json": "witness.json",
                               "native-observations.json": "native.json"}}
        if not old:
            check["negative_control"] = control
        models = [{"id": name, "claim": "compaction.rows", "kind": kind, "status": "pass", "detail": "pass"}
                  for name, kind in (("row-preservation-z3", "z3"), ("row-preservation-lean", "lean"))]
        record = {"schema_version": 2, "kind": "project-verification", "run_id": run_id,
                  "revision": BEFORE if old else FIXED, "repo": "/repo", "base": "c" * 40,
                  "runner": "host", "contract_digest": "d" * 64, "status": outcome,
                  "claims": [{"id": "compaction.rows", "statement": "Preserve rows"}],
                  "model_checks": models, "behavior_checks": [{"id": "compaction-rows", "claim": "compaction.rows"}],
                  "cases": [{"case_id": "compaction-rows", "status": outcome, "checks": [check]}],
                  "behavior_skipped": False, "runtime": {"runner": "host"}, "tools": {}, "reproduce": "mentat verify"}
        raw_cases = {}
        for name, item in witness["cases"].items():
            raw_cases[name] = {"initial_rows": row_dicts(ROWS), "committed_rows": [row_dicts(rows) for rows in item["committed_rows"]],
                               "final_rows": row_dicts(item["actual_rows"]), "operation_error": errors.get(name, "")}
        raw = {"error": None, "output": "raw Go output", "observations": {"schema_version": 1, "go_version": "go1.26.3", "cases": raw_cases}}
        docs = {"cases.json": report, "witness.json": witness, "native.json": raw,
                "control-report.json": {"schema_version": 1, "cases": control_cases},
                "control-witness.json": control_witness}
        if mutate:
            mutate(old, record, docs)
        evidence.save_run(state, record, {name: json.dumps(value).encode() for name, value in docs.items()})
    return state, ids


def run_summary(state, ids, out):
    return subprocess.run([sys.executable, str(SCRIPT), "--state", str(state), "--before", ids[0],
                           "--fixed", ids[1], "--out", str(out)], capture_output=True, text=True)


def test_summary_exports_receipt_validated_bundles_and_hashes(tmp_path):
    state, ids = save_pair(tmp_path)
    out = tmp_path / "summary with spaces"
    result = run_summary(state, ids, out)
    assert result.returncode == 0, result.stderr
    summary_file = out / "iceberg-go-compaction-summary.json"
    bundle_file = out / "iceberg-go-compaction-evidence.tar.gz"
    summary = json.loads(summary_file.read_text())
    assert summary["before"]["rows"] == ROWS[:1] and summary["fixed"]["rows"] == ROWS
    other = sorted(CASES - {PRIMARY})[0]
    for side in ("before", "fixed"):
        assert summary[side]["operation_error"] == "primary operation failed"
        assert summary[side]["operation_errors"] == {
            PRIMARY: "primary operation failed", other: "other operation failed"
        }
    assert summary["evidence"]["bundle_sha256"] == hashlib.sha256(bundle_file.read_bytes()).hexdigest()
    for side, run_id in zip(("before", "fixed"), ids, strict=False):
        original = state / "runs" / run_id
        assert {p.name for p in (out / side).iterdir()} == {p.name for p in original.iterdir()}
        for file in original.iterdir():
            assert (out / side / file.name).read_bytes() == file.read_bytes()
    with tarfile.open(bundle_file) as archive:
        for name in ("LICENSE", "NOTICE"):
            assert archive.extractfile(name).read() == (ROOT / "examples/iceberg-go" / name).read_bytes()
        for side in ("before", "fixed"):
            for file in (out / side).iterdir():
                assert archive.extractfile(f"{side}/{file.name}").read() == file.read_bytes()
    for line in (out / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ")
        assert digest == hashlib.sha256((out / name).read_bytes()).hexdigest()


def test_summary_rejects_existing_and_symlink_destinations(tmp_path):
    state, ids = save_pair(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "marker"
    marker.write_text("preserve")
    link = tmp_path / "dangling"
    link.symlink_to(tmp_path / "missing")
    for dest in (existing, link):
        assert run_summary(state, ids, dest).returncode != 0
    assert marker.read_text() == "preserve" and link.is_symlink()


@pytest.mark.parametrize("problem", ["revision", "formal", "missing-case", "witness-mismatch", "control", "control-extra-change", "raw-mismatch", "same-count-substitution"])
@pytest.mark.saves_invalid_run
def test_summary_rejects_invalid_but_receipt_valid_results(tmp_path, problem):
    def mutate(old, record, docs):
        check = record["cases"][0]["checks"][0]
        if problem == "revision" and old:
            record["revision"] = "e" * 40
        elif problem == "formal" and old:
            record["model_checks"][0]["status"] = "violation"
        elif problem == "missing-case" and old:
            check["required_cases"].pop()
        elif problem == "witness-mismatch" and old:
            docs["witness.json"]["cases"][PRIMARY]["actual_rows"] = ROWS
        elif problem == "raw-mismatch" and not old:
            docs["native.json"]["observations"]["cases"][PRIMARY]["operation_error"] = ""
        elif problem == "same-count-substitution" and not old:
            docs["witness.json"]["cases"][PRIMARY]["actual_rows"] = ROWS[:-1] + [ROWS[0]]
        elif problem == "control" and not old:
            check["negative_control"]["qualified"] = False
        elif problem == "control-extra-change" and not old:
            other = next(name for name in CASES if not docs["control-witness.json"]["cases"][name]["operation_failed"])
            docs["control-witness.json"]["cases"][other]["operation_failed"] = True
    state, ids = save_pair(tmp_path, mutate)
    out = tmp_path / "out"
    result = run_summary(state, ids, out)
    assert result.returncode != 0
    assert not out.exists()


def test_summary_rejects_artifact_tampering(tmp_path):
    state, ids = save_pair(tmp_path)
    (state / "runs" / ids[0] / "witness.json").write_text("{}")
    out = tmp_path / "out"
    result = run_summary(state, ids, out)
    assert result.returncode != 0
    assert not out.exists()
