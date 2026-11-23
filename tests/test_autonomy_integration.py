"""Coordinator publication against the real GitHub adapter at mocked I/O boundaries."""

import hashlib
import json
import subprocess

import pytest

from mentat import autonomy_runtime
from mentat.autonomy import (
    Coordinator,
    RemoteOutcomeUnknown,
    _diff,
    current_tool_identity,
    default_config,
)
from mentat.autonomy_runtime import GitHub
from mentat.autonomy_state import Store


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "file.txt").write_text("before\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    (repo / "file.txt").write_text("after\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "proposal")
    head = git(repo, "rev-parse", "HEAD")
    diff_digest = hashlib.sha256(_diff(repo, base, head)).hexdigest()
    store = Store(tmp_path / "state/state.db")
    config = default_config(repo)
    config.update(auto_merge=True, local_checks=[["required-gate"]])
    session = store.new_session(str(repo), base, head, config, 1, 1000)
    adapter = GitHub([["required-gate"]])
    class Worker:
        def run(self, repo, revision, argv, *, timeout_seconds):
            return {"status": "succeeded", "returncode": 0, "revision": revision, "tools": {"python": "3.12"}}

    coordinator = Coordinator(repo, store, object(), Worker(), adapter, clock=lambda: 2)
    coordinator.session_id = session
    coordinator.config = config
    coordinator.tool_identity = current_tool_identity()
    evidence = coordinator._execute({"payload": {"kind": "final_checks", "target_head": head}}, 1000)["evidence"]
    proposal = {"head": head, "worktree": str(repo), "diff_digest": diff_digest}
    evidence["id"] = "final-checks"
    evidence["check_key"] = coordinator._check_key(head, [], [], None)
    store.put_record(session, "evidence", evidence)
    reviews = [{"head": head, "base": base, "status": "passed", "diff_digest": diff_digest,
                "reviewer_id": name, "author_id": "author", "review_cohort": "cohort",
                "check_evidence_id": evidence["id"], "check_key": evidence["check_key"]}
               for name in ("reviewer-1", "reviewer-2")]
    job = {"payload": {"proposal": proposal, "reviews": reviews}}
    state = {"merged": False, "head": head, "base": base, "stop": False, "remote_calls": 0}

    def fake_git(path, *args):
        state["remote_calls"] += 1
        if args[:2] == ("remote", "get-url"):
            return "git@github.com:owner/repo.git"
        if args[0] in ("push", "fetch"):
            return ""
        if args[0] == "rev-parse":
            return state["base"]
        raise AssertionError(args)

    def fake_gh(path, *args):
        state["remote_calls"] += 1
        if args[:2] == ("pr", "create"):
            return "https://github.com/owner/repo/pull/1"
        if args[:2] == ("pr", "merge"):
            state["merged"] = True
            return ""
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": "owner/repo"})
        assert args[0] == "api", args
        route = args[-1]
        if "branches/main/protection" in route:
            if state["stop"]:
                coordinator.stop.set()
            return json.dumps({"required_status_checks": {"strict": True, "contexts": ["Mentat checks"]}, "enforce_admins": {"enabled": True}})
        if route.endswith("pulls/1/files?per_page=100"):
            return json.dumps([{"filename": "file.txt"}])
        if route.endswith("pulls/1"):
            return json.dumps({"state": "open", "head": {"sha": state["head"]}, "base": {"sha": state["base"], "ref": "main"}})
        if "rulesets?" in route:
            return "[]"
        if route.endswith("/status"):
            return json.dumps({"statuses": [{"context": "Mentat checks", "state": "success"}]})
        if "/check-runs?" in route:
            return json.dumps({"total_count": 0, "check_runs": []})
        raise AssertionError(args)

    monkeypatch.setattr(autonomy_runtime, "git", fake_git)
    monkeypatch.setattr(autonomy_runtime, "_gh", fake_gh)
    return coordinator, job, evidence, state


def test_coordinator_publishes_and_merges_with_real_adapter(scenario):
    coordinator, job, evidence, state = scenario
    coordinator._publish(job, evidence)
    assert state["merged"]
    assert coordinator.store.snapshot(coordinator.session_id)["events"][-1]["kind"] == "merge"


@pytest.mark.parametrize("change", ["base", "head", "stop", "missing_gate", "author", "duplicate"])
def test_real_adapter_rejects_changed_identity_and_incomplete_assurance(scenario, change):
    coordinator, job, evidence, state = scenario
    if change in ("base", "head"):
        state[change] = "f" * 40
    elif change == "stop":
        state["stop"] = True
    elif change == "missing_gate":
        evidence["checks"][0]["command"] = ["true"]
    elif change == "author":
        job["payload"]["reviews"][0]["reviewer_id"] = "author"
    else:
        job["payload"]["reviews"][1]["reviewer_id"] = "reviewer-1"
    expected = {
        "base": "base changed",
        "head": "pull request identity changed",
        "stop": "session is not active",
        "missing_gate": "required local checks are missing",
        "author": "publication reviews do not match the evidence cohort",
        "duplicate": "two independent reviewers are required",
    }
    error_type = ValueError if change in ("duplicate", "author") else RemoteOutcomeUnknown
    with pytest.raises(error_type, match=expected[change]) as caught:
        coordinator._publish(job, evidence)
    assert type(caught.value) is error_type
    assert not state["merged"]


def test_changed_tool_identity_blocks_publication_before_remote_access(scenario, monkeypatch):
    from mentat import source_verify

    coordinator, job, evidence, state = scenario
    changed = {**coordinator.tool_identity, "solver_version": "changed"}
    monkeypatch.setattr(source_verify, "source_tool_identity", lambda: changed)
    with pytest.raises(ValueError, match="toolchain changed"):
        coordinator._publish(job, evidence)
    assert state["remote_calls"] == 0
    assert not state["merged"]


@pytest.mark.parametrize("field", ["review_cohort", "check_evidence_id", "check_key"])
def test_cross_cohort_review_rejected_before_remote_access(scenario, field):
    coordinator, job, evidence, state = scenario
    job["payload"]["reviews"][1][field] = "different"
    with pytest.raises(ValueError, match="publication reviews do not match the evidence cohort"):
        coordinator._publish(job, evidence)
    assert state["remote_calls"] == 0
    assert not state["merged"]
