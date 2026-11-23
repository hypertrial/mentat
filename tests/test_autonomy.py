import json
import subprocess
import uuid
from pathlib import Path

import pytest

from mentat.autonomy import AREAS, Coordinator, default_config, validate_specification
from mentat.autonomy_state import Store


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    (root / "src.py").write_text("def value(): return 1\n")
    config = default_config(root)
    config["local_checks"] = [["check"]]
    config["auto_merge"] = True
    (root / ".mentat").mkdir()
    (root / ".mentat/workspace.json").write_text(json.dumps(config))
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    git(root, "update-ref", "refs/remotes/origin/main", git(root, "rev-parse", "HEAD"))
    return root


class Agent:
    def cancel_all(self):
        pass

    def run(self, repo, job, output, *, timeout_seconds):
        payload = job["payload"]
        kind = payload["kind"]
        if kind == "generate_spec":
            spec = repo / "openspec/specs/application/spec.md"
            spec.parent.mkdir(parents=True)
            requirements = [
                {
                    "id": str(uuid.uuid4()),
                    "area": area,
                    "origin": "uncertain",
                    "statement": "Unspecified",
                    "spec_path": "openspec/specs/application/spec.md",
                }
                for area in AREAS
            ]
            spec.write_text("\n".join(r["id"] for r in requirements))
            surfaces = [
                {"path": s["path"], "status": "gap", "requirement_ids": []}
                for s in payload["surfaces"]
            ]
            (repo / ".mentat/fas.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "requirements": requirements,
                        "claims": [],
                        "surfaces": surfaces,
                    }
                )
            )
        if kind == "generate_verification":
            sidecar = repo / "verification/check.txt"
            sidecar.parent.mkdir()
            sidecar.write_text("check\n")
        return {
            "status": "succeeded",
            "session_id": job["id"],
            "result": {
                "decision": "accept",
                "summary": "accepted",
                "findings": [],
                "verification_requests": [],
            },
        }


class Worker:
    def __init__(self, status="succeeded"):
        self.status = status
        self.revisions = []
        self.commands = []

    def cancel_all(self):
        pass

    def diagnostics(self):
        return {"status": "ready"}

    def run(self, repo, rev, argv, *, timeout_seconds):
        self.revisions.append(rev)
        self.commands.append(argv)
        return {
            "status": self.status,
            "returncode": 0,
            "revision": rev,
            "tools": {"python": "test"},
        }


class GitHub:
    def __init__(self):
        self.published = []
        self.merged = []

    def publish(self, repo, branch, title, body_file):
        self.published.append(git(repo, "rev-parse", "HEAD"))
        return {"status": "succeeded", "url": "https://example.com/pull/1"}

    def merge(self, repo, pr_number, expected_head, expected_base, **kwargs):
        self.merged.append((expected_head, expected_base, kwargs))
        return {"status": "succeeded"}


def run(repo, tmp_path, agent=None, worker=None):
    worker = worker or Worker()
    github = GitHub()
    store = Store(tmp_path / "state/state.db")
    policy = {
        "schema_version": 1,
        "repo": str(repo.resolve()),
        "mandatory_local_checks": [["check"]],
        "mandatory_worker_checks": [],
        "model": "gpt-6.1-terra",
        "reasoning_effort": "high",
        "auto_merge": True,
        "session_seconds": 7200,
        "concurrency": 3,
        "max_attempts": 3,
    }
    (store.path.parent / "policy.json").write_text(json.dumps(policy))
    coordinator = Coordinator(repo, store, agent or Agent(), worker, github)
    return coordinator.run(), worker, github


def test_full_cycle_binds_checks_and_reviews_to_committed_proposal(repo, tmp_path):
    snapshot, worker, github = run(repo, tmp_path)
    assert snapshot["session"]["status"] == "blocked", snapshot
    assert any(f["status"] == "gap" for f in snapshot["findings"])
    assert len(github.published) == len(github.merged) == 1
    head, base, args = github.merged[0]
    assert head == github.published[0] == worker.revisions[0]
    assert all(check["head"] == head and check["base"] == base for check in args["checks"])
    assert len({r["reviewer_id"] for r in args["reviews"]}) == 2
    assert all(r["head"] == head and r["base"] == base for r in args["reviews"])
    assert git(repo, "rev-parse", "HEAD") != head


def test_incomplete_worker_blocks_publication(repo, tmp_path):
    snapshot, _, github = run(repo, tmp_path, worker=Worker("incomplete"))
    assert snapshot["session"]["status"] == "blocked"
    assert not github.published
    assert not any(
        j["payload"]["kind"] in {"review_verification", "review_final"} for j in snapshot["jobs"]
    )
    assert any(
        f.get("summary") == "Protected checks did not pass on the proposed commit"
        for f in snapshot["findings"]
    )


def test_author_cannot_review(repo, tmp_path):
    class SameAgent(Agent):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            result["session_id"] = "same"
            return result

    snapshot, _, github = run(repo, tmp_path, agent=SameAgent())
    assert snapshot["session"]["status"] == "blocked"
    assert not github.published


