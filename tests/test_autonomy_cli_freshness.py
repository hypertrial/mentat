"""Freshness of saved assurance as observed through the status command."""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from mentat.autonomy import current_tool_identity, digest, state_path
from mentat.autonomy_state import Store
from mentat.cli import app
from mentat.source_verify import source_tool_identity


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def saved_session(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "src.py").write_text("def f(x):\n    return x\n")
    git(repo, "add", "src.py")
    git(repo, "commit", "-qm", "base")
    head = git(repo, "rev-parse", "HEAD")
    identity = source_tool_identity()
    contract = {"path": "src.py", "function": "f", "inputs": {"x": "int"},
                "requires": "True", "ensures": "result == x"}
    claim = {"id": "c", "formal_contract": contract, "status": "unattempted"}
    evidence = {
        "id": "e", "claim_id": "c", "method": "source-proof", "status": "proved", "freshness": "current",
        "precondition": "sat", "violation": "unsat", "contract_sha256": "contract",
        "contract": {key: contract[key] for key in ("inputs", "requires", "ensures")},
        "source_identity": {"revision": head, "path": "src.py", "function": "f",
                            "blob_oid": "blob", "source_sha256": "source"},
        "assumptions": ["trusted solver"], **identity,
    }
    state_root = tmp_path / "state"
    store = Store(state_path(repo, state_root))
    policy = {"schema_version": 1, "mandatory_local_checks": [["check"]],
              "mandatory_worker_checks": [], "model": "test"}
    (store.path.parent / "policy.json").write_text(json.dumps(policy))

    def create(*, proof_revision=head, session_repo=repo, pin_authority=True, tool_identity=None, evidence_changes=None):
        session = store.new_session(str(session_repo), head, head,
                                    {"source_checks": [{**contract, "claim_id": "c"}]}, 1, 1000)
        store.append_event(session, "toolchain_pinned", {"identity": tool_identity or current_tool_identity()})
        if pin_authority:
            store.append_event(session, "authority_pinned", {"digest": digest(policy)})
        store.put_record(session, "requirement", {"id": "r", "area": "product", "text": "works"})
        store.put_record(session, "claim", claim)
        proof = {**evidence, "source_identity": {**evidence["source_identity"], "revision": proof_revision}}
        if evidence_changes:
            for key, value in evidence_changes.items():
                if value is None:
                    proof.pop(key, None)
                else:
                    proof[key] = value
        store.put_record(session, "evidence", proof)
        store.append_event(session, "fas_reviewed", {"head": "fas", "claims": {"c": digest(claim)}})
        store.append_event(session, "claim_source_scope", {"head": proof_revision, "fas_head": "fas", "evidence_id": "e"})
        return session

    def status(session):
        result = CliRunner().invoke(app, ["--state", str(state_root), "status", "--repo", str(repo),
                                          "--session", session, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    return repo, store, create, status


def test_status_changes_when_saved_tree_changes_without_mutating_store(saved_session):
    repo, store, create, status = saved_session
    session = create()
    before = store.snapshot(session)
    current = status(session)
    assert current["session"]["status"] == before["session"]["status"]
    assert current["claims"][0]["status"] == "established-within-scope"
    assert current["claims"][0].get("freshness") != "stale"

    (repo / "src.py").write_text("def f(x):\n    return x + 1\n")
    dirty = status(session)
    assert dirty["session"]["status"] == before["session"]["status"]
    assert dirty["freshness"]["status"] in ("stale", "unknown")
    for group in ("requirements", "claims", "evidence"):
        assert dirty[group][0]["freshness"] == "stale"
    assert dirty["claims"][0]["status"] != "established-within-scope"
    git(repo, "add", "src.py")
    git(repo, "commit", "-qm", "changed")
    changed = status(session)
    assert changed["session"]["status"] == before["session"]["status"]
    assert all(changed[group][0]["freshness"] == "stale" for group in ("requirements", "claims", "evidence"))
    assert changed["claims"][0]["status"] != "established-within-scope"
    assert store.snapshot(session) == before


def test_status_fails_closed_when_saved_repository_is_missing(saved_session, tmp_path):
    _, store, create, status = saved_session
    session = create(session_repo=tmp_path / "missing")
    before = store.snapshot(session)
    view = status(session)
    assert view["session"]["status"] == before["session"]["status"]
    assert view["freshness"]["status"] in ("stale", "unknown")
    assert all(view[group][0]["freshness"] == "stale" for group in ("requirements", "claims", "evidence"))
    assert view["claims"][0]["status"] != "established-within-scope"
    assert store.snapshot(session) == before


def test_candidate_proof_is_stale_for_unchanged_checkout(saved_session):
    repo, store, create, status = saved_session
    original = git(repo, "rev-parse", "HEAD")
    (repo / "src.py").write_text("def f(x):\n    return x + 1\n")
    git(repo, "add", "src.py")
    git(repo, "commit", "-qm", "candidate")
    candidate = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "--detach", original)
    session = create(proof_revision=candidate)
    before = store.snapshot(session)
    view = status(session)
    assert view["session"]["status"] == before["session"]["status"]
    assert view["claims"][0]["status"] == "established-within-scope"
    assert view["claims"][0]["proof_scope"]["revision"] == candidate
    assert view["claims"][0]["freshness"] == "stale"
    # A candidate proof is historical evidence for that candidate, not current
    # assurance for the checked-out revision. Preserve its identity and outcome.
    assert view["evidence"][0]["freshness"] == "stale"
    assert view["evidence"][0]["status"] == "proved"
    assert view["evidence"][0]["source_identity"]["revision"] == candidate
    assert store.snapshot(session) == before


@pytest.mark.parametrize("drift", ["changed", "deleted", "malformed", "duplicate_pin"])
def test_status_invalidates_when_saved_authority_drifts(saved_session, drift):
    _, store, create, status = saved_session
    session = create()
    policy = {"schema_version": 1, "mandatory_local_checks": [["check"]],
              "mandatory_worker_checks": [], "model": "test"}
    policy_path = store.path.parent / "policy.json"
    policy_path.write_text(json.dumps(policy))
    current = status(session)
    assert current["session"]["status"] != "invalidated"
    assert current["claims"][0]["status"] == "established-within-scope"
    assert current["claims"][0].get("freshness") != "stale"

    if drift == "changed":
        policy_path.write_text(json.dumps({**policy, "model": "changed"}))
    elif drift == "deleted":
        policy_path.unlink()
    elif drift == "malformed":
        policy_path.write_text("{not valid json")
    else:
        store.append_event(session, "authority_pinned", {"digest": digest(policy)})
    before = store.snapshot(session)
    view = status(session)
    assert view["session"]["status"] == before["session"]["status"]
    assert view["freshness"]["status"] in ("stale", "unknown")
    assert all(view[group][0]["freshness"] == "stale" for group in ("requirements", "claims", "evidence"))
    assert view["claims"][0]["status"] != "established-within-scope"
    assert store.snapshot(session) == before


def test_status_invalidates_without_an_authority_pin(saved_session):
    _, store, create, status = saved_session
    session = create(pin_authority=False)
    before = store.snapshot(session)
    assert not any(event["kind"] == "authority_pinned" for event in before["events"])
    view = status(session)
    assert view["session"]["status"] == before["session"]["status"]
    assert view["freshness"]["status"] in ("stale", "unknown")
    assert all(view[group][0]["freshness"] == "stale" for group in ("requirements", "claims", "evidence"))
    assert view["claims"][0]["status"] != "established-within-scope"
    assert store.snapshot(session) == before


@pytest.mark.parametrize("field", ["frontend_sha256", "execution_sha256"])
def test_changed_pinned_tool_identity_makes_evidence_stale(saved_session, field):
    _, store, create, status = saved_session
    changed = {**current_tool_identity(), field: "f" * 64}
    session = create(tool_identity=changed)
    before = store.snapshot(session)
    view = status(session)
    assert view["session"]["status"] == before["session"]["status"]
    assert view["freshness"]["status"] in ("stale", "unknown")
    assert view["evidence"][0]["freshness"] == "stale"
    assert store.snapshot(session) == before


def test_recorded_invalidation_remains_invalidated(saved_session):
    _, store, create, status = saved_session
    session = create()
    store.invalidate(session, {"head": "changed"})
    view = status(session)
    assert view["session"]["status"] == "invalidated"
    assert view["claims"][0]["freshness"] == "stale"


@pytest.mark.parametrize("changes", [
    {"contract": None}, {"precondition": None}, {"violation": None},
    {"source_identity": None}, {"source_identity": {"path": "src.py"}},
    {"solver_version": None}, {"assumptions": ["x" * 5000]},
], ids=["contract", "precondition", "violation", "source-identity", "revision", "tool", "redacted-assumption"])
def test_missing_proof_qualification_cannot_be_current_proved(saved_session, changes):
    _, _, create, status = saved_session
    result = status(create(evidence_changes=changes))["evidence"][0]
    assert result["status"] != "proved" or result["freshness"] != "current"
    assert result["unavailable_fields"] or result["status"] in ("incomplete", "inconclusive")
