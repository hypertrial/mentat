import json
from types import SimpleNamespace

import pytest

from mentat import evidence, project_formal


@pytest.fixture(scope="session")
def lean_tools():
    project = SimpleNamespace(contract=SimpleNamespace(formal=[SimpleNamespace(engine="lean")]))
    try:
        return project_formal.tool_info(project)
    except ValueError as exc:
        pytest.skip(str(exc))


@pytest.fixture(autouse=True)
def canonical_saved_manifests(monkeypatch):
    """Check every real save: canonical manifest bytes and a receipt that matches them."""
    save_run = evidence.save_run

    def checked_save_run(state, record, artifacts):
        destination = save_run(state, record, artifacts)
        raw = (destination / "manifest.json").read_bytes()
        assert evidence.json_bytes(json.loads(raw)) == raw, "saved manifest is not canonical JSON"
        receipt = (state / "receipts" / record["run_id"]).read_bytes()
        assert receipt == evidence.digest(raw).encode(), "receipt does not match the manifest"
        return destination

    monkeypatch.setattr(evidence, "save_run", checked_save_run)