def test_inventory_rejects_missing_surface(repo, tmp_path):
    snapshot, _, _ = run(repo, tmp_path)
    spec_job = next(j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_spec")
    worktree = Path(spec_job["outcome"]["worktree"])
    with pytest.raises(ValueError, match="omits"):
        validate_specification(worktree, tracked=["missing.py"])


@pytest.mark.parametrize(
    ("protected", "domain"),
    [
        (False, "accept"),
        (True, "accept"),
        (True, "repair_retry"),
        (True, "out_of_domain"),
        (True, "rebound"),
    ],
)
def test_counterexample_requires_protected_reproduction(repo, tmp_path, protected, domain):
    requirements = [
        {
            "id": str(uuid.uuid4()),
            "area": area,
            "origin": "observed",
            "statement": "The function increments its input",
            "spec_path": "openspec/specs/application/spec.md",
        }
        for area in AREAS
    ]
    spec = repo / "openspec/specs/application/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("\n".join(r["id"] for r in requirements))
    source = "def f(x):\n    return x - 1\n"
    if domain == "rebound":
        source += "f = lambda x: x\n"
    if domain == "out_of_domain":
        requirements[0]["statement"] = "The function increments inputs x >= 0"
    (repo / "src.py").write_text(source)
    requirements[0]["binding"] = {"kind": "function", "path": "src.py", "function": "f"}
    surfaces = [
        {"path": path, "status": "gap", "requirement_ids": []}
        for path in [
            "src.py",
            ".mentat/workspace.json",
            ".mentat/fas.json",
            "openspec/specs/application/spec.md",
        ]
    ]
    (repo / ".mentat/fas.json").write_text(
        json.dumps(
            {"schema_version": 1, "requirements": requirements, "claims": [], "surfaces": surfaces}
        )
    )
    config_path = repo / ".mentat/workspace.json"
    config = json.loads(config_path.read_text())
    config["source_checks"] = [
        {
            "path": "src.py",
            "function": "f",
            "inputs": {"x": "int"},
            "requires": "x < 0" if domain == "out_of_domain" else "True",
            "ensures": "result == x + 1",
            "requirement_id": requirements[0]["id"],
        }
    ]
    config_path.write_text(json.dumps(config))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "record requirement")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))

    class FixAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            if job["payload"]["kind"] in {"review_fix", "review_verification", "review_final"}:
                summary = job["payload"].get("check_summary")
                assert summary and summary["status"] == "passed"
                assert summary["head"] == git(repo, "rev-parse", "HEAD")
                assert summary["evidence_id"] == job["payload"]["check_evidence_id"]
                assert summary["checks"] and all(
                    item["status"] == "passed" for item in summary["checks"]
                )
                assert summary["source"] and all(
                    item["status"] == "proved" for item in summary["source"]
                )
                assert summary["repair_check"]["status"] == "passed"
                if job["payload"]["kind"] in {"review_verification", "review_final"}:
                    assert any(item["command"] == ["sidecar-check"] for item in summary["checks"])
                assert '"check_summary"' in job["payload"]["prompt"]
            if job["payload"]["kind"] == "generate_spec":
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept" if job["payload"].get("repair") else "defer",
                        "summary": "inventory reviewed"
                        if job["payload"].get("repair")
                        else "inventory needs work",
                        "findings": [],
                        "verification_requests": [],
                    },
                }
            if job["payload"]["kind"] == "adjudicate_domain":
                assert job["payload"]["purpose"] == "discriminator"
                assert "exact reproduced witness" in job["payload"]["prompt"]
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept" if domain in {"accept", "repair_retry"} else "defer",
                        "summary": "domain reviewed",
                        "findings": [],
                        "verification_requests": [],
                        "defects": [
                            {
                                "id": "finding",
                                "requirement_id": job["payload"]["requirement"]["id"],
                                "evidence_id": job["payload"]["evidence"]["id"],
                                "summary": "src.py: direct function binding",
                            }
                        ],
                    },
                }
            if job["payload"]["kind"] == "investigate_defect":
                if job["payload"].get("round", 0) == 0:
                    (repo / "src.py").write_text("def f(x):\n    return x + 1\n")
                else:
                    assert all(
                        k in job["payload"] for k in ("requirement", "evidence", "reproduction")
                    )
                    failed = job["payload"]["feedback"]["check_summary"]["checks"][0]
                    assert failed["command"] == ["bad-repair-command"]
                    assert "not found" in failed["stderr_excerpt"]
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if (
                job["payload"]["kind"] == "investigate_defect"
                and domain == "repair_retry"
                and job["payload"].get("round", 0) == 0
            ):
                result["result"]["verification_requests"] = [
                    {"argv": ["bad-repair-command"], "claim_ids": [], "method": "test"}
                ]
            if job["payload"]["kind"] == "generate_verification":
                result["result"]["verification_requests"] = [
                    {"argv": ["sidecar-check"], "claim_ids": [], "method": "test"}
                ]
            return result

    class ProtectedWorker(Worker):
        def run(self, repo, rev, argv, *, timeout_seconds):
            result = super().run(repo, rev, argv, timeout_seconds=timeout_seconds)
            if argv == ["bad-repair-command"]:
                result.update(status="failed", returncode=127, stderr="not found")
            return result

        def reproduce(self, repo, rev, evidence, *, timeout_seconds):
            return {
                "status": "passed",
                "revision": rev,
                "evidence_id": evidence["id"],
                "inputs": evidence["counterexample"],
                "precondition": True,
                "violated": True,
                "method": "protected-runtime-reproduction",
                "tools": {"harness": "test"},
            }

        def verify_repair(self, repo, rev, evidence, *, timeout_seconds):
            return {
                "status": "passed",
                "revision": rev,
                "evidence_id": evidence["id"],
                "inputs": evidence["counterexample"],
                "precondition": True,
                "postcondition": True,
                "method": "protected-runtime-repair",
                "tools": {"harness": "test"},
            }

    snapshot, worker, github = run(
        repo, tmp_path, agent=FixAgent(), worker=ProtectedWorker() if protected else Worker()
    )
    assert any(j["payload"]["kind"] == "adjudicate_defect" for j in snapshot["jobs"])
    assert any(j["payload"]["kind"] == "reproduce_defect" for j in snapshot["jobs"])
    fix_expected = protected and domain in {"accept", "repair_retry"}
    assert any(j["payload"]["kind"] == "adjudicate_domain" for j in snapshot["jobs"]) is protected
    assert (
        any(j["payload"]["kind"] == "investigate_defect" for j in snapshot["jobs"]) is fix_expected
    )
    if fix_expected:
        assert github.published and worker.revisions[-1] == github.published[0]
        assert len(worker.revisions) == (5 if domain == "repair_retry" else 3)
        assert worker.commands.count(["check"]) == (3 if domain == "repair_retry" else 2)
        assert worker.commands.count(["sidecar-check"]) == 1
        if domain == "repair_retry":
            assert worker.commands.count(["bad-repair-command"]) == 1
            fix_jobs = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "investigate_defect"]
            assert len(fix_jobs) == 2 and len({j["item_id"] for j in fix_jobs}) == 1
        assert any(
            j["payload"]["kind"] == "generate_spec" and j["payload"].get("repair")
            for j in snapshot["jobs"]
        )
        assert any(
            j["payload"]["kind"] == "generate_verification" and j["payload"].get("repair")
            for j in snapshot["jobs"]
        )
        assert any(
            e.get("repair_check", {}).get("status") == "passed"
            for e in snapshot["evidence"]
            if isinstance(e.get("repair_check"), dict)
        )
    else:
        assert not github.published and not worker.revisions
    assert snapshot["session"]["status"] == "blocked"


