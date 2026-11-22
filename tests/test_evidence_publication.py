import concurrent.futures
import json
import threading
from pathlib import Path

import pytest
from helpers import PRE_SPLIT_STATE, copy_state, reseal
from typer.testing import CliRunner

from mentat import evidence
from mentat.cli import app

RUN_ID = "a" * 32


def record(revision="b" * 40):
    return {
        "run_id": RUN_ID,
        "status": "pass",
        "revision": revision,
        "contract_digest": "c" * 64,
        "reproduce": "mentat verify",
    }


@pytest.mark.parametrize("failed_file", ["result.json", "manifest.json", RUN_ID])
def test_staging_write_failure_leaves_no_visible_run(tmp_path, monkeypatch, failed_file):
    write = evidence.write_record

    def fail(path, data):
        if path.name == failed_file:
            raise OSError("injected write failure")
        return write(path, data)

    monkeypatch.setattr(evidence, "write_record", fail)
    with pytest.raises(OSError, match="injected"):
        evidence.save_run(tmp_path, record(), {"result.json": b"data"})
    assert not (tmp_path / "runs" / RUN_ID).exists()
    with pytest.raises(FileNotFoundError):
        evidence.read_run(tmp_path, RUN_ID)
    assert not (tmp_path / "receipts" / RUN_ID).exists()


def test_failed_rename_can_retry_same_id_with_different_manifest(tmp_path, monkeypatch):
    rename = Path.rename

    def fail_bundle(path, target):
        if path.name == "bundle":
            raise OSError("injected rename failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_bundle)
    with pytest.raises(OSError, match="injected"):
        evidence.save_run(tmp_path, record(), {"result.json": b"old"})
    assert not (tmp_path / "runs" / RUN_ID).exists()
    assert (tmp_path / "receipts" / RUN_ID).exists()
    with pytest.raises(FileNotFoundError):
        evidence.read_run(tmp_path, RUN_ID)

    monkeypatch.setattr(Path, "rename", rename)
    evidence.save_run(tmp_path, record("d" * 40), {"result.json": b"new"})
    bundle, saved = evidence.read_run(tmp_path, RUN_ID)
    assert saved["revision"] == "d" * 40
    assert (bundle / "result.json").read_bytes() == b"new"


def test_reader_sees_no_run_until_bundle_is_published(tmp_path, monkeypatch):
    ready = threading.Event()
    release = threading.Event()
    rename = Path.rename

    def pause_bundle(path, target):
        if path.name == "bundle":
            ready.set()
            assert release.wait(5), "reader did not release publication"
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", pause_bundle)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(evidence.save_run, tmp_path, record(), {"result.json": b"data"})
        try:
            assert ready.wait(5), "publisher did not reach rename"
            with pytest.raises(FileNotFoundError):
                evidence.read_run(tmp_path, RUN_ID)
        finally:
            release.set()
        future.result(timeout=5)
    assert evidence.read_run(tmp_path, RUN_ID)[1]["revision"] == "b" * 40


def test_concurrent_same_id_keeps_winning_manifest_and_receipt(tmp_path, monkeypatch):
    ready = threading.Barrier(2, timeout=5)
    write = evidence.write_record

    def sync_staging(path, data):
        write(path, data)
        if path.name == "manifest.json":
            ready.wait()

    monkeypatch.setattr(evidence, "write_record", sync_staging)

    def publish(revision):
        try:
            evidence.save_run(tmp_path, record(revision), {"result.json": revision.encode()})
            return "saved", revision
        except FileExistsError:
            return "duplicate", revision

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, ["b" * 40, "d" * 40]))
    assert sorted(result[0] for result in results) == ["duplicate", "saved"]
    monkeypatch.setattr(evidence, "write_record", write)
    winner = next(revision for result, revision in results if result == "saved")
    bundle, saved = evidence.read_run(tmp_path, RUN_ID)
    assert saved["revision"] == winner
    assert (bundle / "result.json").read_bytes() == winner.encode()
    with pytest.raises(FileExistsError):
        evidence.save_run(tmp_path, record("e" * 40), {})
    assert evidence.read_run(tmp_path, RUN_ID)[1]["revision"] == winner


