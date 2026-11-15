import concurrent.futures
import threading
from pathlib import Path

import pytest

from mentat import evidence

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