def test_agent_receives_committed_context_and_review_diff(repo, tmp_path):
    class InspectAgent(Agent):
        def __init__(self):
            self.kinds = set()

        def run(self, repo, job, output, *, timeout_seconds):
            kind = job["payload"]["kind"]
            prompt = job["payload"]["prompt"]
            assert "SOURCE_CONTEXT_JSON:" in prompt
            context = json.loads(prompt.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
            assert "src.py" in context["files"]
            if kind.startswith("review_"):
                assert context["proposal_diff"]
                assert context["proposal_base"]
            self.kinds.add(kind)
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    agent = InspectAgent()
    _, _, github = run(repo, tmp_path, agent=agent)
    assert {"generate_spec", "review_spec", "generate_verification", "review_final"} <= agent.kinds
    assert github.published


def test_rejects_config_that_removes_pinned_gate(repo, tmp_path):
    store = Store(tmp_path / "state/state.db")
    policy = {
        "schema_version": 1,
        "repo": str(repo.resolve()),
        "mandatory_local_checks": [["required"]],
        "mandatory_worker_checks": [],
        "model": "gpt-6.1-terra",
        "reasoning_effort": "high",
        "auto_merge": True,
        "session_seconds": 7200,
        "concurrency": 3,
        "max_attempts": 3,
    }
    (store.path.parent / "policy.json").write_text(json.dumps(policy))
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    with pytest.raises(ValueError, match="removes a required"):
        coordinator.run()


def test_feedback_is_consumed_once(repo, tmp_path):
    store = Store(tmp_path / "state/state.db")
    config = default_config(repo)
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), "base", "head", config, 1, 10)
    store.append_event(
        coordinator.session_id, "human_feedback", {"message": "Review this requirement"}
    )
    coordinator._consume_feedback()
    coordinator._consume_feedback()
    snapshot = store.snapshot(coordinator.session_id)
    assert sum(e["kind"] == "feedback_consumed" for e in snapshot["events"]) == 1
    assert len(snapshot["jobs"]) == 1


def test_changed_external_authority_invalidates_resume(repo, tmp_path):
    snapshot, worker, github = run(repo, tmp_path)
    store = Store(tmp_path / "state/state.db")
    policy_path = store.path.parent / "policy.json"
    policy = json.loads(policy_path.read_text())
    policy["concurrency"] = 2
    policy_path.write_text(json.dumps(policy))
    coordinator = Coordinator(repo, store, Agent(), worker, github)
    with pytest.raises(ValueError, match="authority changed"):
        coordinator.run(resume=snapshot["session"]["id"])
    assert store.snapshot(snapshot["session"]["id"])["session"]["status"] == "invalidated"


def test_missing_claims_recorded_as_formal_gap(repo, tmp_path):
    snapshot, _, _ = run(repo, tmp_path)
    assert snapshot["session"]["status"] == "blocked"
    assert any(
        e["kind"] == "formal_gaps" and e["payload"]["reason"] == "no claims established"
        for e in snapshot["events"]
    )


def test_lock_remains_held_until_pending_task_finishes(repo, tmp_path):
    from concurrent.futures import Future

    store = Store(tmp_path / "state/state.db")
    first = Coordinator(repo, store, Agent(), Worker(), GitHub())
    second = Coordinator(repo, store, Agent(), Worker(), GitHub())
    pending = Future()
    with first._exclusive():
        first._pending_tasks = [pending]
    with pytest.raises(ValueError, match="another controller"):
        with second._exclusive():
            pass
    pending.set_result(None)
    with second._exclusive():
        pass


def test_unconfirmed_termination_blocks_new_session(repo, tmp_path):
    store = Store(tmp_path / "state/state.db")
    store.path.with_suffix(".termination-unconfirmed").write_text("inspect worker")
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    with pytest.raises(ValueError, match="termination is unconfirmed"):
        with coordinator._exclusive():
            pass


