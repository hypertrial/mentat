from types import SimpleNamespace

import pytest

from mentat import canonical, evidence, evidence_report, project_formal, records


@pytest.fixture(scope="session")
def lean_tools():
    project = SimpleNamespace(contract=SimpleNamespace(formal=[SimpleNamespace(engine="lean")]))
    try:
        return project_formal.tool_info(project)
    except ValueError as exc:
        pytest.skip(str(exc))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "saves_invalid_run: the test saves a run record that is deliberately invalid",
    )


@pytest.fixture(autouse=True)
def canonical_saved_manifests(request, monkeypatch):
    """Check every real save: canonical bytes, a matching receipt, and that the run validates.

    A saved project-verification run must pass the run record model, the verifier, and the
    report's claim grouping. A test that saves a deliberately invalid run opts out with
    @pytest.mark.saves_invalid_run.
    """
    save_run = evidence.save_run
    validated = request.node.get_closest_marker("saves_invalid_run") is None

    def checked_save_run(state, record, artifacts):
        destination = save_run(state, record, artifacts)
        raw = (destination / "manifest.json").read_bytes()
        saved = canonical.decode_json(raw, limit=None)
        assert evidence.json_bytes(saved) == raw, "saved manifest is not canonical JSON"
        receipt = (state / "receipts" / record["run_id"]).read_bytes()
        assert receipt == evidence.digest(raw).encode(), "receipt does not match the manifest"
        if saved.get("kind") == "project-verification":
            records.check(records.RunRecord, saved)
            if validated:
                evidence.validate_run_record(saved)
                evidence_report.claim_groups(saved)
        return destination

    monkeypatch.setattr(evidence, "save_run", checked_save_run)
