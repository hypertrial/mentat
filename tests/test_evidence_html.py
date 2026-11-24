"""Acceptance and integrity checks formerly exercised through HTML rendering."""
import json

import pytest

from mentat import evidence

RUN = "a" * 32
REV = "b" * 40
CONTRACT = "c" * 64


@pytest.fixture
def saved(tmp_path):
    state = evidence.state_directory(tmp_path / "state")
    record = {"run_id": RUN, "kind": "project-verification", "schema_version": 2,
              "status": "pass", "revision": REV, "base": "d" * 40,
              "contract_digest": CONTRACT, "runner": "host",
              "claims": [{"id": "claim", "statement": "Claim"}],
              "behavior_skipped": False, "model_checks": [], "cases": []}
    bundle = evidence.save_run(state, record, {"result.json": b"original"})
    return state, bundle


def receipt(state, **overrides):
    data = {"run_id": RUN, "revision": REV, "contract_digest": CONTRACT,
            "manifest_digest": (state / "receipts" / RUN).read_text(),
            "accepted_at": "2026-01-02T03:04:05+00:00", **overrides}
    path = state / "acceptances" / f"{RUN}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def acceptance(state):
    _, record, digest = evidence.read_run_snapshot(state, RUN)
    return evidence.acceptance_status(state, record, digest)


def test_acceptance_binds_exact_raw_manifest_bytes(saved):
    state, bundle = saved
    manifest = bundle / "manifest.json"
    manifest.write_text(json.dumps(json.loads(manifest.read_text()), indent=2) + "\n")
    (state / "receipts" / RUN).write_text(evidence.digest(manifest.read_bytes()))
    receipt(state)
    assert acceptance(state).startswith("Recorded at 2026-01-02")
    manifest.write_text(json.dumps(json.loads(manifest.read_text())) + "\n")
    (state / "receipts" / RUN).write_text(evidence.digest(manifest.read_bytes()))
    assert acceptance(state) == "Invalid or mismatched receipt"


@pytest.mark.parametrize("field", ["run_id", "revision", "contract_digest", "manifest_digest", "accepted_at"])
@pytest.mark.parametrize("value", ["invalid", 1])
def test_invalid_acceptance_bindings_are_never_accepted(saved, field, value):
    state, _ = saved
    receipt(state, **{field: value})
    assert acceptance(state) == "Invalid or mismatched receipt"


@pytest.mark.parametrize("case", ["missing", "malformed", "directory", "symlink", "nonpassing"])
def test_missing_or_unsafe_acceptance_is_not_recorded(saved, case, tmp_path):
    state, bundle = saved
    if case != "missing":
        path = receipt(state)
        if case == "malformed":
            path.write_text("{")
        elif case == "directory":
            path.unlink()
            path.mkdir()
        elif case == "symlink":
            target = tmp_path / "outside"
            target.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(target)
        else:
            manifest = bundle / "manifest.json"
            record = json.loads(manifest.read_bytes())
            record["status"] = "violation"
            manifest.write_bytes(evidence.json_bytes(record))
            (state / "receipts" / RUN).write_text(evidence.digest(manifest.read_bytes()))
            receipt(state)
    assert not acceptance(state).startswith("Recorded at")


@pytest.mark.parametrize("damage", ["manifest", "artifact", "extra", "artifact-symlink", "manifest-symlink"])
def test_tampered_bundle_fails_integrity(saved, damage, tmp_path):
    state, bundle = saved
    if damage in {"manifest", "artifact", "extra"}:
        name = {"manifest": "manifest.json", "artifact": "result.json", "extra": "extra"}[damage]
        (bundle / name).write_text("changed")
    else:
        path = bundle / ("result.json" if damage == "artifact-symlink" else "manifest.json")
        target = tmp_path / "replacement"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    with pytest.raises(ValueError):
        evidence.read_run_snapshot(state, RUN)