def test_project_snapshot_binds_only_matching_reviewed_current_source_proofs():
    from copy import deepcopy

    from mentat.autonomy import digest, project_snapshot

    contract = {
        "path": "src.py",
        "function": "f",
        "inputs": {"x": "int"},
        "requires": "True",
        "ensures": "result == x",
    }
    claim = {
        "id": "claim-1",
        "formal_contract": contract,
        "status": "unattempted",
        "assumptions": ["exact inputs"],
    }
    proof = {
        "id": "evidence-1",
        "claim_id": "claim-1",
        "method": "source-proof",
        "status": "proved",
        "precondition": "sat",
        "violation": "unsat",
        "contract": {key: contract[key] for key in ("inputs", "requires", "ensures")},
        "source_identity": {
            "revision": "head",
            "path": "src.py",
            "function": "f",
            "blob_oid": "blob",
            "source_sha256": "source",
        },
        "frontend_sha256": "frontend",
        "contract_sha256": "contract",
        "solver_version": "z3",
        "solver_library_sha256": "solver",
        "python_version": "3",
        "python_implementation": "CPython",
        "solver_binding_sha256": "binding",
        "assumptions": ["trusted solver"],
    }
    snapshot = {
        "session": {
            "status": "blocked",
            "policy": {"source_checks": [{**contract, "claim_id": "claim-1"}]},
        },
        "claims": [claim],
        "evidence": [proof],
        "events": [
            {
                "kind": "fas_reviewed",
                "payload": {"head": "fas", "claims": {"claim-1": digest(claim)}},
            },
            {
                "kind": "claim_source_scope",
                "payload": {"head": "head", "fas_head": "fas", "evidence_id": "evidence-1"},
            },
        ],
    }
    identity = {
        key: proof[key]
        for key in (
            "frontend_sha256",
            "python_version",
            "python_implementation",
            "solver_version",
            "solver_binding_sha256",
            "solver_library_sha256",
        )
    }
    assert project_snapshot(snapshot)["claims"][0]["status"] != "established-within-scope"
    view = project_snapshot(snapshot, identity=identity)["claims"][0]
    assert view["status"] == "established-within-scope"
    assert view["evidence_ids"] == ["evidence-1"]
    assert view["proof_scope"]["assumptions"] == ["trusted solver"]
    for key in ("frontend_sha256", "python_version", "solver_library_sha256"):
        assert (
            project_snapshot(snapshot, identity={**identity, key: "drift"})["claims"][0]["status"]
            != "established-within-scope"
        )
    assert snapshot["claims"][0]["status"] == "unattempted"
    for mutate in (
        lambda x: x["session"]["policy"]["source_checks"][0].update(ensures="False"),
        lambda x: x["evidence"][0].update(claim_id="another"),
        lambda x: x["evidence"][0]["source_identity"].update(revision="other"),
        lambda x: x["evidence"][0].update(violation="sat"),
        lambda x: x["events"].pop(0),
        lambda x: x["session"].update(status="invalidated"),
    ):
        changed = deepcopy(snapshot)
        mutate(changed)
        assert (
            project_snapshot(changed, identity=identity)["claims"][0]["status"]
            != "established-within-scope"
        )
    counterexample = deepcopy(snapshot)
    counterexample["evidence"][0]["status"] = "counterexample"
    assert (
        project_snapshot(counterexample, identity=identity)["claims"][0]["status"]
        == "unconfirmed-counterexample"
    )


def test_reviewed_claim_projects_real_final_source_proof(repo, tmp_path):
    claim_id = str(uuid.uuid4())
    contract = {
        "path": "src.py",
        "function": "f",
        "inputs": {"x": "int"},
        "requires": "True",
        "ensures": "result == x + 1",
    }
    (repo / "src.py").write_text("def f(x):\n    return x + 1\n")
    config_path = repo / ".mentat/workspace.json"
    config = json.loads(config_path.read_text())
    config_path.write_text(json.dumps(config))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "declare source check")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))

    class ClaimAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if job["payload"]["kind"] == "generate_spec":
                path = repo / ".mentat/fas.json"
                fas = json.loads(path.read_text())
                fas["claims"] = [
                    {
                        "id": claim_id,
                        "requirement_ids": [fas["requirements"][0]["id"]],
                        "statement": "f increments",
                        "assumptions": ["exact typed input"],
                        "status": "unattempted",
                        "formal_contract": contract,
                    }
                ]
                path.write_text(json.dumps(fas))
            return result

    snapshot, worker, github = run(repo, tmp_path, agent=ClaimAgent())
    assert len(snapshot["claims"]) == 1
    claim = snapshot["claims"][0]
    assert claim["status"] == "established-within-scope", snapshot
    assert claim["proof_scope"]["revision"] == github.published[0] == worker.revisions[0]
    assert claim["evidence_ids"]


def test_toolchain_change_invalidates_resume(repo, tmp_path, monkeypatch):
    from mentat import source_verify

    snapshot, worker, github = run(repo, tmp_path)
    store = Store(tmp_path / "state/state.db")
    pinned = next(
        e["payload"]["identity"] for e in snapshot["events"] if e["kind"] == "toolchain_pinned"
    )
    monkeypatch.setattr(
        source_verify, "source_tool_identity", lambda: {**pinned, "python_version": "changed"}
    )
    coordinator = Coordinator(repo, store, Agent(), worker, github)
    with pytest.raises(ValueError, match="toolchain changed"):
        coordinator.run(resume=snapshot["session"]["id"])
    assert store.snapshot(snapshot["session"]["id"])["session"]["status"] == "invalidated"


def test_revision_feedback_regenerates_same_item_with_bounded_round(repo, tmp_path):
    class RevisingAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job["payload"]
            if payload["kind"] == "generate_spec" and payload.get("round") == 1:
                spec = repo / "openspec/specs/application/spec.md"
                spec.write_text(spec.read_text() + "\nReviewed clarification\n")
                fas_path = repo / ".mentat/fas.json"
                fas = json.loads(fas_path.read_text())
                for path in (".mentat/fas.json", "openspec/specs/application/spec.md"):
                    fas["surfaces"].append({"path": path, "status": "gap", "requirement_ids": []})
                fas_path.write_text(json.dumps(fas))
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept",
                        "summary": "revised",
                        "findings": [],
                        "verification_requests": [],
                    },
                }
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if payload["kind"] == "review_spec" and payload["proposal"].get("round") == 0:
                result["result"]["decision"] = "revise"
                result["result"]["findings"] = [{"summary": "Clarify"}]
            return result

    snapshot, _, _ = run(repo, tmp_path, agent=RevisingAgent())
    generations = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_spec"]
    assert len(generations) == 2
    assert {j["item_id"] for j in generations} == {"cas.inventory"}
    assert sorted(j["payload"].get("round", 0) for j in generations) == [0, 1]
    assert any(
        j["payload"]["kind"] == "review_spec" and j["payload"]["proposal"].get("round") == 1
        for j in snapshot["jobs"]
    ), snapshot


