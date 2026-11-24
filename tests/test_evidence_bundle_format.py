"""Compatibility and integrity of machine and historical evidence bundles."""
import json

import pytest
from typer.testing import CliRunner

from mentat import evidence
from mentat.cli import app

RUN = "a" * 32


@pytest.fixture
def bundle(tmp_path):
    state = evidence.state_directory(tmp_path / "state")
    path = evidence.save_run(state, {"run_id": RUN, "kind": "verification", "status": "pass"}, {"log.txt": b"evidence"})
    return state, path


def rewrite(state, bundle, mutate, *, reseal=True):
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_bytes())
    mutate(manifest)
    path.write_bytes(evidence.json_bytes(manifest))
    if reseal:
        (state / "receipts" / RUN).write_text(evidence.digest(path.read_bytes()))


@pytest.mark.parametrize("kind", ["project-verification", "verification", "evaluation"])
def test_new_format_is_machine_only_for_all_run_kinds(tmp_path, kind):
    state = evidence.state_directory(tmp_path / "state")
    record = {"run_id": RUN, "kind": kind, "status": "pass"}
    if kind == "project-verification":
        record.update(schema_version=2, revision="b" * 40, base="d" * 40,
                      contract_digest="c" * 64, runner="host",
                      claims=[{"id": "claim", "statement": "Claim"}],
                      behavior_skipped=False, model_checks=[], cases=[])
    bundle = evidence.save_run(state, record, {"data": b"x"})
    _, record, _ = evidence.read_run_snapshot(state, RUN)
    assert record["bundle_format"] == 2
    assert record["artifacts"] == {"data": evidence.digest(b"x")}
    assert not (bundle / "report.md").exists()
    result = CliRunner().invoke(app, ["--state", str(state), "evidence", "export", RUN, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["manifest"] == str(bundle / "manifest.json")
    assert "report" not in json.loads(result.output)


@pytest.mark.parametrize("marker", [None, True, False, 1, 3, "2", 2.0])
def test_unknown_or_noninteger_marker_is_refused_even_with_recomputed_receipt(bundle, marker):
    state, path = bundle
    rewrite(state, path, lambda record: record.__setitem__("bundle_format", marker))
    with pytest.raises(ValueError):
        evidence.read_run_snapshot(state, RUN)
    result = CliRunner().invoke(app, ["--state", str(state), "evidence", "export", RUN, "--json"])
    assert result.exit_code == 2


def test_marker_tampering_invalidates_original_receipt(bundle):
    state, path = bundle
    rewrite(state, path, lambda record: record.pop("bundle_format"), reseal=False)
    with pytest.raises(ValueError, match="integrity"):
        evidence.read_run_snapshot(state, RUN)


def test_absent_marker_requires_verified_legacy_report(bundle):
    state, path = bundle
    rewrite(state, path, lambda record: record.pop("bundle_format"))
    with pytest.raises(ValueError, match="Legacy"):
        evidence.read_run_snapshot(state, RUN)
    (path / "report.md").write_bytes(b"historical report")
    rewrite(state, path, lambda record: record["artifacts"].__setitem__("report.md", evidence.digest(b"historical report")))
    assert "bundle_format" not in evidence.read_run_snapshot(state, RUN)[1]
    (path / "report.md").write_bytes(b"tampered")
    with pytest.raises(ValueError):
        evidence.read_run_snapshot(state, RUN)


@pytest.mark.parametrize("reserved", ["bundle_format", "artifacts", "manifest.json", "report.md"])
@pytest.mark.parametrize("location", ["record", "artifact"])
def test_writer_rejects_reserved_names_without_publishing(tmp_path, reserved, location):
    state = evidence.state_directory(tmp_path / "state")
    record = {"run_id": RUN, "kind": "verification", "status": "pass"}
    artifacts = {}
    if location == "record":
        record[reserved] = "collision"
    else:
        artifacts[reserved] = b"collision"
    with pytest.raises(ValueError):
        evidence.save_run(state, record, artifacts)
    assert not (state / "runs" / RUN).exists()
    assert not (state / "receipts" / RUN).exists()


def test_existing_run_cannot_be_overwritten(bundle):
    state, path = bundle
    original = (path / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        evidence.save_run(state, {"run_id": RUN, "kind": "evaluation", "status": "pass"}, {})
    assert (path / "manifest.json").read_bytes() == original
    evidence.read_run_snapshot(state, RUN)