def test_existing_bundle_layout_remains_readable(tmp_path):
    bundle = tmp_path / "runs" / RUN_ID
    bundle.mkdir(parents=True)
    artifacts = {"result.json": b"legacy", "report.md": b"legacy report"}
    for name, data in artifacts.items():
        (bundle / name).write_bytes(data)
    manifest = evidence.json_bytes(
        {**record(), "artifacts": {name: evidence.digest(data) for name, data in artifacts.items()}}
    )
    (bundle / "manifest.json").write_bytes(manifest)
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / RUN_ID).write_text(evidence.digest(manifest))
    assert evidence.read_run(tmp_path, RUN_ID) == (
        bundle,
        {
            **record(),
            "artifacts": {name: evidence.digest(data) for name, data in artifacts.items()},
        },
    )


@pytest.mark.parametrize(
    ("checks", "normal", "incomplete_first"),
    [
        ([], "incomplete", "incomplete"),
        ([{}], "incomplete", "incomplete"),
        ([{"status": "unknown"}, {"status": "violation"}], "incomplete", "incomplete"),
        ([{"status": "pass"}], "pass", "pass"),
        ([{"status": "violation"}, {"status": "pass"}], "violation", "violation"),
        ([{"status": "incomplete"}, {"status": "pass"}], "incomplete", "incomplete"),
        ([{"status": "incomplete"}, {"status": "violation"}], "violation", "incomplete"),
    ],
)
def test_status_precedence_and_invalid_results(checks, normal, incomplete_first):
    assert evidence.status(checks) == normal
    assert evidence.status(checks, incomplete_first=True) == incomplete_first


PUBLISHED_RUN = "4354a85afc7a4316b34fc391dc76f50c"
MISSING_FIELDS = {
    "run_id": lambda record: record.pop("run_id"),
    "model_checks": lambda record: record.__setitem__("model_checks", None),
    "artifacts": lambda record: record.__setitem__("artifacts", None),
}


def _published_state(tmp_path, damage=None):
    state = copy_state(PRE_SPLIT_STATE, tmp_path / "state")
    record = json.loads((state / "runs" / PUBLISHED_RUN / "manifest.json").read_bytes())
    if damage is not None:
        MISSING_FIELDS[damage](record)
    reseal(state, PUBLISHED_RUN, record)
    return state, record["revision"]


def _summary(state, head):
    args = ["evidence", "pr-summary", PUBLISHED_RUN, "--head", head,
            "--report-url", "https://example.com/report"]
    return CliRunner().invoke(app, ["--state", str(state), *args])


def _export(state):
    return CliRunner().invoke(app, ["--state", str(state), "evidence", "export", PUBLISHED_RUN])


def test_resealed_published_run_fixture_summarizes_and_exports(tmp_path):
    state, head = _published_state(tmp_path)
    assert _summary(state, head).exit_code == 0
    assert _export(state).exit_code == 0


@pytest.mark.parametrize(
    ("command", "damage"),
    [
        ("pr-summary", "run_id"),
        ("pr-summary", "model_checks"),
        ("export", "model_checks"),
        ("export", "artifacts"),
    ],
)
def test_resealed_run_missing_required_fields_is_refused_without_traceback(
    tmp_path, command, damage
):
    state, head = _published_state(tmp_path, damage)
    result = _summary(state, head) if command == "pr-summary" else _export(state)
    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit), repr(result.exception)


def test_export_of_missing_run_does_not_create_state(tmp_path):
    state = tmp_path / "missing" / "state"
    result = CliRunner().invoke(
        app, ["--state", str(state), "evidence", "export", "a" * 32]
    )
    assert result.exit_code == 2, result.output
    assert not state.exists()