def test_refinement_requires_stable_id_versioned_lineage_and_evidence(repo, tmp_path):
    from copy import deepcopy

    from mentat.autonomy import digest

    snapshot, _, _ = run(repo, tmp_path)
    job = next(j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_spec")
    worktree = Path(job["outcome"]["worktree"])
    path = worktree / ".mentat/fas.json"
    prior = json.loads(path.read_text())
    updated = deepcopy(prior)
    old = prior["requirements"][0]
    updated["requirements"][0]["statement"] = "Clarified requirement"
    path.write_text(json.dumps(updated))
    with pytest.raises(ValueError, match="lineage"):
        validate_specification(worktree, prior=prior)
    updated["requirements"][0]["revision"] = {
        "version": 2,
        "previous_digest": digest(old),
        "reason": "Inspected behavior",
        "evidence": ["src.py"],
    }
    path.write_text(json.dumps(updated))
    assert validate_specification(worktree, prior=prior)["requirements"][0]["id"] == old["id"]
    updated["requirements"].pop(0)
    path.write_text(json.dumps(updated))
    with pytest.raises(ValueError):
        validate_specification(worktree, prior=prior)


def test_successful_job_transition_replays_once_after_restart(repo, tmp_path):
    store = Store(tmp_path / "state/state.db")
    config = default_config(repo)
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), "base", "head", config, 1, 100)
    job_id = store.enqueue_job(
        coordinator.session_id, "ivl", "verifier", "source.0", "input", {"kind": "source_check"}
    )
    claimed = store.claim_job(coordinator.session_id, "worker", now=2)
    store.finish_job(
        job_id,
        claimed["lease_token"],
        {"status": "succeeded", "evidence": {"status": "proved"}},
        now=3,
    )
    assert len(store.pending_transitions(coordinator.session_id)) == 1
    coordinator._drain_transitions()
    coordinator._drain_transitions()
    snapshot = store.snapshot(coordinator.session_id)
    assert len(snapshot["evidence"]) == 1
    assert not store.pending_transitions(coordinator.session_id)


def test_structured_generator_changes_are_applied_and_content_not_recorded(repo, tmp_path):
    class StructuredAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            if job["payload"]["kind"] == "generate_verification":
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "events": [{"content": "provider transcript"}],
                    "result": {
                        "decision": "accept",
                        "summary": "generated",
                        "findings": [],
                        "verification_requests": [],
                        "changes": [
                            {
                                "path": "verification/check.txt",
                                "operation": "write",
                                "content": "sensitive proposal content",
                            }
                        ],
                    },
                }
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=StructuredAgent())
    job = next(j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_verification")
    assert (
        git(Path(job["outcome"]["worktree"]), "show", "HEAD:verification/check.txt")
        == "sensitive proposal content"
    )
    assert "sensitive proposal content" not in json.dumps(job["outcome"])
    assert "provider transcript" not in json.dumps(job["outcome"])
    assert github.published


def test_reviewed_requirement_refinement_is_versioned_and_rechecked(repo, tmp_path):
    from mentat.autonomy import digest

    requirements = [
        {
            "id": str(uuid.uuid4()),
            "area": area,
            "origin": "uncertain",
            "statement": "Unknown",
            "spec_path": "openspec/specs/application/spec.md",
        }
        for area in AREAS
    ]
    spec = repo / "openspec/specs/application/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("\n".join(r["id"] for r in requirements))
    surfaces = [
        {"path": path, "status": "gap", "requirement_ids": []}
        for path in [
            "src.py",
            ".mentat/workspace.json",
            ".mentat/fas.json",
            "openspec/specs/application/spec.md",
        ]
    ]
    (repo / ".mentat/fas.json").write_text(
        json.dumps(
            {"schema_version": 1, "requirements": requirements, "claims": [], "surfaces": surfaces}
        )
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "prior specification")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))

    class RefineAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            if job["payload"]["kind"] == "generate_spec":
                path = repo / ".mentat/fas.json"
                fas = json.loads(path.read_text())
                old = dict(fas["requirements"][0])
                fas["requirements"][0]["statement"] = "Clarified"
                fas["requirements"][0]["revision"] = {
                    "version": 2,
                    "previous_digest": digest(old),
                    "reason": "Reviewed source",
                    "evidence": ["src.py"],
                }
                path.write_text(json.dumps(fas))
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept",
                        "summary": "refined",
                        "findings": [],
                        "verification_requests": [],
                    },
                }
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=RefineAgent())
    updated = next(r for r in snapshot["requirements"] if r["id"] == requirements[0]["id"])
    assert updated["statement"] == "Clarified", snapshot
    assert any(e["kind"] == "record_revised" for e in snapshot["events"])
    assert github.published, snapshot


def test_large_inventory_continues_in_targeted_chunks(repo, tmp_path):
    folder = repo / "src"
    folder.mkdir()
    for i in range(10):
        (folder / f"file{i}.py").write_text("# " + "x" * 44990 + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "large inventory")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))

    class ChunkAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            if job["payload"]["kind"] == "generate_spec" and job["payload"].get("targetfiles"):
                path = repo / ".mentat/fas.json"
                fas = json.loads(path.read_text())
                existing = {s["path"] for s in fas["surfaces"]}
                for name in (".mentat/fas.json", "openspec/specs/application/spec.md"):
                    if name not in existing:
                        fas["surfaces"].append(
                            {"path": name, "status": "gap", "requirement_ids": []}
                        )
                path.write_text(json.dumps(fas))
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept",
                        "summary": "continued inventory",
                        "findings": [],
                        "verification_requests": [],
                    },
                }
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=ChunkAgent())
    chunks = [
        j
        for j in snapshot["jobs"]
        if j["payload"]["kind"] == "generate_spec" and j["payload"].get("targetfiles")
    ]
    assert chunks, snapshot
    assert any(path.startswith("src/file") for path in chunks[0]["payload"]["targetfiles"])
    assert github.published, snapshot


@pytest.mark.parametrize("remote_state", ["open", "merged"])
@pytest.mark.parametrize("recorded_failure", [False, True])
def test_interrupted_merge_reconciles_before_retry(repo, tmp_path, remote_state, recorded_failure):
    import time

    from mentat.autonomy import current_tool_identity

    class ReconcilingGitHub(GitHub):
        def reconcile(self, repo, branch, expected_head, expected_base):
            return {
                "status": "found",
                "state": remote_state,
                "head": expected_head,
                "base": expected_base,
                "url": "https://example.com/pull/1",
                "number": 1,
            }

    store = Store(tmp_path / "state/state.db")
    github = ReconcilingGitHub()
    coordinator = Coordinator(repo, store, Agent(), Worker(), github)
    config = default_config(repo)
    config["auto_merge"] = True
    coordinator.config = config
    coordinator.tool_identity = current_tool_identity()
    head = git(repo, "rev-parse", "HEAD")
    now = time.time()
    coordinator.session_id = store.new_session(str(repo), head, head, config, now, now + 60)
    store.append_event(coordinator.session_id, "publication_started", {"head": head, "base": head})
    store.append_event(
        coordinator.session_id,
        "publication",
        {
            "head": head,
            "base": head,
            "result": {"status": "succeeded", "url": "https://example.com/pull/1"},
        },
    )
    store.append_event(coordinator.session_id, "merge_started", {"head": head, "base": head})
    if recorded_failure:
        store.append_event(
            coordinator.session_id,
            "merge",
            {
                "head": head,
                "base": head,
                "result": {"status": "incomplete", "reason": "connection lost"},
            },
        )
    key = coordinator._check_key(head, [], [], None)
    proposal = {"head": head, "worktree": str(repo), "diff_digest": "diff"}
    reviews = [
        {
            "reviewer_id": reviewer,
            "author_id": "author",
            "status": "passed",
            "head": head,
            "base": head,
            "diff_digest": "diff",
            "review_cohort": "cohort",
            "check_evidence_id": "checked",
            "check_key": key,
        }
        for reviewer in ("one", "two")
    ]
    job = {"payload": {"proposal": proposal, "reviews": reviews}}
    coordinator._publish(
        job,
        {
            "id": "checked",
            "check_key": key,
            "head": head,
            "base": head,
            "checks": [{"status": "passed"}],
        },
    )
    assert len(github.merged) == (1 if remote_state == "open" else 0)
    events = store.snapshot(coordinator.session_id)["events"]
    assert any(
        e["kind"] == "merge" and e["payload"]["result"]["status"] == "succeeded" for e in events
    )
    assert any(e["kind"] == "merge_reconciled_open" for e in events) == (remote_state == "open")


def test_failed_publication_reconciles_existing_pr(repo, tmp_path):
    import time

    from mentat.autonomy import current_tool_identity

    class ReconcilingGitHub(GitHub):
        def reconcile(self, repo, branch, expected_head, expected_base):
            return {
                "status": "found",
                "state": "open",
                "head": expected_head,
                "base": expected_base,
                "url": "https://example.com/pull/1",
                "number": 1,
            }

    store = Store(tmp_path / "state/state.db")
    github = ReconcilingGitHub()
    coordinator = Coordinator(repo, store, Agent(), Worker(), github)
    config = default_config(repo)
    config["auto_merge"] = True
    coordinator.config = config
    coordinator.tool_identity = current_tool_identity()
    head = git(repo, "rev-parse", "HEAD")
    now = time.time()
    coordinator.session_id = store.new_session(str(repo), head, head, config, now, now + 60)
    store.append_event(coordinator.session_id, "publication_started", {"head": head, "base": head})
    store.append_event(
        coordinator.session_id,
        "publication",
        {"head": head, "base": head, "result": {"status": "failed", "error": "timeout"}},
    )
    key = coordinator._check_key(head, [], [], None)
    proposal = {"head": head, "worktree": str(repo), "diff_digest": "diff"}
    reviews = [
        {
            "reviewer_id": reviewer,
            "author_id": "author",
            "status": "passed",
            "head": head,
            "base": head,
            "diff_digest": "diff",
            "review_cohort": "cohort",
            "check_evidence_id": "checked",
            "check_key": key,
        }
        for reviewer in ("one", "two")
    ]
    job = {"payload": {"proposal": proposal, "reviews": reviews}}
    coordinator._publish(
        job,
        {
            "id": "checked",
            "check_key": key,
            "head": head,
            "base": head,
            "checks": [{"status": "passed"}],
        },
    )
    assert not github.published
    assert len(github.merged) == 1
    events = store.snapshot(coordinator.session_id)["events"]
    assert any(
        e["kind"] == "publication" and e["payload"]["result"].get("reconciled") for e in events
    )


def test_ambiguous_remote_outcome_keeps_transition_pending(repo, tmp_path, monkeypatch):
    from mentat.autonomy import RemoteOutcomeUnknown

    store = Store(tmp_path / "state/state.db")
    config = default_config(repo)
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), "base", "head", config, 1, 100)
    job_id = store.enqueue_job(
        coordinator.session_id, "ivl", "verifier", "final", "input", {"kind": "final_checks"}
    )
    job = store.claim_job(coordinator.session_id, "worker", now=2)
    store.finish_job(
        job_id, job["lease_token"], {"status": "succeeded", "evidence": {"status": "passed"}}, now=3
    )

    def ambiguous(*args):
        raise RemoteOutcomeUnknown("connection lost")

    monkeypatch.setattr(coordinator, "_advance", ambiguous)
    coordinator._drain_transitions()
    assert coordinator.stop.is_set()
    assert store.transition_status(job_id) == "pending"
    assert any(
        e["kind"] == "remote_reconciliation_pending"
        for e in store.snapshot(coordinator.session_id)["events"]
    )


def test_adjudication_prompt_checks_atomic_obligation_and_concrete_witness(repo, tmp_path):
    store = Store(tmp_path / "state/state.db")
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    job = {
        "loop": "iil",
        "payload": {
            "kind": "adjudicate_defect",
            "requirement": {"statement": "identity returns its exact integer argument"},
            "evidence": {
                "contract": {"inputs": {"x": "int"}, "requires": "True", "ensures": "result == x"},
                "counterexample": {"x": 0},
            },
        },
    }
    prompt = coordinator._prompt(job, {"head": "revision", "base": "base"})
    assert "entails this atomic source-contract obligation" in prompt
    assert "concrete witness refutes it" in prompt
    assert "omitted output-type, caller, and CLI guarantees remain separate gaps" in prompt
    assert "out-of-domain witness" in prompt
    assert "actual function's supported input and return types" in prompt
    assert "do not generalize mixed int/bool comparisons" in prompt
    assert '"x": 0' in prompt


def test_pre_review_cache_requires_exact_head_and_check_plan(repo, tmp_path):
    store = Store(tmp_path / "state/state.db")
    config = default_config(repo)
    config["local_checks"] = [["check"]]
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.tool_identity = {"frontend": "trusted", "execution_sha256": "one"}
    coordinator.authority_digest = "policy"
    coordinator.session_id = store.new_session(str(repo), "base", "head", config, 1, 100)
    key = coordinator._check_key("head", [], [], None)
    store.put_record(
        coordinator.session_id,
        "evidence",
        {
            "id": "checked",
            "status": "passed",
            "head": "head",
            "base": "base",
            "check_key": key,
            "checks": [
                {
                    "command": ["check"],
                    "status": "passed",
                    "returncode": 0,
                    "tools": {"python": "test"},
                }
            ],
            "source": [],
        },
    )
    job = {
        "id": "generator",
        "item_id": "verification",
        "loop": "ivl",
        "payload": {"source_checks": []},
    }
    proposal = {
        "head": "head",
        "root_item": "verification",
        "round": 0,
        "author_id": "author",
        "commands": [],
    }
    coordinator._request_reviews(job, proposal, "review_verification")
    jobs = store.snapshot(coordinator.session_id)["jobs"]
    assert len(jobs) == 2 and all(j["payload"]["kind"] == "review_verification" for j in jobs)
    assert all(j["payload"]["check_evidence_id"] == "checked" for j in jobs)
    coordinator._request_reviews(job, {**proposal, "head": "changed"}, "review_verification")
    coordinator._request_reviews(
        job, {**proposal, "commands": [["different-check"]]}, "review_verification"
    )
    coordinator.tool_identity = {"frontend": "trusted", "execution_sha256": "two"}
    coordinator._request_reviews(job, proposal, "review_verification")
    prechecks = [
        j
        for j in store.snapshot(coordinator.session_id)["jobs"]
        if j["payload"]["kind"] == "pre_review_checks"
    ]
    assert len(prechecks) == 3
    assert len({j["payload"]["check_key"] for j in prechecks}) == 3


def test_interleaved_review_plans_never_share_acceptances_or_rejections(
    repo, tmp_path, monkeypatch
):
    store = Store(tmp_path / "state/state.db")
    config = default_config(repo)
    config["local_checks"] = [["check"]]
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.tool_identity = {"frontend": "trusted"}
    coordinator.authority_digest = "policy"
    coordinator.session_id = store.new_session(str(repo), "base", "head", config, 1, 100)
    published = []
    monkeypatch.setattr(
        coordinator, "_publish", lambda job, evidence: published.append((job, evidence))
    )
    generator = {
        "id": "generator",
        "item_id": "verify",
        "loop": "ivl",
        "payload": {"source_checks": []},
    }
    for name in ("a", "b", "c"):
        commands = [[name]]
        key = coordinator._check_key("head", commands, [], None)
        evidence = {
            "id": name,
            "status": "passed",
            "head": "head",
            "base": "base",
            "check_key": key,
            "checks": [
                {
                    "command": [name],
                    "status": "passed",
                    "returncode": 0,
                    "tools": {"python": "test"},
                }
            ],
            "source": [],
        }
        store.put_record(coordinator.session_id, "evidence", evidence)
        proposal = {
            "head": "head",
            "diff_digest": "same-diff",
            "author_id": "author",
            "root_item": "verify",
            "round": 0,
            "commands": commands,
            "source_checks": [],
            "repair": None,
        }
        coordinator._enqueue_reviewers(generator, proposal, "review_final", evidence)
    jobs = store.snapshot(coordinator.session_id)["jobs"]
    groups = {
        name: [j for j in jobs if j["payload"]["check_evidence_id"] == name]
        for name in ("a", "b", "c")
    }
    assert all(len(group) == 2 for group in groups.values())

    def review(job, decision):
        outcome = {
            "author_id": job["id"],
            "head": "head",
            "diff_digest": "same-diff",
            "result": {"decision": decision, "summary": decision, "findings": []},
        }
        coordinator._advance(job, outcome)

    review(groups["a"][0], "accept")
    review(groups["b"][0], "accept")
    assert not published
    review(groups["a"][1], "accept")
    assert len(published) == 1 and published[0][1]["id"] == "a"
    review(groups["b"][1], "defer")
    review(groups["c"][0], "accept")
    review(groups["c"][1], "accept")
    assert len(published) == 2 and published[1][1]["id"] == "c"
    assert (
        len({d["review_cohort"] for d in store.snapshot(coordinator.session_id)["decisions"]}) == 3
    )


def test_execution_identity_change_invalidates_resume(repo, tmp_path, monkeypatch):
    from mentat import autonomy_runtime

    snapshot, worker, github = run(repo, tmp_path)
    store = Store(tmp_path / "state/state.db")
    pinned = next(
        e["payload"]["identity"] for e in snapshot["events"] if e["kind"] == "toolchain_pinned"
    )
    monkeypatch.setattr(
        autonomy_runtime, "execution_identity", lambda: pinned["execution_sha256"] + "changed"
    )
    coordinator = Coordinator(repo, store, Agent(), worker, github)
    with pytest.raises(ValueError, match="toolchain changed"):
        coordinator.run(resume=snapshot["session"]["id"])
    assert store.snapshot(snapshot["session"]["id"])["session"]["status"] == "invalidated"


def test_execution_identity_drift_blocks_publication(repo, tmp_path, monkeypatch):
    import time

    from mentat import autonomy_runtime
    from mentat.autonomy import current_tool_identity

    store = Store(tmp_path / "state/state.db")
    github = GitHub()
    coordinator = Coordinator(repo, store, Agent(), Worker(), github)
    coordinator.config = default_config(repo)
    coordinator.tool_identity = current_tool_identity()
    head = git(repo, "rev-parse", "HEAD")
    now = time.time()
    coordinator.session_id = store.new_session(
        str(repo), head, head, coordinator.config, now, now + 60
    )
    monkeypatch.setattr(autonomy_runtime, "execution_identity", lambda: "different-execution")
    with pytest.raises(ValueError, match="execution toolchain changed"):
        coordinator._publish(
            {"payload": {"proposal": {"head": head}, "reviews": []}}, {"head": head, "base": head}
        )
    assert not github.published


def test_verification_prompt_distinguishes_managed_and_additional_checks(repo, tmp_path):
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    generator = {"loop": "ivl", "payload": {"kind": "generate_verification"}}
    reviewer = {
        "loop": "ivl",
        "payload": {"kind": "review_final", "check_summary": {"status": "passed"}},
    }
    session = {"head": "head", "base": "base"}
    prompt = coordinator._prompt(generator, session)
    assert "controller automatically runs" in prompt
    assert "only additional executable commands" in prompt
    assert "empty verification_requests list" in prompt
    assert "never invent an empty argv" in prompt.lower()
    assert "formal_contract" in prompt
    assert "unsupported" in prompt and "defer" in prompt
    review_prompt = coordinator._prompt(reviewer, session)
    assert "actual protected completion-gate and source-contract evidence" in review_prompt
    assert "Do not request those same checks again" in review_prompt


def test_duplicate_requested_gate_runs_once(repo, tmp_path):
    class DuplicateAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if job["payload"]["kind"] == "generate_verification":
                result["result"]["verification_requests"] = [
                    {"argv": ["check"], "claim_ids": [], "method": "test"},
                    {"argv": ["check"], "claim_ids": [], "method": "test"},
                ]
            return result

    snapshot, worker, github = run(repo, tmp_path, agent=DuplicateAgent())
    assert github.published, snapshot
    assert worker.commands == [["check"]]
    checks = next(e for e in snapshot["evidence"] if e.get("check_key"))
    assert len(checks["checks"]) == 1
    coordinator = Coordinator(repo, Store(tmp_path / "other/state.db"), Agent(), Worker(), GitHub())
    coordinator.config = {"local_checks": [["check"]], "worker_checks": []}
    with pytest.raises(ValueError, match="invalid verification command"):
        coordinator._check_plan([[]])


@pytest.mark.parametrize("mode", ["corrected", "unchanged", "mandatory"])
def test_failed_prechecks_return_bounded_feedback_to_original_generator(repo, tmp_path, mode):
    class ChecksWorker(Worker):
        def run(self, repo, rev, argv, *, timeout_seconds):
            self.revisions.append(rev)
            self.commands.append(argv)
            failed = argv == ["bad-command"] or (mode == "mandatory" and argv == ["check"])
            return {
                "status": "failed" if failed else "succeeded",
                "returncode": 127 if failed else 0,
                "revision": rev,
                "tools": {"python": "test"},
                "stdout": "",
                "stderr": "command not found" if failed else "",
            }

    class RetryingAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job["payload"]
            if payload["kind"] == "generate_verification":
                round_number = payload.get("round", 0)
                if round_number == 0:
                    result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
                else:
                    feedback = payload["feedback"]["check_summary"]
                    failed = next(item for item in feedback["checks"] if item["status"] != "passed")
                    assert failed["command"] == (
                        ["check"] if mode == "mandatory" else ["bad-command"]
                    )
                    assert "command not found" in failed["stderr_excerpt"]
                    assert any(item["command"] == ["check"] for item in feedback["checks"])
                    result = {
                        "status": "succeeded",
                        "session_id": job["id"],
                        "result": {
                            "decision": "accept",
                            "summary": "revised requests",
                            "findings": [],
                            "verification_requests": [],
                        },
                    }
                if mode != "mandatory" and (mode == "unchanged" or round_number == 0):
                    result["result"]["verification_requests"] = [
                        {"argv": ["bad-command"], "claim_ids": [], "method": "test"}
                    ]
                return result
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, worker, github = run(repo, tmp_path, agent=RetryingAgent(), worker=ChecksWorker())
    generations = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_verification"]
    assert len(generations) == (2 if mode == "corrected" else 3), snapshot
    assert {j["item_id"] for j in generations} == {"verification.inventory"}
    assert worker.commands.count(["check"]) == len(generations)
    if mode == "corrected":
        assert github.published
        assert worker.commands.count(["bad-command"]) == 1
        assert (
            len([j for j in snapshot["jobs"] if j["payload"]["kind"] == "review_verification"]) == 2
        )
        assert len([j for j in snapshot["jobs"] if j["payload"]["kind"] == "review_final"]) == 2
    else:
        assert not github.published
        assert not any(
            j["payload"]["kind"] in {"review_verification", "review_final"}
            for j in snapshot["jobs"]
        )
        assert any(
            f["status"] == "inconclusive"
            and f["summary"] == "Protected checks did not pass on the proposed commit"
            for f in snapshot["findings"]
        )


def test_managed_gate_instructions_cover_all_generators(repo, tmp_path):
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    session = {"head": "head", "base": "base"}
    for kind in ("generate_spec", "generate_verification", "investigate_defect"):
        prompt = coordinator._prompt({"loop": "iil", "payload": {"kind": kind}}, session)
        assert "controller automatically runs configured completion gates" in prompt
        assert "only additional executable commands" in prompt
        assert "not describe a target or formal proof" in prompt
        assert "empty verification_requests list and accept" in prompt
        assert "do not defer merely because controller-managed evidence is pending" in prompt
    repair_prompt = coordinator._prompt(
        {"loop": "iil", "payload": {"kind": "investigate_defect"}}, session
    )
    assert "correction or removal of your invalid additional verification requests" in repair_prompt
    assert "keep mandatory plans unchanged" in repair_prompt
    assert "accept with changes=[]" in repair_prompt


def test_source_context_only_requires_diff_for_actual_proposal(repo, tmp_path):
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    head = git(repo, "rev-parse", "HEAD")
    for kind in (
        "generate_verification",
        "investigate_defect",
        "adjudicate_defect",
        "adjudicate_domain",
    ):
        context, _, _, _ = coordinator._source_context(head, {"payload": {"kind": kind}})
        assert "No proposal diff is supplied" in context
        assert "Review the complete proposal_diff" not in context
        assert "proposal_diff" not in json.loads(context.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
    context, _, _, _ = coordinator._source_context(
        head, {"payload": {"kind": "review_fix", "proposal": {"parent": head}}}
    )
    assert "Review the complete proposal_diff against proposal_base" in context
    assert "proposal_diff" in json.loads(context.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
