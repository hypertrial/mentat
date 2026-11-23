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


def run(repo, tmp_path, agent=None, worker=None, concurrency=3):
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
        "concurrency": concurrency,
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
        (True, "chained_refresh"),
        (True, "chained_retry"),
        (True, "changed_requirement"),
        (True, "changed_cas"),
        (True, "ancestral_refresh"),
        (True, "repair_retry"),
        (True, "verification_repair_retry"),
        (True, "out_of_domain"),
        (True, "rebound"),
    ],
)
def test_counterexample_requires_protected_reproduction(repo, tmp_path, protected, domain, monkeypatch):
    checkpoint_domains = {"chained_refresh", "chained_retry", "ancestral_refresh", "changed_requirement", "changed_cas"}
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
    if domain in checkpoint_domains:
        config["concurrency"] = 1
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

    fix_review_prompts = []
    spec_review_contexts = []
    final_contexts = []
    repair_inputs = []

    class FixAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            if domain in checkpoint_domains and job["payload"]["kind"] in {"review_spec", "review_final"}:
                context = json.loads(job["payload"]["prompt"].split("SOURCE_CONTEXT_JSON:\n", 1)[1])
                (spec_review_contexts if job["payload"]["kind"] == "review_spec" else final_contexts).append(
                    (job["payload"], context))
            if job["payload"]["kind"] in {"review_fix", "review_verification", "review_final"}:
                if job["payload"]["kind"] == "review_fix":
                    fix_review_prompts.append(job["payload"]["prompt"])
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
                if domain in checkpoint_domains and not job["payload"].get("repair"):
                    path = repo / ".mentat/fas.json"
                    fas = json.loads(path.read_text())
                    next(row for row in fas["surfaces"] if row["path"] == "src.py").update(
                        status="mapped", requirement_ids=[requirements[0]["id"]])
                    if domain == "changed_requirement":
                        from mentat.autonomy import digest

                        record = fas["requirements"][0]
                        record["revision"] = {"version": 2, "previous_digest": digest(record),
                            "reason": "Changed requirement", "evidence": [{"path": "src.py"}]}
                        record["statement"] = "The function increments only nonnegative inputs"
                    if domain == "changed_cas":
                        spec = repo / "openspec/specs/application/spec.md"
                        spec.write_text(spec.read_text() + "\nNew semantic qualification\n")
                    path.write_text(json.dumps(fas))
                if job["payload"].get("repair"):
                    path = repo / ".mentat/fas.json"
                    fas = json.loads(path.read_text())
                    existing = {row["path"] for row in fas["surfaces"]}
                    missing = set(git(repo, "ls-files").splitlines()) - existing
                    if missing:
                        fas["surfaces"].extend(
                            {"path": p, "status": "unexamined", "requirement_ids": []}
                            for p in sorted(missing)
                        )
                        path.write_text(json.dumps(fas))
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept" if job["payload"].get("repair") or domain in checkpoint_domains else "defer",
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
                        "decision": "accept"
                        if domain in {"accept", *checkpoint_domains, "repair_retry", "verification_repair_retry"}
                        else "defer",
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
                repair_inputs.append((job["payload"], git(repo, "rev-parse", "HEAD")))
                if job["payload"].get("round", 0) == 0:
                    (repo / "src.py").write_text("def f(x):\n    return x + 1\n")
                else:
                    assert all(
                        k in job["payload"] for k in ("requirement", "evidence", "reproduction")
                    )
                    failed = job["payload"]["feedback"]["check_summary"]["checks"][0]
                    assert failed["command"] == ["bad-repair-command"]
                    assert "not found" in failed["stderr_excerpt"]
                    (repo / "src.py").write_text("def f(x):\n    return x + 1  # corrected\n")
            if (
                job["payload"]["kind"] == "generate_verification"
                and (repo / "verification").exists()
            ):
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept",
                        "summary": "existing verification",
                        "findings": [],
                        "verification_requests": [
                            {"argv": ["sidecar-check"], "claim_ids": [], "method": "test"}
                        ],
                    },
                }
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if (
                job["payload"]["kind"] == "investigate_defect"
                and domain in {"repair_retry", "chained_retry", "verification_repair_retry"}
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
            if argv == ["bad-repair-command"] and (
                domain in {"repair_retry", "chained_retry"} or self.commands.count(argv) == 2
            ):
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

    if domain == "ancestral_refresh":
        # Model a scheduler that starts the fix from the already reviewed SCL
        # checkpoint. All generator/reviewer jobs and their events remain real.
        enqueue = Coordinator._enqueue

        def start_fix_at_reviewed_checkpoint(coordinator, loop, role, item, payload):
            if payload.get("kind") == "investigate_defect":
                events = coordinator.store.snapshot(coordinator.session_id)["events"]
                checkpoint = [e["payload"]["head"] for e in events if e["kind"] == "fas_reviewed"][-1]
                payload = {**payload, "target_head": checkpoint}
            return enqueue(coordinator, loop, role, item, payload)

        monkeypatch.setattr(Coordinator, "_enqueue", start_fix_at_reviewed_checkpoint)
    snapshot, worker, github = run(
        repo, tmp_path, agent=FixAgent(), worker=ProtectedWorker() if protected else Worker(),
        concurrency=1 if domain in checkpoint_domains else 3,
    )
    if domain == "changed_requirement":
        assert any(e["kind"] == "fas_reviewed" for e in snapshot["events"])
        assert not repair_inputs and not github.published
        assert snapshot["session"]["status"] == "blocked"
        return
    assert any(j["payload"]["kind"] == "adjudicate_defect" for j in snapshot["jobs"])
    assert any(j["payload"]["kind"] == "reproduce_defect" for j in snapshot["jobs"])
    fix_expected = protected and domain in {"accept", *checkpoint_domains, "repair_retry", "verification_repair_retry"}
    assert any(j["payload"]["kind"] == "adjudicate_domain" for j in snapshot["jobs"]) is protected
    assert (
        any(j["payload"]["kind"] == "investigate_defect" for j in snapshot["jobs"]) is fix_expected
    )
    if domain in {"changed_requirement", "changed_cas"}:
        accepted = [event["payload"]["head"] for event in snapshot["events"]
                    if event["kind"] == "fas_reviewed"]
        assert accepted and accepted[0] != snapshot["session"]["base"]
        assert not repair_inputs
        assert not github.published
        assert snapshot["session"]["status"] == "blocked"
        assert any(finding.get("status") in {"gap", "inconclusive"} and
                   ("checkpoint" in finding.get("summary", "").lower() or
                    "repair" in finding.get("summary", "").lower())
                   for finding in snapshot["findings"])
        return
    if fix_expected:
        assert github.published and worker.revisions[-1] == github.published[0], snapshot[
            "findings"
        ]
        if domain not in {"verification_repair_retry", "chained_refresh", "chained_retry", "ancestral_refresh"}:
            assert len(worker.revisions) == (5 if domain == "repair_retry" else 3)
            assert worker.commands.count(["check"]) == (3 if domain == "repair_retry" else 2)
            assert worker.commands.count(["sidecar-check"]) == 1
        if domain in {"repair_retry", "chained_retry", "verification_repair_retry"}:
            assert worker.commands.count(["bad-repair-command"]) == (
                1 if domain in {"repair_retry", "chained_retry"} else 2
            )
            fix_jobs = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "investigate_defect"]
            assert len(fix_jobs) == 2 and len({j["item_id"] for j in fix_jobs}) == 1
            assert len(fix_review_prompts) == (2 if domain in {"repair_retry", "chained_retry"} else 4)
            for prompt in fix_review_prompts[-2:]:
                context = json.loads(prompt.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
                assert "return x - 1" in context["proposal_diff"]
                assert "return x + 1  # corrected" in context["proposal_diff"]
        assert any(
            j["payload"]["kind"] == "generate_spec" and j["payload"].get("repair")
            for j in snapshot["jobs"]
        )
        assert any(
            j["payload"]["kind"] == "generate_verification" and j["payload"].get("repair")
            for j in snapshot["jobs"]
        )
        if domain in checkpoint_domains:
            import hashlib

            first_checkpoint = next(e["payload"]["head"] for e in snapshot["events"]
                                    if e["kind"] == "fas_reviewed")
            fix = next(j for j in snapshot["jobs"] if j["payload"]["kind"] == "investigate_defect")
            assert repair_inputs[0][1] == first_checkpoint
            assert fix["outcome"]["observed_source_revision"] == first_checkpoint
            assert git(repo, "merge-base", first_checkpoint, fix["outcome"]["head"]) == first_checkpoint
            assert snapshot["session"]["base"] == fix["payload"]["evidence"]["source_identity"]["revision"]
            assert repair_inputs[0][0]["evidence"] == fix["payload"]["evidence"]
            assert repair_inputs[0][0]["reproduction"] == fix["payload"]["reproduction"]
            domain_review = next(j for j in snapshot["jobs"] if j["payload"]["kind"] == "adjudicate_domain")
            assert domain_review["payload"]["evidence"] == fix["payload"]["evidence"]
            assert domain_review["payload"]["reproduction"] == fix["payload"]["reproduction"]
            entailment = next(event["payload"] for event in snapshot["events"]
                              if event["kind"] == "defect_adjudicated")
            domain_event = next(event["payload"] for event in snapshot["events"]
                                if event["kind"] == "domain_adjudicated")
            assert entailment["reviewer_id"] == fix["payload"]["adjudicator_id"]
            assert entailment["evidence_id"] == fix["payload"]["evidence"]["id"]
            assert domain_event["reviewer_id"] == domain_review["outcome"]["author_id"]
            assert domain_event["defect"]["evidence_id"] == entailment["evidence_id"]
            assert domain_event["reviewer_id"] != entailment["reviewer_id"]
            if domain == "chained_retry":
                fix_jobs = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "investigate_defect"]
                assert len(fix_jobs) == len(repair_inputs) == 2
                first_head = fix_jobs[0]["outcome"]["head"]
                assert repair_inputs[1][1] == first_head
                assert git(repo, "merge-base", first_head, fix_jobs[1]["outcome"]["head"]) == first_head
                assert all(payload["evidence"] == fix["payload"]["evidence"] and
                           payload["reproduction"] == fix["payload"]["reproduction"]
                           for payload, _ in repair_inputs)
            assert len(spec_review_contexts) >= 4
            fresh = [(payload, context) for payload, context in spec_review_contexts
                     if "return x + 1" in context["files"].get("src.py", "")]
            assert len(fresh) == 2
            for payload, context in fresh:
                assert payload["proposal"]["head"] == context["revision"]
                assert payload["correspondence_refresh"]["source_paths"] == ["src.py"]
                assert payload["correspondence_refresh"]["checkpoint"] != context["revision"]
                fixed = ("def f(x):\n    return x + 1  # corrected\n" if domain == "chained_retry"
                         else "def f(x):\n    return x + 1\n")
                assert context["files"]["src.py"] == fixed
            assert len(final_contexts) == 2
            expected = hashlib.sha256(fixed.encode()).hexdigest()
            for _, context in final_contexts:
                assert context["reviewed_specification"]["source_blobs_sha256"]["src.py"] == expected
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
    base = git(repo, "rev-parse", "HEAD")
    (repo / "src.py").write_text("def value(): return 2\n")
    git(repo, "commit", "-qam", "feature source")
    head = git(repo, "rev-parse", "HEAD")
    coordinator.session_id = store.new_session(str(repo), base, head, config, 1, 10)
    store.append_event(
        coordinator.session_id, "human_feedback", {"message": "Review this requirement"}
    )
    coordinator._consume_feedback()
    coordinator._consume_feedback()
    snapshot = store.snapshot(coordinator.session_id)
    assert sum(e["kind"] == "feedback_consumed" for e in snapshot["events"]) == 1
    assert len(snapshot["jobs"]) == 1
    assert snapshot["jobs"][0]["payload"]["target_head"] == head
    assert snapshot["jobs"][0]["payload"]["review_base"] == base


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
    with_cases = deepcopy(snapshot)
    with_cases["session"]["policy"]["source_checks"][0]["counterexample_cases"] = [
        {"path": "bad.py", "function": "f"}]
    with_cases["session"]["policy"]["source_checks"][0]["timeout_ms"] = 750
    with_cases["session"]["policy"]["source_checks"].append(
        {**contract, "claim_id": "different-claim", "ensures": "result >= x"})
    assert project_snapshot(with_cases, identity=identity)["claims"][0]["status"] == "established-within-scope"
    # A negative control can never replace the claim's independently proved source.
    with_cases["evidence"] = [{**proof, "id": "control", "status": "counterexample",
                                "source_identity": {**proof["source_identity"], "path": "bad.py"}}]
    assert project_snapshot(with_cases, identity=identity)["claims"][0]["status"] != "established-within-scope"
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

    proof_reviews = []

    class ClaimAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            if job["payload"]["kind"] in {"review_verification", "review_final"}:
                payload = job["payload"]
                context = json.loads(payload["prompt"].split("SOURCE_CONTEXT_JSON:\n", 1)[1])
                proof_reviews.append((payload["kind"], payload, context))
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
    for kind in ("review_verification", "review_final"):
        pair = [(payload, context) for stage, payload, context in proof_reviews if stage == kind]
        assert len(pair) == 2
        for payload, context in pair:
            assert context["files"]["src.py"] == "def f(x):\n    return x + 1\n"
            assert any(check["path"] == "src.py" and check["ensures"] == contract["ensures"]
                       for check in context["mandatory_source_contracts"])
            assert "exact typed input" in context["files"][".mentat/fas.json"]
            assert context["trusted_source_verifier"]["source"]
            assert context["trusted_source_verifier"]["identity"]["frontend_sha256"]
            assert payload["check_summary"]["head"] == context["revision"]
            assert any(proof.get("source_identity", {}).get("path") == "src.py"
                       for proof in payload["check_summary"]["source"])


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


@pytest.mark.parametrize("revise_record", [False, True])
def test_revision_feedback_regenerates_same_item_with_bounded_round(repo, tmp_path, revise_record):
    review_prompts = []

    class RevisingAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job["payload"]
            if payload["kind"] == "generate_spec" and payload.get("round") == 1:
                spec = repo / "openspec/specs/application/spec.md"
                spec.write_text(spec.read_text() + "\nReviewed clarification\n")
                if revise_record:
                    from mentat.autonomy import digest

                    path = repo / ".mentat/fas.json"
                    fas = json.loads(path.read_text())
                    record = fas["requirements"][0]
                    previous = digest(record)
                    record["statement"] = "Clarified uncertainty"
                    record["revision"] = {
                        "version": 2,
                        "previous_digest": previous,
                        "reason": "Clarify the draft",
                        "evidence": [{"path": "src.py"}],
                    }
                    path.write_text(json.dumps(fas))
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
            if payload["kind"] == "review_spec" and payload["proposal"].get("round") == 1:
                review_prompts.append(payload["prompt"])
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
    reviews = [
        j
        for j in snapshot["jobs"]
        if j["payload"]["kind"] == "review_spec" and j["payload"]["proposal"].get("round") == 1
    ]
    assert len(reviews) == 2, snapshot
    assert len(review_prompts) == 2
    for prompt in review_prompts:
        context = json.loads(prompt.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
        assert "openspec/specs/application/spec.md" in context["proposal_diff"]
        assert ".mentat/fas.json" in context["proposal_diff"]
    proposal = reviews[0]["payload"]["proposal"]
    assert proposal["parent"] == snapshot["session"]["base"]
    changed = git(repo, "diff", "--name-only", proposal["parent"], proposal["head"])
    assert "openspec/specs/application/spec.md" in changed
    assert ".mentat/fas.json" in changed
    fas = json.loads(git(repo, "show", f"{proposal['head']}:.mentat/fas.json"))
    rows = {row["path"]: row for row in fas["surfaces"]}
    assert (
        set(git(repo, "ls-tree", "-r", "--name-only", proposal["head"]).splitlines()) <= rows.keys()
    )
    for path in (".mentat/fas.json", "openspec/specs/application/spec.md"):
        assert rows[path] == {"path": path, "status": "unexamined", "requirement_ids": []}


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

    _valid_publication_spec(repo)

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

    _valid_publication_spec(repo)

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


def test_large_fas_is_reserved_with_complete_lineage_and_review_diff(repo, tmp_path):
    from mentat.autonomy import digest

    record = {
        "id": str(uuid.uuid4()),
        "area": "product",
        "origin": "uncertain",
        "statement": "a" * 70_000,
        "spec_path": "openspec/specs/app/spec.md",
    }
    fas = {"schema_version": 1, "requirements": [record], "claims": [], "surfaces": []}
    path = repo / ".mentat/fas.json"
    path.write_text(json.dumps(fas))
    folder = repo / "src"
    folder.mkdir()
    for index in range(10):
        (folder / f"file{index}.py").write_text("# " + "x" * 44990 + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "large fas")
    base = git(repo, "rev-parse", "HEAD")
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    text, omitted, included, deferred = coordinator._source_context(
        base, {"payload": {"kind": "generate_spec"}}
    )
    context = json.loads(text.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
    assert context["files"][".mentat/fas.json"] == path.read_text()
    assert context["prior_revision_index"][record["id"]] == {"digest": digest(record), "version": 1}
    assert ".mentat/fas.json" in included and ".mentat/fas.json" not in omitted
    assert ".mentat/workspace.json" in included
    assert deferred
    assert len(json.dumps(context, ensure_ascii=True)) <= 500_000

    fas["requirements"][0]["statement"] = "b" * 70_000
    path.write_text(json.dumps(fas))
    git(repo, "add", ".mentat/fas.json")
    git(repo, "commit", "-qm", "revise fas")
    head = git(repo, "rev-parse", "HEAD")
    text, _, _, _ = coordinator._source_context(
        head, {"payload": {"kind": "review_spec", "proposal": {"parent": base}}}
    )
    context = json.loads(text.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
    assert context["files"][".mentat/fas.json"] == path.read_text()
    assert len(context["proposal_diff"]) > 100_000
    assert len(json.dumps(context, ensure_ascii=True)) <= 500_000


def test_required_fas_over_capacity_fails_explicitly(repo, tmp_path):
    path = repo / ".mentat/fas.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "requirements": [{"id": str(uuid.uuid4()), "statement": "a" * 240_000}],
                "claims": [],
                "surfaces": [],
            }
        )
    )
    git(repo, "add", ".mentat/fas.json")
    git(repo, "commit", "-qm", "oversized fas")
    head = git(repo, "rev-parse", "HEAD")
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    with pytest.raises(ValueError, match="required FAS.*context capacity"):
        coordinator._source_context(head, {"payload": {"kind": "generate_spec"}})


@pytest.mark.parametrize("mandatory", [".mentat/workspace.json", "openspec/specs/app/spec.md"])
def test_required_configuration_and_openspec_are_not_silently_omitted(repo, tmp_path, mandatory):
    path = repo / mandatory
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a" * 70_000)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "large required context")
    head = git(repo, "rev-parse", "HEAD")
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    text, omitted, included, _ = coordinator._source_context(
        head, {"payload": {"kind": "generate_spec", "targetfiles": ["src.py"]}}
    )
    context = json.loads(text.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
    assert context["files"][mandatory] == path.read_text()
    assert mandatory in included and mandatory not in omitted
    path.write_text("a" * 240_001)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "oversized required context")
    head = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="required configuration or OpenSpec.*capacity"):
        coordinator._source_context(head, {"payload": {"kind": "generate_spec"}})


def test_required_documents_over_budget_fail_before_optional_source(repo, tmp_path):
    fas = {
        "schema_version": 1,
        "requirements": [{"id": str(uuid.uuid4()), "statement": "a" * 200_000}],
        "claims": [],
        "surfaces": [],
    }
    (repo / ".mentat/fas.json").write_text(json.dumps(fas))
    spec = repo / "openspec/specs/app/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("s" * 170_000)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "required context exceeds budget")
    head = git(repo, "rev-parse", "HEAD")
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    with pytest.raises(ValueError, match="required configuration or OpenSpec.*capacity"):
        coordinator._source_context(head, {"payload": {"kind": "generate_spec"}})


def test_targeted_large_source_progress_and_unsupported_size(repo, tmp_path):
    source = repo / "src.py"
    source.write_text("# " + "x" * 112_000)
    giant = repo / "too_large.py"
    giant.write_text("# " + "x" * 240_001)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "large application sources")
    head = git(repo, "rev-parse", "HEAD")
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    text, omitted, _, deferred = coordinator._source_context(
        head, {"payload": {"kind": "generate_spec"}}
    )
    context = json.loads(text.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
    assert "src.py" in omitted and "src.py" in deferred
    assert "too_large.py" in omitted and "too_large.py" not in deferred
    assert any(
        item["path"] == "too_large.py" and "240000" in item["reason"] for item in context["omitted"]
    )
    text, _, included, _ = coordinator._source_context(
        head, {"payload": {"kind": "generate_spec", "targetfiles": ["src.py"]}}
    )
    assert "src.py" in included
    assert (
        json.loads(text.split("SOURCE_CONTEXT_JSON:\n", 1)[1])["files"]["src.py"]
        == source.read_text()
    )
    with pytest.raises(ValueError, match="cannot make progress within capacity"):
        coordinator._source_context(
            head, {"payload": {"kind": "generate_spec", "targetfiles": ["too_large.py"]}}
        )
    source.write_text(source.read_text() + "\n# reviewed change\n")
    git(repo, "add", "src.py")
    git(repo, "commit", "-qm", "change large source")
    changed_head = git(repo, "rev-parse", "HEAD")
    text, _, included, _ = coordinator._source_context(
        changed_head, {"payload": {"kind": "review_fix", "proposal": {"parent": head}}}
    )
    assert "src.py" in included
    assert (
        json.loads(text.split("SOURCE_CONTEXT_JSON:\n", 1)[1])["files"]["src.py"]
        == source.read_text()
    )


def test_targeted_chunk_without_remaining_capacity_fails_explicitly(repo, tmp_path):
    fas = {
        "schema_version": 1,
        "requirements": [{"id": str(uuid.uuid4()), "statement": "a" * 200_000}],
        "claims": [],
        "surfaces": [],
    }
    (repo / ".mentat/fas.json").write_text(json.dumps(fas))
    spec = repo / "openspec/specs/app/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("s" * 150_000)
    (repo / "src.py").write_text("# " + "x" * 20_000)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "target has no remaining capacity")
    head = git(repo, "rev-parse", "HEAD")
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    with pytest.raises(ValueError, match="cannot make progress within capacity"):
        coordinator._source_context(
            head, {"payload": {"kind": "generate_spec", "targetfiles": ["src.py"]}}
        )


@pytest.mark.parametrize("revise", [False, True])
def test_deferred_partial_spec_continues_and_revisits_uncommitted_batch(repo, tmp_path, revise):
    folder = repo / "src"
    folder.mkdir()
    for index in range(30):
        (folder / f"file{index}.py").write_text("# " + "x" * 44990 + "\n")
    (repo / "too_large.py").write_text("# " + "x" * 240_001)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "large source inventory")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))

    retry_review_prompts = []

    class DeferredAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job["payload"]
            if payload["kind"] == "generate_spec" and not payload.get("targetfiles"):
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "defer",
                        "summary": "omitted files need batches",
                        "findings": [],
                        "verification_requests": [],
                    },
                }
            if (
                revise
                and payload["kind"] == "generate_spec"
                and payload.get("round") == 1
                and payload.get("chunk") == 1
            ):
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "defer",
                        "summary": "continue revised draft",
                        "findings": [],
                        "verification_requests": [],
                    },
                }
            if payload["kind"] == "generate_spec" and (repo / ".mentat/fas.json").is_file():
                if revise and payload.get("round") == 1:
                    spec = repo / "openspec/specs/application/spec.md"
                    spec.write_text(spec.read_text() + "\nNext batch clarification\n")
                path = repo / ".mentat/fas.json"
                fas = json.loads(path.read_text())
                existing = {surface["path"] for surface in fas["surfaces"]}
                for surface in payload["surfaces"]:
                    if surface["path"] not in existing:
                        fas["surfaces"].append(
                            {"path": surface["path"], "status": "gap", "requirement_ids": []}
                        )
                path.write_text(json.dumps(fas))
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept",
                        "summary": "reviewed partial inventory",
                        "findings": [],
                        "verification_requests": [],
                    },
                }
            if payload["kind"] == "review_spec" and payload["proposal"].get("round") == 1:
                retry_review_prompts.append(payload["prompt"])
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if (
                revise
                and payload["kind"] == "review_spec"
                and payload.get("chunk") == 1
                and payload["proposal"].get("round") == 0
            ):
                result["result"]["decision"] = "revise"
                result["result"]["findings"] = [{"summary": "Clarify this batch"}]
            return result

    snapshot, _, github = run(repo, tmp_path, agent=DeferredAgent())
    jobs = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_spec"]
    assert len(jobs) >= 3, snapshot
    assert jobs[0]["outcome"]["result"]["decision"] == "defer"
    assert jobs[1]["payload"]["targetfiles"]
    assert jobs[1]["payload"]["revisit_files"]
    assert "too_large.py" in jobs[1]["payload"]["unavailable_files"]
    if revise:
        retries = [j for j in jobs if j["payload"].get("round") == 1]
        assert retries
        for key in ("targetfiles", "chunk", "batch_limit", "revisit_files", "unavailable_files"):
            assert retries[0]["payload"][key] == jobs[1]["payload"][key]
        assert retries[0]["outcome"]["result"]["decision"] == "defer"
        assert len(retry_review_prompts) == 2, [
            (j["payload"].get("kind"), j["payload"].get("chunk"), j["status"], j.get("outcome"))
            for j in jobs
        ]
        for prompt in retry_review_prompts:
            context = json.loads(prompt.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
            assert "openspec/specs/application/spec.md" in context["proposal_diff"]
            assert "Next batch clarification" in context["proposal_diff"]
            assert ".mentat/fas.json" in context["proposal_diff"]
    revisited = set(jobs[1]["payload"]["revisit_files"])
    later_targets = set().union(*(set(j["payload"].get("targetfiles", [])) for j in jobs[2:]))
    assert revisited <= later_targets
    assert any(f["status"] == "awaiting-context" for f in snapshot["findings"])
    assert (
        sum(
            j["payload"]["kind"] == "review_spec" and j["status"] == "succeeded"
            for j in snapshot["jobs"]
        )
        >= 2
    )
    assert github.published, snapshot


@pytest.mark.parametrize("pending", [[], ["src.py"]])
def test_deferred_spec_without_new_context_is_explicitly_inconclusive(repo, tmp_path, pending):
    store = Store(tmp_path / "state/state.db")
    config = default_config(repo)
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = config
    head = git(repo, "rev-parse", "HEAD")
    coordinator.session_id = store.new_session(str(repo), head, head, config, 1, 100)
    job = {
        "id": "deferred",
        "item_id": "cas.inventory",
        "loop": "scl",
        "payload": {
            "kind": "generate_spec",
            "targetfiles": ["src.py"],
            "chunk": 1,
            "batch_limit": 3,
        },
    }
    outcome = {
        "head": head,
        "context_pending": pending,
        "context_included": [],
        "result": {"decision": "defer", "summary": "unsupported capability", "findings": []},
    }
    coordinator._advance(job, outcome)
    snapshot = store.snapshot(coordinator.session_id)
    assert not snapshot["jobs"]
    assert len(snapshot["findings"]) == 1
    assert snapshot["findings"][0]["status"] == "inconclusive"


def test_spec_prompts_allow_reviewed_incremental_batches(repo, tmp_path):
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    session = {"head": "head", "base": "base"}
    draft = coordinator._prompt({"loop": "scl", "payload": {"kind": "generate_spec"}}, session)
    review = coordinator._prompt({"loop": "scl", "payload": {"kind": "review_spec"}}, session)
    assert "Do not defer the whole batch solely because files are omitted" in draft
    assert "keep omitted surfaces unexamined or gap" in draft
    assert "Accept a defensible partial draft" in review
    assert "Reject fabricated mappings" in review


def test_deferred_spec_stops_at_fixed_batch_limit(repo, tmp_path):
    store = Store(tmp_path / "state/state.db")
    config = default_config(repo)
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = config
    head = git(repo, "rev-parse", "HEAD")
    coordinator.session_id = store.new_session(str(repo), head, head, config, 1, 100)
    job = {
        "id": "deferred-limit",
        "item_id": "cas.inventory",
        "loop": "scl",
        "payload": {
            "kind": "generate_spec",
            "targetfiles": ["src.py", "other.py"],
            "chunk": 2,
            "batch_limit": 2,
        },
    }
    outcome = {
        "head": head,
        "context_pending": ["src.py"],
        "context_included": ["other.py"],
        "result": {"decision": "defer", "summary": "needs context", "findings": []},
    }
    coordinator._advance(job, outcome)
    snapshot = store.snapshot(coordinator.session_id)
    assert not snapshot["jobs"]
    assert snapshot["findings"][0]["status"] == "inconclusive"
    assert "bounded progress" in snapshot["findings"][0]["summary"]


@pytest.mark.parametrize(
    "mutation", ["unchanged", "surface", "requirement", "claim", "contract", "new_mapping", "unsupported"]
)
def test_budget_deferred_prior_mapping_requires_unchanged_lineage_and_is_revisited(
    repo, tmp_path, mutation
):
    from mentat.autonomy import digest

    folder = repo / "src"
    folder.mkdir()
    for index in range(10):
        (folder / f"file{index}.py").write_text("# " + "x" * 44990 + "\n")
    (folder / "z_mapped.py").write_text(
        "# " + "z" * (240_001 if mutation == "unsupported" else 44990) + "\ndef known(): return 1\n"
    )
    requirements = [
        {
            "id": str(uuid.uuid4()),
            "area": area,
            "origin": "uncertain",
            "statement": "existing intent",
            "spec_path": "openspec/specs/app/spec.md",
        }
        for area in AREAS
    ]
    spec = repo / "openspec/specs/app/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("\n".join(r["id"] for r in requirements))
    paths = [
        ".mentat/workspace.json",
        ".mentat/fas.json",
        "src.py",
        "openspec/specs/app/spec.md",
        *[f"src/file{i}.py" for i in range(10)],
        "src/z_mapped.py",
    ]
    surfaces = [
        {
            "path": path,
            "status": "mapped" if path == "src/z_mapped.py" else "gap",
            "requirement_ids": [requirements[0]["id"]] if path == "src/z_mapped.py" else [],
        }
        for path in paths
    ]
    (repo / ".mentat/fas.json").write_text(
        json.dumps(
            {"schema_version": 1, "requirements": requirements, "claims": [], "surfaces": surfaces}
        )
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "mapped prior source")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))

    class MappingAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job["payload"]
            if payload["kind"] == "generate_spec":
                path = repo / ".mentat/fas.json"
                fas = json.loads(path.read_text())
                if not payload.get("targetfiles"):
                    context = json.loads(payload["prompt"].split("SOURCE_CONTEXT_JSON:\n", 1)[1])
                    assert any(
                        item["path"] == "src/z_mapped.py"
                        and item["reason"]
                        == (
                            "file exceeds supported 240000-byte limit"
                            if mutation == "unsupported"
                            else "context budget exhausted"
                        )
                        for item in context["omitted"]
                    )
                    if mutation == "surface":
                        next(item for item in fas["surfaces"] if item["path"] == "src/z_mapped.py")[
                            "note"
                        ] = "changed"
                    elif mutation == "new_mapping":
                        entry = next(
                            item for item in fas["surfaces"] if item["path"] == "src/file9.py"
                        )
                        entry.update(status="mapped", requirement_ids=[requirements[0]["id"]])
                    elif mutation == "claim":
                        fas["claims"].append({
                            "id": str(uuid.uuid4()), "requirement_ids": [requirements[0]["id"]],
                            "statement": "A new obligation", "assumptions": [],
                            "status": "unattempted", "formal_meaning": "unknown",
                        })
                    elif mutation == "contract":
                        fas["claims"].append({
                            "id": str(uuid.uuid4()), "requirement_ids": [requirements[1]["id"]],
                            "statement": "A new obligation", "assumptions": [],
                            "status": "unattempted", "formal_meaning": "unknown",
                            "formal_contract": {"path": "src/file9.py", "function": "f",
                                                "inputs": {}, "requires": "True", "ensures": "True"},
                        })
                    elif mutation == "requirement":
                        old = dict(fas["requirements"][0])
                        fas["requirements"][0]["statement"] = "changed intent"
                        fas["requirements"][0]["revision"] = {
                            "version": 2,
                            "previous_digest": digest(old),
                            "reason": "edit",
                            "evidence": ["source"],
                        }
                    path.write_text(json.dumps(fas))
                    spec = repo / "openspec/specs/app/spec.md"
                    spec.write_text(spec.read_text() + "\nclarification\n")
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept",
                        "summary": "draft",
                        "findings": [],
                        "verification_requests": [],
                    },
                }
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=MappingAgent())
    generators = [j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_spec"]
    if mutation == "unchanged":
        assert any("src/z_mapped.py" in j["payload"].get("targetfiles", []) for j in generators), (
            snapshot
        )
        assert github.published, snapshot
    else:
        assert not github.published
        assert any(
            ("unexamined source cannot support a changed formal contract" if mutation == "contract"
             else "unexamined source cannot be marked mapped") in f.get("summary", "")
            for f in snapshot["findings"]
        )


@pytest.mark.parametrize(
    ("field", "first", "second"),
    [
        ("batch_limit", 3, 4),
        ("revisit_files", ["one.py"], ["two.py"]),
        ("unavailable_files", ["a.py"], ["b.py"]),
    ],
)
def test_review_batch_scope_changes_never_pool_approvals(
    repo, tmp_path, monkeypatch, field, first, second
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
    key = coordinator._check_key("head", [], [], None)
    evidence = {
        "id": "checked",
        "status": "passed",
        "head": "head",
        "base": "base",
        "check_key": key,
        "checks": [
            {"command": ["check"], "status": "passed", "returncode": 0, "tools": {"python": "test"}}
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
        "commands": [],
        "source_checks": [],
        "repair": None,
    }
    for value in (first, second):
        generator = {
            "id": "generator",
            "item_id": "verify",
            "loop": "ivl",
            "payload": {"source_checks": [], "targetfiles": ["same.py"], "chunk": 1, field: value},
        }
        coordinator._enqueue_reviewers(generator, proposal, "review_final", evidence)
    jobs = store.snapshot(coordinator.session_id)["jobs"]
    one = [j for j in jobs if j["payload"][field] == first]
    two = [j for j in jobs if j["payload"][field] == second]
    assert len(one) == len(two) == 2
    assert one[0]["payload"]["review_cohort"] != two[0]["payload"]["review_cohort"]

    def accept(job):
        coordinator._advance(
            job,
            {
                "author_id": job["id"],
                "head": "head",
                "diff_digest": "same-diff",
                "result": {"decision": "accept", "summary": "accepted", "findings": []},
            },
        )

    accept(one[0])
    accept(two[0])
    assert not published
    accept(one[1])
    assert len(published) == 1


@pytest.mark.parametrize("fault", ["omitted", "duplicate"])
def test_new_surface_gaps_do_not_repair_invalid_parent_inventory(repo, tmp_path, fault):
    class InvalidInventoryAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if job["payload"]["kind"] == "generate_spec":
                path = repo / ".mentat/fas.json"
                fas = json.loads(path.read_text())
                if fault == "omitted":
                    fas["surfaces"] = [s for s in fas["surfaces"] if s["path"] != "src.py"]
                else:
                    fas["surfaces"].append(dict(fas["surfaces"][0]))
                path.write_text(json.dumps(fas))
            return result

    snapshot, _, _ = run(repo, tmp_path, agent=InvalidInventoryAgent())
    expected = (
        "surface inventory omits tracked paths"
        if fault == "omitted"
        else "duplicate surface inventory path"
    )
    assert any(expected in finding.get("summary", "") for finding in snapshot["findings"])
    assert not any(j["payload"]["kind"] == "review_spec" for j in snapshot["jobs"])


@pytest.mark.parametrize("delete", [False, True])
def test_existing_inventory_is_not_rewritten_for_noop_or_deletion(repo, tmp_path, delete):
    spec_path = "openspec/specs/application/spec.md"
    spec = repo / spec_path
    spec.parent.mkdir(parents=True)
    requirements = [
        {
            "id": str(uuid.uuid4()),
            "area": area,
            "origin": "uncertain",
            "statement": "Unspecified",
            "spec_path": spec_path,
        }
        for area in AREAS
    ]
    spec.write_text("\n".join(item["id"] for item in requirements))
    obsolete = repo / "openspec/specs/obsolete/spec.md"
    obsolete.parent.mkdir(parents=True)
    obsolete.write_text("Obsolete note\n")
    paths = [
        ".mentat/workspace.json",
        ".mentat/fas.json",
        "src.py",
        spec_path,
        "openspec/specs/obsolete/spec.md",
    ]
    fas = {
        "schema_version": 1,
        "requirements": requirements,
        "claims": [],
        "surfaces": [{"path": path, "status": "gap", "requirement_ids": []} for path in paths],
    }
    original = json.dumps(fas, separators=(",", ":")) + "\n"
    (repo / ".mentat/fas.json").write_text(original)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "existing inventory")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))

    class ExistingAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            if job["payload"]["kind"] == "generate_spec":
                if delete:
                    (repo / "openspec/specs/obsolete/spec.md").unlink()
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept",
                        "summary": "unchanged inventory",
                        "findings": [],
                        "verification_requests": [],
                    },
                }
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, _ = run(repo, tmp_path, agent=ExistingAgent())
    generation = next(j for j in snapshot["jobs"] if j["payload"]["kind"] == "generate_spec")
    head = generation["outcome"]["head"]
    assert git(repo, "show", f"{head}:.mentat/fas.json") == original.strip()
    tracked = git(repo, "ls-tree", "-r", "--name-only", head).splitlines()
    assert ("openspec/specs/obsolete/spec.md" not in tracked) is delete
    assert any(j["payload"]["kind"] == "review_spec" for j in snapshot["jobs"])


def test_revision_cannot_drop_previously_added_surface(repo, tmp_path):
    class DroppingAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job["payload"]
            if payload["kind"] == "generate_spec" and payload.get("round") == 1:
                path = repo / ".mentat/fas.json"
                fas = json.loads(path.read_text())
                fas["surfaces"] = [
                    row
                    for row in fas["surfaces"]
                    if row["path"] != "openspec/specs/application/spec.md"
                ]
                path.write_text(json.dumps(fas))
                return {
                    "status": "succeeded",
                    "session_id": job["id"],
                    "result": {
                        "decision": "accept",
                        "summary": "dropped surface",
                        "findings": [],
                        "verification_requests": [],
                    },
                }
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if payload["kind"] == "review_spec" and payload["proposal"].get("round") == 0:
                result["result"]["decision"] = "revise"
            return result

    snapshot, _, _ = run(repo, tmp_path, agent=DroppingAgent())
    assert any(
        "surface inventory omits tracked paths" in f.get("summary", "")
        for f in snapshot["findings"]
    )
    assert not any(
        j["payload"]["kind"] == "review_spec" and j["payload"]["proposal"].get("round") == 1
        for j in snapshot["jobs"]
    )


def test_structured_fas_patch_keeps_lineage_and_reaches_both_reviewers(repo, tmp_path):
    import hashlib

    from mentat.autonomy import digest
    from mentat.autonomy_state import record_digest

    requirements = [
        {'id': str(uuid.uuid4()), 'area': area, 'origin': 'uncertain',
         'statement': 'Unknown', 'spec_path': 'openspec/specs/application/spec.md'}
        for area in AREAS
    ]
    spec = repo / 'openspec/specs/application/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('\n'.join(r['id'] for r in requirements))
    for index in range(5):
        (repo / f'a{index}.py').write_text('# optional\n' + 'x' * 55_000)
    cited_source = repo / 'z_cited.py'
    cited_source.write_text('# cited\n' + 'x' * 60_000)
    paths = [*[f'a{index}.py' for index in range(5)], 'z_cited.py',
             'src.py', '.mentat/workspace.json', '.mentat/fas.json',
             'openspec/specs/application/spec.md']
    surfaces = [{'path': path, 'status': 'gap', 'requirement_ids': []} for path in paths]
    initial = {'schema_version': 1, 'requirements': requirements, 'claims': [], 'surfaces': surfaces}
    path = repo / '.mentat/fas.json'
    path.write_text(json.dumps(initial))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'prior specification')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    prompts = []

    class PatchAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            if payload['kind'] == 'generate_spec':
                current = (repo / '.mentat/fas.json').read_bytes()
                context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
                old = json.loads(current)['requirements'][0]
                assert context['files']['src.py'] == (repo / 'src.py').read_text()
                assert context['files']['z_cited.py'] == cited_source.read_text()
                assert context['fas_sha256'] == hashlib.sha256(current).hexdigest()
                assert context['patch_record_digest_index']['requirements'][old['id']] == record_digest(old)
                assert context['patch_record_digest_index']['surfaces']['src.py'] == record_digest(next(s for s in surfaces if s['path'] == 'src.py'))
                updated = {**old, 'statement': 'Clarified', 'revision': {
                    'version': 2, 'previous_digest': digest(old),
                    'reason': 'Reviewed source', 'evidence': ['src.py']},
                    'citations': ['z_cited.py:1']}
                patch = {'schema_version': 1, 'expected_sha256': context['fas_sha256'], 'edits': [{
                    'collection': 'requirements', 'key': old['id'],
                    'expected_digest': record_digest(old), 'record': updated}, {
                    'collection': 'surfaces', 'key': 'src.py',
                    'expected_digest': record_digest(next(s for s in surfaces if s['path'] == 'src.py')), 'record': {
                        'path': 'src.py', 'status': 'mapped', 'requirement_ids': [old['id']]}}]}
                spec = repo / 'openspec/specs/application/spec.md'
                spec.write_text(spec.read_text() + '\n' + 's' * 30_000)
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'refined', 'findings': [],
                    'verification_requests': [], 'changes': [{
                        'path': '.mentat/fas.json', 'operation': 'patch_fas',
                        'content': json.dumps(patch)}]}}
            if payload['kind'] == 'review_spec':
                prompts.append(payload['prompt'])
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=PatchAgent())
    updated = next(r for r in snapshot['requirements'] if r['id'] == requirements[0]['id'])
    assert updated['statement'] == 'Clarified', snapshot
    assert any(e['kind'] == 'record_revised' for e in snapshot['events'])
    assert len(prompts) == 2
    for prompt in prompts:
        context = json.loads(prompt.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
        assert '.mentat/fas.json' in context['proposal_diff']
        assert 'Clarified' in context['proposal_diff']
        assert context['files']['src.py'] == (repo / 'src.py').read_text()
        assert context['files']['z_cited.py'] == cited_source.read_text()
        assert any(item['reason'] == 'context budget exhausted' for item in context['omitted'])
        assert context['files']['.mentat/fas.json'] == git(repo, 'show', f"{context['proposal_head']}:.mentat/fas.json") + '\n'
    review = next(j for j in snapshot['jobs'] if j['payload']['kind'] == 'review_spec')
    candidate = json.loads(git(repo, 'show', f"{review['payload']['proposal']['head']}:.mentat/fas.json"))
    assert candidate['requirements'][1:] == requirements[1:]
    mapped = next(s for s in candidate['surfaces'] if s['path'] == 'src.py')
    assert mapped == {'path': 'src.py', 'status': 'mapped', 'requirement_ids': [requirements[0]['id']]}
    assert [s for s in candidate['surfaces'] if s['path'] != 'src.py'] == [
        s for s in surfaces if s['path'] != 'src.py']
    assert github.published


def test_spec_provenance_tracks_observed_input_across_review_retries(repo, tmp_path):
    generations = []
    reviews = []

    class ProvenanceAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            if kind == 'generate_spec':
                context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
                generations.append((payload, context, git(repo, 'rev-parse', 'HEAD')))
                if payload.get('round') == 1:
                    spec = repo / 'openspec/specs/application/spec.md'
                    spec.write_text(spec.read_text() + '\nReviewed clarification\n')
                    return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                        'decision': 'accept', 'summary': 'revised', 'findings': [],
                        'verification_requests': []}}
            if kind == 'review_spec':
                context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
                reviews.append((payload, context, git(repo, 'rev-parse', 'HEAD')))
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'review_spec' and payload['proposal'].get('round') == 0:
                result['result']['decision'] = 'revise'
                result['result']['findings'] = [{'summary': 'Clarify'}]
            return result

    snapshot, _, _ = run(repo, tmp_path, agent=ProvenanceAgent())
    assert len(generations) == 2
    assert len(reviews) == 4
    first_input = generations[0][2]
    retry_input = generations[1][2]
    assert first_input == snapshot['session']['base']
    assert retry_input != first_input
    for payload, context, checkout in generations:
        assert context['revision'] == checkout
        prompt = payload['prompt']
        assert f'task input revision {checkout}' in prompt
        assert f"Session head {snapshot['session']['base']}" in prompt
        assert 'observed file blobs or content' in prompt
        assert 'never require a document to cite its own resulting commit hash' in prompt
    for round_number, observed in [(0, first_input), (1, retry_input)]:
        pair = [(p, c, h) for p, c, h in reviews if p['proposal'].get('round') == round_number]
        assert len(pair) == 2
        for payload, context, checkout in pair:
            proposal = payload['proposal']
            assert proposal['observed_source_revision'] == observed
            assert context['observed_source_revision'] == observed
            assert context['revision'] == context['proposal_head'] == proposal['head'] == checkout
            assert context['proposal_base'] == proposal['parent'] == first_input
            assert context['proposal_diff']
            assert set(context['source_changes_since_observation']) == set(
                git(repo, 'diff', '--name-only', '--no-renames', observed, checkout).splitlines()
            )
            assert 'openspec/specs/application/spec.md' in context['source_changes_since_observation']
            assert 'src.py' not in context['source_changes_since_observation']
            prompt = payload['prompt']
            assert 'file blobs or content and scope remain unchanged' in prompt
            assert 'flag actual drift and defer' in prompt
            assert 'Never require a versioned document to cite its own resulting commit hash' in prompt


def test_review_context_rejects_observation_from_unrelated_candidate(repo, tmp_path):
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('first branch\n')
    git(repo, 'commit', '-qam', 'first')
    observed = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', '--detach', base)
    (repo / 'src.py').write_text('second branch\n')
    git(repo, 'commit', '-qam', 'second')
    candidate = git(repo, 'rev-parse', 'HEAD')
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), Agent(), Worker(), GitHub())
    with pytest.raises(ValueError, match='ancestor'):
        coordinator._source_context(candidate, {'payload': {'kind': 'review_spec', 'proposal': {
            'parent': base, 'head': candidate, 'observed_source_revision': observed}}})


@pytest.mark.parametrize("source_reason", ["mapping", "citation"])
def test_review_reserves_newly_mapped_source_before_optional_targets(repo, tmp_path, source_reason):
    source = repo / "z_source.py"
    source.write_text("# mapped source\n" + "x" * 60_000)
    for index in range(5):
        (repo / f"a{index}.py").write_text("# optional\n" + "x" * 55_000)
    fas_path = repo / ".mentat/fas.json"
    fas = {
        "schema_version": 1,
        "requirements": [],
        "claims": [],
        "surfaces": [{"path": "z_source.py", "status": "gap", "requirement_ids": []}],
    }
    fas_path.write_text(json.dumps(fas))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "source and prior fas")
    base = git(repo, "rev-parse", "HEAD")
    coordinator = Coordinator(repo, Store(tmp_path / "state/state.db"), Agent(), Worker(), GitHub())
    targets = [f"a{index}.py" for index in range(5)] + ["z_source.py"]
    _, _, included, _ = coordinator._source_context(
        base, {"payload": {"kind": "generate_spec", "targetfiles": targets}}
    )
    assert "z_source.py" in included
    if source_reason == "mapping":
        fas["surfaces"][0]["status"] = "mapped"
    else:
        fas["requirements"].append({"id": str(uuid.uuid4()), "citations": ["z_source.py#L1"]})
    fas_path.write_text(json.dumps(fas))
    spec = repo / "openspec/specs/app/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("new spec\n" + "s" * 30_000)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "map source")
    head = git(repo, "rev-parse", "HEAD")
    text, _, included, _ = coordinator._source_context(
        head,
        {"payload": {"kind": "review_spec", "targetfiles": targets, "proposal": {"parent": base}}},
    )
    context = json.loads(text.split("SOURCE_CONTEXT_JSON:\n", 1)[1])
    assert context["files"]["z_source.py"] == source.read_text()
    assert "z_source.py" in included
    assert context["files"][".mentat/fas.json"] == fas_path.read_text()
    assert context["proposal_head"] == head and context["proposal_base"] == base
    assert context["proposal_diff"]


def test_review_required_source_over_capacity_fails_closed(repo, tmp_path):
    source = repo / 'large.py'
    source.write_text('x' * 200_000)
    fas_path = repo / '.mentat/fas.json'
    fas = {'schema_version': 1, 'requirements': [], 'claims': [], 'surfaces': [
        {'path': 'large.py', 'status': 'gap', 'requirement_ids': []}]}
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'prior')
    base = git(repo, 'rev-parse', 'HEAD')
    fas['surfaces'][0]['status'] = 'mapped'
    fas_path.write_text(json.dumps(fas))
    spec = repo / 'openspec/specs/app/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('s' * 170_000)
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'map source')
    head = git(repo, 'rev-parse', 'HEAD')
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), Agent(), Worker(), GitHub())
    with pytest.raises(ValueError, match='required review source.*split or revise'):
        coordinator._source_context(head, {'payload': {
            'kind': 'review_spec', 'proposal': {'parent': base}}})


def test_review_retry_retains_mapping_and_changed_claim_source(repo, tmp_path):
    fas_path = repo / '.mentat/fas.json'
    req = {'id': str(uuid.uuid4()), 'statement': 'requirement'}
    claim = {'id': str(uuid.uuid4()), 'requirement_ids': [req['id']],
             'formal_contract': {'path': 'src.py'}}
    fas = {'schema_version': 1, 'requirements': [req], 'claims': [], 'surfaces': [
        {'path': 'src.py', 'status': 'gap', 'requirement_ids': []}]}
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'prior')
    base = git(repo, 'rev-parse', 'HEAD')
    fas['claims'].append(claim)
    fas['surfaces'][0] = {'path': 'src.py', 'status': 'mapped', 'requirement_ids': [req['id']]}
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'unreviewed mapping')
    intermediate = git(repo, 'rev-parse', 'HEAD')
    spec = repo / 'openspec/specs/app/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('retry clarification')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'retry')
    head = git(repo, 'rev-parse', 'HEAD')
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), Agent(), Worker(), GitHub())
    text, _, _, _ = coordinator._source_context(head, {'payload': {
        'kind': 'review_spec', 'targetfiles': [], 'proposal': {
            'parent': base, 'observed_source_revision': intermediate}}})
    context = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert context['files']['src.py'] == (repo / 'src.py').read_text()
    assert context['proposal_base'] == base and context['proposal_head'] == head
    assert 'unreviewed' not in context['proposal_diff']
    assert 'mapped' in context['proposal_diff'] and 'retry clarification' in context['proposal_diff']


@pytest.mark.parametrize('change', ['requirement', 'claim'])
def test_review_requires_source_for_changed_semantics_on_existing_mapping(repo, tmp_path, change):
    fas_path = repo / '.mentat/fas.json'
    req = {'id': str(uuid.uuid4()), 'statement': 'original'}
    claim = {'id': str(uuid.uuid4()), 'requirement_ids': [req['id']],
             'statement': 'original', 'formal_contract': {'path': 'src.py'}}
    fas = {'schema_version': 1, 'requirements': [req], 'claims': [claim], 'surfaces': [
        {'path': 'src.py', 'status': 'mapped', 'requirement_ids': [req['id']]}]}
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'prior')
    base = git(repo, 'rev-parse', 'HEAD')
    (req if change == 'requirement' else claim)['statement'] = 'revised'
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'revise semantics')
    head = git(repo, 'rev-parse', 'HEAD')
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), Agent(), Worker(), GitHub())
    text, _, _, _ = coordinator._source_context(head, {'payload': {
        'kind': 'review_spec', 'targetfiles': [], 'proposal': {'parent': base}}})
    context = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert context['files']['src.py'] == (repo / 'src.py').read_text()


@pytest.mark.parametrize('record_kind', ['requirements', 'claims'])
@pytest.mark.parametrize(('citation', 'error'), [
    ('missing.py', 'required review source unavailable'),
    ({'path': 'missing.py'}, 'required review source unavailable'),
    ('../outside.py', 'unsafe review citation'),
    ('/etc/passwd', 'unsafe review citation'),
    ({'unexpected': 'value'}, 'invalid review citation'),
    (42, 'invalid review citation'),
])
def test_changed_citations_fail_closed(repo, tmp_path, record_kind, citation, error):
    fas_path = repo / '.mentat/fas.json'
    fas = {'schema_version': 1, 'requirements': [], 'claims': [], 'surfaces': []}
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'prior')
    base = git(repo, 'rev-parse', 'HEAD')
    record = {'id': str(uuid.uuid4()), 'citations': [citation]}
    if record_kind == 'claims':
        record['requirement_ids'] = []
    fas[record_kind].append(record)
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'changed citation')
    head = git(repo, 'rev-parse', 'HEAD')
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), Agent(), Worker(), GitHub())
    from mentat.autonomy import ReviewContextCapacity

    with pytest.raises(ValueError, match=error) as exc:
        coordinator._source_context(head, {'payload': {
            'kind': 'review_spec', 'proposal': {'parent': base}}})
    assert not isinstance(exc.value, ReviewContextCapacity)


@pytest.mark.parametrize('citations', [None, 'src.py', {'path': 'src.py'}])
def test_changed_citations_require_list(repo, tmp_path, citations):
    fas_path = repo / '.mentat/fas.json'
    fas = {'schema_version': 1, 'requirements': [], 'claims': [], 'surfaces': []}
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'prior')
    base = git(repo, 'rev-parse', 'HEAD')
    fas['requirements'].append({'id': str(uuid.uuid4()), 'citations': citations})
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'changed citation')
    head = git(repo, 'rev-parse', 'HEAD')
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), Agent(), Worker(), GitHub())
    with pytest.raises(ValueError, match='invalid review citations'):
        coordinator._source_context(head, {'payload': {
            'kind': 'review_spec', 'proposal': {'parent': base}}})


def test_external_citation_remains_uninspected_metadata(repo, tmp_path):
    fas_path = repo / '.mentat/fas.json'
    fas = {'schema_version': 1, 'requirements': [], 'claims': [], 'surfaces': []}
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'prior')
    base = git(repo, 'rev-parse', 'HEAD')
    url = 'https://example.org/doc'
    fas['requirements'].append({'id': str(uuid.uuid4()), 'citations': [url]})
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'external citation')
    head = git(repo, 'rev-parse', 'HEAD')
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), Agent(), Worker(), GitHub())
    text, _, _, _ = coordinator._source_context(head, {'payload': {
        'kind': 'review_spec', 'targetfiles': [], 'proposal': {'parent': base}}})
    context = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert url not in context['files']
    assert url in context['files']['.mentat/fas.json']


def test_changed_citation_of_deleted_file_fails_closed(repo, tmp_path):
    fas_path = repo / '.mentat/fas.json'
    fas = {'schema_version': 1, 'requirements': [], 'claims': [], 'surfaces': []}
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'prior')
    base = git(repo, 'rev-parse', 'HEAD')
    fas['requirements'].append({'id': str(uuid.uuid4()), 'citations': ['src.py']})
    fas_path.write_text(json.dumps(fas))
    (repo / 'src.py').unlink()
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'citation and deletion')
    head = git(repo, 'rev-parse', 'HEAD')
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), Agent(), Worker(), GitHub())
    with pytest.raises(ValueError, match='required review source unavailable.*split or revise'):
        coordinator._source_context(head, {'payload': {
            'kind': 'review_spec', 'proposal': {'parent': base}}})


def test_unchanged_historical_citation_does_not_block_unrelated_review(repo, tmp_path):
    fas_path = repo / '.mentat/fas.json'
    fas = {'schema_version': 1, 'requirements': [
        {'id': str(uuid.uuid4()), 'citations': 'historical unsupported format'}],
        'claims': [], 'surfaces': []}
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'historic fas')
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('unrelated change')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'unrelated change')
    head = git(repo, 'rev-parse', 'HEAD')
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), Agent(), Worker(), GitHub())
    context, _, _, _ = coordinator._source_context(head, {'payload': {
        'kind': 'review_spec', 'proposal': {'parent': base}}})
    assert 'proposal_diff' in context


def _review_capacity_repo(repo):
    requirements = [
        {'id': str(uuid.uuid4()), 'area': area, 'origin': 'uncertain',
         'statement': 'Existing intent', 'spec_path': 'openspec/specs/application/spec.md'}
        for area in AREAS
    ]
    spec = repo / 'openspec/specs/application/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('\n'.join(row['id'] for row in requirements))
    paths = [f'src/file{i}.py' for i in range(8)]
    (repo / 'src').mkdir()
    for path in paths:
        (repo / path).write_text('# ' + 'x' * 34_000 + '\n')
    surfaces = [{'path': path, 'status': 'gap', 'requirement_ids': []} for path in [
        'src.py', '.mentat/workspace.json', '.mentat/fas.json',
        'openspec/specs/application/spec.md', *paths]]
    (repo / '.mentat/fas.json').write_text(json.dumps({
        'schema_version': 1, 'requirements': requirements, 'claims': [], 'surfaces': surfaces}))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'prior reviewed specification')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    return requirements, paths


def test_review_capacity_splits_draft_and_reviews_each_accepted_mapping(repo, tmp_path):
    requirements, paths = _review_capacity_repo(repo)
    base = git(repo, 'rev-parse', 'HEAD')
    calls = []

    class CapacityAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
            calls.append((kind, payload, context, git(repo, 'rev-parse', 'HEAD')))
            if kind == 'generate_spec':
                path = repo / '.mentat/fas.json'
                fas = json.loads(path.read_text())
                if not payload.get('round') and not payload.get('targetfiles'):
                    assert all(p in context['files'] for p in paths)
                    for surface in fas['surfaces']:
                        if surface['path'] in paths:
                            surface.update(status='mapped', requirement_ids=[requirements[0]['id']],
                                           note='x' * 12_000)
                elif payload.get('round') == 1:
                    assert git(repo, 'rev-parse', 'HEAD') == base
                    assert set(paths) <= set(payload['revisit_files'])
                    surface = next(row for row in fas['surfaces'] if row['path'] == paths[0])
                    surface.update(status='mapped', requirement_ids=[requirements[0]['id']])
                else:
                    for surface in fas['surfaces']:
                        if surface['path'] in payload.get('targetfiles', []) and surface['path'] in paths:
                            surface.update(status='mapped', requirement_ids=[requirements[0]['id']])
                path.write_text(json.dumps(fas))
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'partial mapping', 'findings': [],
                    'verification_requests': []}}
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=CapacityAgent())
    generators = [x for x in calls if x[0] == 'generate_spec']
    retries = [x for x in generators if x[1].get('round') == 1]
    assert retries, [(j['payload']['kind'], j['status'], j.get('outcome')) for j in snapshot['jobs']]
    retry = retries[0]
    assert retry[3] == base
    assert set(paths) <= set(retry[1]['revisit_files'])
    feedback = retry[1]['feedback']
    rejected = feedback['rejected_head']
    assert feedback['review_base'] == base
    assert set(paths) <= set(feedback['required_paths'])
    rejected_fas = json.loads(git(repo, 'show', f'{rejected}:.mentat/fas.json'))
    assert all(next(s for s in rejected_fas['surfaces'] if s['path'] == p)['status'] == 'mapped' for p in paths)
    assert all(x[1]['proposal']['head'] != rejected for x in calls if x[0] == 'review_spec')
    assert any(set(paths[1:]) & set(x[1].get('targetfiles') or []) for x in generators)
    reviews = [x for x in calls if x[0] == 'review_spec']
    first_reviews = [x for x in reviews if x[1]['proposal'].get('round') == 1]
    assert len(first_reviews) == 2
    for _, payload, context, _ in first_reviews:
        assert context['files'][paths[0]] == (repo / paths[0]).read_text()
        assert '.mentat/fas.json' in context['files']
        assert '.mentat/fas.json' in context['proposal_diff']
        assert payload['proposal']['parent'] == base
    reviewed = json.loads(git(repo, 'show', f"{first_reviews[0][1]['proposal']['head']}:.mentat/fas.json"))
    assert reviewed['requirements'] == requirements
    assert github.published, snapshot
    assert any(x[0] == 'generate_verification' for x in calls)
    assert any('capacity' in f.get('summary', '').lower() for f in snapshot['findings'])


def test_review_capacity_exhaustion_is_bounded_across_resume(repo, tmp_path):
    requirements, paths = _review_capacity_repo(repo)
    base = git(repo, 'rev-parse', 'HEAD')
    calls = []

    class OversizedAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            calls.append((payload['kind'], payload.get('round', 0)))
            if payload['kind'] == 'generate_spec':
                assert git(repo, 'rev-parse', 'HEAD') == base
                path = repo / '.mentat/fas.json'
                fas = json.loads(path.read_text())
                for surface in fas['surfaces']:
                    if surface['path'] in paths:
                        surface.update(status='mapped', requirement_ids=[requirements[0]['id']],
                                       note='x' * 12_000)
                path.write_text(json.dumps(fas))
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'oversized', 'findings': [],
                    'verification_requests': []}}
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    agent = OversizedAgent()
    snapshot, worker, github = run(repo, tmp_path, agent=agent)
    assert [n for kind, n in calls if kind == 'generate_spec'] == [0, 1, 2]
    assert not [kind for kind, _ in calls if kind == 'review_spec']
    assert not github.published
    assert any(f['status'] == 'inconclusive' and 'capacity' in f['summary'].lower()
               for f in snapshot['findings'])
    before = list(calls)
    store = Store(tmp_path / 'state/state.db')
    coordinator = Coordinator(repo, store, agent, worker, github)
    coordinator.run(resume=snapshot['session']['id'])
    assert calls == before


@pytest.mark.parametrize('trusted', ['valid', 'missing', 'invalid'])
def test_capacity_retry_keeps_newer_accepted_specification(repo, tmp_path, trusted):
    requirements, paths = _review_capacity_repo(repo)
    base = git(repo, 'rev-parse', 'HEAD')
    path = repo / '.mentat/fas.json'
    fas = json.loads(path.read_text())
    accepted_record = {**requirements[0], 'id': str(uuid.uuid4()), 'statement': 'Accepted later'}
    fas['requirements'].append(accepted_record)
    path.write_text(json.dumps(fas))
    git(repo, 'commit', '-qam', 'independently reviewed specification')
    accepted = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('def value(): return 42  # approved fix\n')
    git(repo, 'commit', '-qam', 'approved application fix')
    fixed = git(repo, 'rev-parse', 'HEAD')
    for surface in fas['surfaces']:
        if surface['path'] in paths:
            surface.update(status='mapped', requirement_ids=[accepted_record['id']], note='x' * 12_000)
    path.write_text(json.dumps(fas))
    git(repo, 'commit', '-qam', 'oversized unreviewed proposal')
    rejected = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'state/state.db')
    config = default_config(repo)
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), base, base, config, 1, 100)
    coordinator._event('fas_reviewed', {'head': accepted})
    job = {'id': 'generation', 'item_id': 'cas.inventory', 'loop': 'scl',
           'payload': {'kind': 'generate_spec', 'accepted_spec_base': fixed}}
    proposal = {'head': rejected, 'parent': base, 'author_id': 'generator', 'round': 0,
                'accepted_spec_base': fixed}
    if trusted == 'missing':
        del job['payload']['accepted_spec_base']
        del proposal['accepted_spec_base']
    elif trusted == 'invalid':
        job['payload']['accepted_spec_base'] = proposal['accepted_spec_base'] = '0' * 40
    coordinator._request_reviews(job, proposal, 'review_spec')
    snapshot = store.snapshot(coordinator.session_id)
    retries = [j for j in snapshot['jobs'] if j['payload']['kind'] == 'generate_spec']
    if trusted != 'valid':
        assert not retries
        assert not [j for j in snapshot['jobs'] if j['payload']['kind'] == 'review_spec']
        assert any(f['status'] == 'inconclusive' for f in snapshot['findings'])
        return
    assert len(retries) == 1
    retry = retries[0]['payload']
    assert retry['target_head'] == retry['review_base'] == fixed
    assert retry['feedback']['rejected_head'] == rejected
    restored = json.loads(git(repo, 'show', f"{retry['target_head']}:.mentat/fas.json"))
    assert accepted_record in restored['requirements']
    assert git(repo, 'show', f"{retry['target_head']}:src.py") == 'def value(): return 42  # approved fix'
    assert all(s['status'] == 'gap' for s in restored['surfaces'] if s['path'] in paths)
    assert not [j for j in snapshot['jobs'] if j['payload']['kind'] == 'review_spec']


def test_noop_spec_proposal_uses_observed_accepted_head_as_review_base(repo, tmp_path):
    import time

    requirements, _ = _review_capacity_repo(repo)
    base = git(repo, 'rev-parse', 'HEAD')
    path = repo / '.mentat/fas.json'
    fas = json.loads(path.read_text())
    extra = {**requirements[0], 'id': str(uuid.uuid4()), 'statement': 'Accepted later'}
    fas['requirements'].append(extra)
    path.write_text(json.dumps(fas))
    spec = repo / 'openspec/specs/application/spec.md'
    spec.write_text(spec.read_text() + '\n' + extra['id'])
    git(repo, 'commit', '-qam', 'later accepted spec')
    accepted = git(repo, 'rev-parse', 'HEAD')

    class NoOpAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                'decision': 'accept', 'summary': 'no edits', 'findings': [],
                'verification_requests': []}}

    store = Store(tmp_path / 'state/state.db')
    config = default_config(repo)
    coordinator = Coordinator(repo, store, NoOpAgent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), base, base, config, 1, 100)
    job = {'id': 'noop', 'attempt': 1, 'item_id': 'cas.inventory', 'loop': 'scl',
           'payload': {'kind': 'generate_spec', 'target_head': accepted}}
    outcome = coordinator._execute(job, time.time() + 60)
    assert outcome['head'] == accepted
    assert outcome['parent'] == accepted
    assert outcome['observed_source_revision'] == accepted


def test_revision_generator_reserves_reviewed_mapped_source_before_optional_files(repo, tmp_path):
    from mentat.autonomy_state import record_digest

    requirements, paths = _review_capacity_repo(repo)
    target = paths[-1]
    base = git(repo, 'rev-parse', 'HEAD')
    calls = []

    class CorrectionAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
            calls.append((kind, payload, context, git(repo, 'rev-parse', 'HEAD')))
            if kind == 'generate_spec':
                path = repo / '.mentat/fas.json'
                fas = json.loads(path.read_text())
                if payload.get('round') == 1:
                    assert context['files'][target] == (repo / target).read_text()
                    assert context['files']['.mentat/fas.json'] == path.read_text()
                    assert 'proposal_diff' not in context
                    old = next(row for row in fas['surfaces'] if row['path'] == target)
                    revised = {**old, 'note': 'Corrected mutant interpretation'}
                    patch = {'schema_version': 1, 'expected_sha256': context['fas_sha256'],
                             'edits': [{'collection': 'surfaces', 'key': target,
                                        'expected_digest': record_digest(old), 'record': revised}]}
                    return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                        'decision': 'accept', 'summary': 'corrected', 'findings': [],
                        'verification_requests': [], 'changes': [{
                            'path': '.mentat/fas.json', 'operation': 'patch_fas',
                            'content': json.dumps(patch)}]}}
                if not payload.get('targetfiles'):
                    assert context['files'][target] == (repo / target).read_text()
                    row = next(row for row in fas['surfaces'] if row['path'] == target)
                    row.update(status='mapped', requirement_ids=[requirements[0]['id']],
                               note='x' * 110_000)
                    path.write_text(json.dumps(fas))
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'mapping', 'findings': [],
                    'verification_requests': []}}
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'review_spec' and payload['proposal']['parent'] == base:
                assert context['files'][target] == (repo / target).read_text()
                if payload['proposal'].get('round') == 0 and job['item_id'].endswith('.1'):
                    result['result']['decision'] = 'revise'
                    result['result']['findings'] = [{'summary': 'Correct mutant interpretation'}]
            return result

    snapshot, _, github = run(repo, tmp_path, agent=CorrectionAgent())
    generators = [entry for entry in calls if entry[0] == 'generate_spec']
    retry = next((entry for entry in generators if entry[1].get('round') == 1), None)
    assert retry, snapshot
    initial = next(entry for entry in generators if not entry[1].get('round'))
    reviews = [entry for entry in calls if entry[0] == 'review_spec'
               and entry[1]['proposal']['parent'] == base]
    first = [entry for entry in reviews if entry[1]['proposal'].get('round') == 0]
    revised = [entry for entry in reviews if entry[1]['proposal'].get('round') == 1]
    assert len(first) == len(revised) == 2, snapshot
    initial_head = first[0][1]['proposal']['head']
    assert initial[3] == base
    assert retry[3] == retry[1]['target_head'] == initial_head
    assert retry[1]['review_base'] == retry[1]['accepted_spec_base'] == base
    for _, payload, context, checkout in revised:
        proposal = payload['proposal']
        assert proposal['parent'] == base and proposal['observed_source_revision'] == initial_head
        assert checkout == proposal['head'] == context['revision']
        assert target in context['files'] and 'Corrected mutant interpretation' in context['proposal_diff']
        candidate = json.loads(git(repo, 'show', f"{checkout}:.mentat/fas.json"))
        assert candidate['requirements'] == requirements
        assert next(row for row in candidate['surfaces'] if row['path'] == target)['note'] == 'Corrected mutant interpretation'
    assert any(entry[0] == 'generate_verification' for entry in calls)
    assert github.published, snapshot


@pytest.mark.parametrize('round_number', [1, 2])
def test_correction_context_capacity_is_controller_only_and_bounded(repo, tmp_path, round_number):
    import time

    requirements, paths = _review_capacity_repo(repo)
    base = git(repo, 'rev-parse', 'HEAD')
    path = repo / '.mentat/fas.json'
    fas = json.loads(path.read_text())
    for row in fas['surfaces']:
        if row['path'] in paths:
            row.update(status='mapped', requirement_ids=[requirements[0]['id']], note='x' * 12_000)
    path.write_text(json.dumps(fas))
    git(repo, 'commit', '-qam', 'oversized unreviewed draft')
    rejected = git(repo, 'rev-parse', 'HEAD')

    class ForbiddenAgent(Agent):
        def run(self, *args, **kwargs):
            raise AssertionError('provider must not run without required source')

    store = Store(tmp_path / 'state/state.db')
    config = default_config(repo)
    coordinator = Coordinator(repo, store, ForbiddenAgent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), base, base, config, 1, 100)
    job = {'id': 'correction', 'attempt': 1, 'item_id': 'cas.inventory', 'loop': 'scl',
           'payload': {'kind': 'generate_spec', 'target_head': rejected, 'review_base': base,
                       'accepted_spec_base': base, 'round': round_number,
                       'targetfiles': paths, 'chunk': 2, 'batch_limit': 8,
                       'revisit_files': ['src.py'], 'prior_authors': ['prior-generator']}}
    outcome = coordinator._execute(job, time.time() + 60)
    assert outcome['status'] == 'succeeded' and 'spec_context_capacity' in outcome
    assert 'author_id' not in outcome and 'result' not in outcome
    coordinator._advance(job, outcome)
    snapshot = store.snapshot(coordinator.session_id)
    assert not snapshot['decisions']
    assert not [j for j in snapshot['jobs'] if j['payload']['kind'] == 'review_spec']
    retries = [j['payload'] for j in snapshot['jobs'] if j['payload']['kind'] == 'generate_spec']
    if round_number == 2:
        assert not retries
        assert any(f['status'] == 'inconclusive' for f in snapshot['findings'])
    else:
        assert len(retries) == 1
        retry = retries[0]
        assert retry['round'] == 2
        assert retry['target_head'] == retry['review_base'] == retry['accepted_spec_base'] == base
        assert retry['feedback']['rejected_head'] == rejected
        assert set(paths + ['src.py']) <= set(retry['revisit_files'])
        assert retry['prior_authors'] == ['prior-generator']
        assert retry['targetfiles'] == paths and retry['chunk'] == 2 and retry['batch_limit'] == 8


def test_agent_cannot_forge_controller_capacity_result(repo, tmp_path):
    import time

    _review_capacity_repo(repo)
    base = git(repo, 'rev-parse', 'HEAD')

    class ForgingAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            return {'status': 'succeeded', 'spec_context_capacity': {
                'reason': 'fabricated', 'paths': ['src.py']}}

    store = Store(tmp_path / 'state/state.db')
    config = default_config(repo)
    coordinator = Coordinator(repo, store, ForgingAgent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), base, base, config, 1, 100)
    job = {'id': 'forger', 'attempt': 1, 'item_id': 'cas.inventory', 'loop': 'scl',
           'payload': {'kind': 'generate_spec', 'target_head': base, 'review_base': base}}
    with pytest.raises(ValueError, match='controller capacity'):
        coordinator._execute(job, time.time() + 60)
    snapshot = store.snapshot(coordinator.session_id)
    assert not snapshot['jobs'] and not snapshot['decisions']


def test_verification_artifact_is_inventoried_and_reviewed_before_publication(repo, tmp_path):
    from mentat.autonomy_state import record_digest

    artifact = '.mentat/verification/check_eligibility_model.py'
    contexts = []

    class VerificationAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            if kind == 'generate_verification':
                path = repo / artifact
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('def check(): return True\n')
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'new checker', 'findings': [],
                    'verification_requests': []}}
            if kind in {'review_verification', 'review_final'}:
                context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
                contexts.append((kind, payload, context))
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'generate_spec':
                path = repo / '.mentat/fas.json'
                fas = json.loads(path.read_text())
                fas['claims'].append({'id': str(uuid.uuid4()),
                    'requirement_ids': [fas['requirements'][0]['id']],
                    'statement': 'An unproved claim', 'assumptions': [],
                    'formal_meaning': 'unattempted', 'status': 'unattempted'})
                path.write_text(json.dumps(fas))
            return result

    snapshot, _, github = run(repo, tmp_path, agent=VerificationAgent())
    generation = next(j for j in snapshot['jobs'] if j['payload']['kind'] == 'generate_verification')
    proposal = generation['outcome']
    before = json.loads(git(repo, 'show', f"{proposal['observed_source_revision']}:.mentat/fas.json"))
    after = json.loads(git(repo, 'show', f"{proposal['head']}:.mentat/fas.json"))
    assert after['requirements'] == before['requirements']
    assert after['claims'] == before['claims']
    assert len(after['claims']) == 1 and after['claims'][0]['status'] == 'unattempted'
    assert all(claim['status'] == 'unattempted' for claim in snapshot['claims'])
    assert [record_digest(c) for c in after['claims']] == [record_digest(c) for c in before['claims']]
    old_rows = {row['path']: row for row in before['surfaces']}
    new_rows = {row['path']: row for row in after['surfaces']}
    assert {key: new_rows[key] for key in old_rows} == old_rows
    assert set(new_rows) - set(old_rows) == {artifact}
    assert new_rows[artifact] == {'path': artifact, 'status': 'unexamined', 'requirement_ids': []}
    for kind in ('review_verification', 'review_final'):
        reviews = [(payload, context) for review, payload, context in contexts if review == kind]
        assert len(reviews) == 2
        for payload, context in reviews:
            assert payload['proposal']['head'] == proposal['head'] == context['revision']
            assert '.mentat/fas.json' in context['proposal_diff']
            assert artifact in context['proposal_diff']
            assert json.loads(context['files']['.mentat/fas.json']) == after
    candidate = Path(proposal['worktree'])
    tracked = git(candidate, 'ls-tree', '-r', '--name-only', 'HEAD').splitlines()
    assert validate_specification(candidate, tracked=tracked) == after
    assert github.published

    class DeferAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                'decision': 'defer', 'summary': 'no further work', 'findings': [],
                'verification_requests': []}}

    fresh, _, _ = run(candidate, tmp_path / 'fresh', agent=DeferAgent())
    assert fresh['session']['head'] == proposal['head']
    assert {r['id'] for r in fresh['requirements']} == {r['id'] for r in after['requirements']}


@pytest.mark.parametrize('fault', ['omitted', 'duplicate', 'malformed'])
def test_verification_inventory_does_not_repair_invalid_prior_fas(repo, tmp_path, fault):
    import time

    _review_capacity_repo(repo)
    path = repo / '.mentat/fas.json'
    fas = json.loads(path.read_text())
    if fault == 'omitted':
        fas['surfaces'] = [row for row in fas['surfaces'] if row['path'] != 'src.py']
        raw = json.dumps(fas)
    elif fault == 'duplicate':
        fas['surfaces'].append(dict(fas['surfaces'][0]))
        raw = json.dumps(fas)
    else:
        raw = '{malformed'
    path.write_text(raw)
    git(repo, 'commit', '-qam', 'invalid existing inventory')
    head = git(repo, 'rev-parse', 'HEAD')

    class ArtifactAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            artifact = repo / '.mentat/verification/check.py'
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text('check = True\n')
            return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                'decision': 'accept', 'summary': 'checker', 'findings': [],
                'verification_requests': []}}

    store = Store(tmp_path / 'state/state.db')
    config = default_config(repo)
    coordinator = Coordinator(repo, store, ArtifactAgent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), head, head, config, 1, 100)
    job = {'id': 'invalid-inventory', 'attempt': 1, 'item_id': 'verification.inventory',
           'loop': 'ivl', 'payload': {'kind': 'generate_verification', 'target_head': head}}
    with pytest.raises(ValueError):
        coordinator._execute(job, time.time() + 60)
    assert path.read_text() == raw
    assert not store.snapshot(coordinator.session_id)['decisions']


def test_verification_generator_cannot_write_fas(repo, tmp_path):
    import time

    _review_capacity_repo(repo)
    head = git(repo, 'rev-parse', 'HEAD')

    class UnauthorizedAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            (repo / '.mentat/fas.json').write_text('{}')
            return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                'decision': 'accept', 'summary': 'invalid edit', 'findings': [],
                'verification_requests': []}}

    store = Store(tmp_path / 'state/state.db')
    config = default_config(repo)
    coordinator = Coordinator(repo, store, UnauthorizedAgent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), head, head, config, 1, 100)
    job = {'id': 'unauthorized', 'attempt': 1, 'item_id': 'verification.inventory',
           'loop': 'ivl', 'payload': {'kind': 'generate_verification', 'target_head': head}}
    with pytest.raises(ValueError, match='scope|outside|permitted'):
        coordinator._execute(job, time.time() + 60)


def test_publication_rejects_incomplete_candidate_before_any_github_call(repo, tmp_path):
    import time

    from mentat.autonomy import current_tool_identity

    _review_capacity_repo(repo)
    path = repo / '.mentat/fas.json'
    fas = json.loads(path.read_text())
    fas['surfaces'] = [row for row in fas['surfaces'] if row['path'] != 'src.py']
    path.write_text(json.dumps(fas))
    git(repo, 'commit', '-qam', 'incomplete candidate')
    head = git(repo, 'rev-parse', 'HEAD')

    class NoGitHub(GitHub):
        def publish(self, *args, **kwargs):
            raise AssertionError('publication before inventory validation')
        def reconcile(self, *args, **kwargs):
            raise AssertionError('reconciliation before inventory validation')
        def merge(self, *args, **kwargs):
            raise AssertionError('merge before inventory validation')

    store = Store(tmp_path / 'state/state.db')
    config = default_config(repo)
    coordinator = Coordinator(repo, store, Agent(), Worker(), NoGitHub())
    coordinator.config = config
    coordinator.tool_identity = current_tool_identity()
    now = time.time()
    coordinator.session_id = store.new_session(str(repo), head, head, config, now, now + 60)
    key = coordinator._check_key(head, [], [], None)
    proposal = {'head': head, 'worktree': str(repo), 'diff_digest': 'diff'}
    reviews = [{'reviewer_id': reviewer, 'author_id': 'author', 'status': 'passed',
                'head': head, 'base': head, 'diff_digest': 'diff', 'review_cohort': 'cohort',
                'check_evidence_id': 'checked', 'check_key': key} for reviewer in ('one', 'two')]
    evidence = {'id': 'checked', 'check_key': key, 'head': head, 'base': head, 'checks': []}
    with pytest.raises(ValueError, match='surface inventory omits'):
        coordinator._publish({'payload': {'proposal': proposal, 'reviews': reviews}}, evidence)
    assert not any(e['kind'] in {'publication_started', 'merge_started'}
                   for e in store.snapshot(coordinator.session_id)['events'])


def _valid_publication_spec(repo):
    spec_name = 'openspec/specs/app/spec.md'
    spec = repo / spec_name
    spec.parent.mkdir(parents=True, exist_ok=True)
    requirements = [{'id': str(uuid.uuid4()), 'area': area, 'origin': 'uncertain',
                     'statement': 'Unspecified', 'spec_path': spec_name} for area in AREAS]
    spec.write_text('\n'.join(row['id'] for row in requirements))
    paths = ['src.py', '.mentat/workspace.json', '.mentat/fas.json', spec_name]
    (repo / '.mentat/fas.json').write_text(json.dumps({
        'schema_version': 1, 'requirements': requirements, 'claims': [],
        'surfaces': [{'path': path, 'status': 'gap', 'requirement_ids': []} for path in paths]}))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'reviewed publication fixture')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))


@pytest.mark.parametrize('explicit_base', [False, True])
def test_noop_verification_uses_input_base_unless_retry_base_is_explicit(repo, tmp_path, explicit_base):
    import time

    _review_capacity_repo(repo)
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('def value(): return 42  # accepted change\n')
    git(repo, 'commit', '-qam', 'accepted source change')
    accepted = git(repo, 'rev-parse', 'HEAD')

    class NoOpAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                'decision': 'accept', 'summary': 'no edits', 'findings': [],
                'verification_requests': []}}

    store = Store(tmp_path / 'state/state.db')
    config = default_config(repo)
    coordinator = Coordinator(repo, store, NoOpAgent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), base, base, config, 1, 100)
    payload = {'kind': 'generate_verification', 'target_head': accepted}
    if explicit_base:
        payload['review_base'] = base
    job = {'id': 'noop-verification', 'attempt': 1, 'item_id': 'verification.inventory',
           'loop': 'ivl', 'payload': payload}
    outcome = coordinator._execute(job, time.time() + 60)
    assert outcome['head'] == accepted
    assert outcome['observed_source_revision'] == accepted
    assert outcome['parent'] == (base if explicit_base else accepted)


def test_both_review_stages_receive_every_check_after_twelfth(repo, tmp_path):
    reviews = []
    commands = [['extra-check', str(index)] for index in range(15)]

    class ManyChecksAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            if payload['kind'] in {'review_verification', 'review_final'}:
                reviews.append((payload['kind'], payload['check_summary'], payload['prompt']))
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if payload['kind'] == 'generate_verification':
                result['result']['verification_requests'] = [
                    {'argv': command, 'claim_ids': [], 'method': 'test'} for command in commands
                ]
            return result

    snapshot, _, github = run(repo, tmp_path, agent=ManyChecksAgent())
    assert github.published, snapshot
    for kind in ('review_verification', 'review_final'):
        pair = [(summary, prompt) for stage, summary, prompt in reviews if stage == kind]
        assert len(pair) == 2
        for summary, prompt in pair:
            assert summary['checks_omitted'] == 0
            assert {tuple(item['command']) for item in summary['checks']} >= {
                tuple(command) for command in commands}
            assert 'extra-check' in prompt and '14' in prompt


@pytest.mark.parametrize("revert_prior_mapping", [False, True])
def test_chained_spec_reviews_reach_noop_verification_and_full_final_review(repo, tmp_path, revert_prior_mapping):
    requirements, paths = _review_capacity_repo(repo)
    if revert_prior_mapping:
        unicode_cas = repo / 'openspec/specs/é/spec.md'
        unicode_cas.parent.mkdir(parents=True)
        unicode_cas.write_text('Existing unicode CAS\n')
        fas_path = repo / '.mentat/fas.json'
        fas = json.loads(fas_path.read_text())
        fas['surfaces'].append({'path': 'openspec/specs/é/spec.md', 'status': 'gap',
                                'requirement_ids': []})
        fas_path.write_text(json.dumps(fas, ensure_ascii=False))
        git(repo, 'add', '.')
    for path in paths:
        (repo / path).write_text('# ' + 'x' * 55_000 + '\n')
    git(repo, 'commit', '-qam', 'large optional source inventory')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    calls = []

    class ChainedAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
            calls.append((kind, payload, context))
            if kind == 'generate_spec':
                path = repo / '.mentat/fas.json'
                fas = json.loads(path.read_text())
                for surface in fas['surfaces']:
                    if revert_prior_mapping and payload.get('targetfiles') and surface['status'] == 'mapped':
                        surface.update(status='gap', requirement_ids=[])
                    elif surface['path'] in paths and surface['path'] in context['files']:
                        surface.update(status='mapped', requirement_ids=[requirements[0]['id']])
                path.write_text(json.dumps(fas))
            if kind in {'generate_spec', 'generate_verification'}:
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'reviewable change', 'findings': [],
                    'verification_requests': []}}
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=ChainedAgent())
    assert github.published, snapshot
    events = [e for e in snapshot['events'] if e['kind'] == 'fas_reviewed']
    assert len(events) >= 2
    decisions = {d['id']: d for d in snapshot['decisions']}
    for event in events:
        ids = event['payload']['decision_ids']
        assert len(ids) >= 2
        assert len({decisions[identity]['reviewer_id'] for identity in ids}) >= 2
        assert all(decisions[identity]['decision'] == 'accept' for identity in ids)
    generation = next(j for j in snapshot['jobs'] if j['payload']['kind'] == 'generate_verification')
    candidate = generation['outcome']
    assert candidate['head'] == candidate['observed_source_revision'] == candidate['parent']
    for kind in ('review_verification', 'review_final'):
        pair = [(payload, context) for stage, payload, context in calls if stage == kind]
        assert len(pair) == 2
        for payload, context in pair:
            composition = context['reviewed_specification']
            assert 'unreviewed' in composition['baseline'].lower()
            assert composition['checkpoint'] == candidate['head']
            assert len(composition['cohorts']) >= 2
            assert {identity for cohort in composition['cohorts'] for identity in cohort['decision_ids']} >= {
                identity for event in events for identity in event['payload']['decision_ids']}
            assert set(paths) <= set(composition['source_blobs_sha256'])
            assert context['revision'] == candidate['head']
            assert payload['check_summary']['head'] == candidate['head']
            if kind == 'review_final':
                assert context['proposal_base'] == snapshot['session']['base']
                assert '"mapped"' in context['proposal_diff']

    # A later SCL cohort can complete between final context construction and
    # publication. Recreate that ordering using these real persisted cohorts.
    from copy import deepcopy

    from mentat.autonomy import current_tool_identity

    store = Store(tmp_path / 'state/state.db')
    first_head = events[0]['payload']['head']
    earlier = deepcopy(snapshot)
    earlier['events'] = [event for event in earlier['events']
                         if event['kind'] != 'fas_reviewed' or event['seq'] == events[0]['seq']]

    class EarlierSnapshot:
        def snapshot(self, session_id):
            return earlier

    github_spy = GitHub()
    coordinator = Coordinator(repo, store, Agent(), Worker(), github_spy)
    coordinator.session_id = snapshot['session']['id']
    coordinator.config = default_config(repo)
    coordinator.tool_identity = current_tool_identity()
    coordinator.authority_digest = next(e['payload']['digest'] for e in snapshot['events']
                                        if e['kind'] == 'authority_pinned')
    coordinator.store = EarlierSnapshot()
    proposal = {'head': first_head, 'parent': snapshot['session']['base'],
                'worktree': str(repo), 'diff_digest': 'final-diff'}
    coordinator._source_context(first_head, {'payload': {'kind': 'review_final',
        'proposal': proposal, 'check_summary': {}}})
    coordinator.store = store
    git(repo, 'checkout', '-q', '--detach', first_head)
    key = coordinator._check_key(first_head, [], [], None)
    evidence = {'id': 'check', 'head': first_head, 'base': snapshot['session']['base'],
                'check_key': key, 'checks': []}
    reviews = [{'reviewer_id': reviewer, 'author_id': 'author', 'status': 'passed',
                'head': first_head, 'base': snapshot['session']['base'],
                'diff_digest': 'final-diff', 'review_cohort': 'final-cohort',
                'check_evidence_id': 'check', 'check_key': key} for reviewer in ('one', 'two')]
    with pytest.raises(ValueError, match='specification|correspondence|ancestor|reconciliation'):
        coordinator._publish({'payload': {'proposal': proposal, 'reviews': reviews}}, evidence)
    assert not github_spy.published and not github_spy.merged

    if revert_prior_mapping:
        # Replay a real accepted ancestor after final context construction.
        # The candidate has since reverted that ancestor's mapped records.
        late_ancestor = deepcopy(snapshot)
        first = next(e for e in late_ancestor['events'] if e['seq'] == events[0]['seq'])
        class LateSnapshot:
            path = store.path
            def snapshot(self, session_id):
                return late_ancestor
        coordinator.store = LateSnapshot()
        git(repo, 'checkout', '-q', '--detach', candidate['head'])
        candidate_proposal = {'head': candidate['head'], 'parent': snapshot['session']['base'],
                              'worktree': str(repo), 'diff_digest': 'later-final'}
        coordinator._source_context(candidate['head'], {'payload': {'kind': 'review_final',
            'proposal': candidate_proposal, 'check_summary': {}}})
        late_ancestor['events'].append(first)
        key = coordinator._check_key(candidate['head'], [], [], None)
        later_evidence = {'id': 'later-check', 'head': candidate['head'],
                          'base': snapshot['session']['base'], 'check_key': key, 'checks': []}
        later_reviews = [{'reviewer_id': reviewer, 'author_id': 'author', 'status': 'passed',
            'head': candidate['head'], 'base': snapshot['session']['base'],
            'diff_digest': 'later-final', 'review_cohort': 'later-cohort',
            'check_evidence_id': 'later-check', 'check_key': key} for reviewer in ('one', 'two')]
        with pytest.raises(ValueError):
            coordinator._publish({'payload': {'proposal': candidate_proposal,
                                  'reviews': later_reviews}}, later_evidence)
        assert not github_spy.published and not github_spy.merged
        coordinator.store = store

    # Reuse the actual persisted review events and decisions, then change one
    # reviewed input at a time. A copied receipt must not survive content drift.
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.session_id = snapshot['session']['id']
    coordinator.tool_identity = current_tool_identity()
    coordinator.authority_digest = next(e['payload']['digest'] for e in snapshot['events']
                                        if e['kind'] == 'authority_pinned')
    coordinator._review_composition(candidate['head'])
    git(repo, 'checkout', '-q', '--detach', candidate['head'])
    for drift in ('source', 'cas', 'mapping', 'requirement', 'deleted_surface', 'forged_inventory'):
        if drift == 'source':
            (repo / paths[0]).write_text('changed after review\n')
        elif drift == 'cas':
            spec = repo / ('openspec/specs/é/spec.md' if revert_prior_mapping
                           else 'openspec/specs/application/spec.md')
            spec.write_text(spec.read_text() + '\nchanged after review\n')
        else:
            fas_path = repo / '.mentat/fas.json'
            fas = json.loads(fas_path.read_text())
            if drift == 'mapping':
                next(row for row in fas['surfaces'] if row['path'] == paths[0])['status'] = 'unsupported'
            elif drift == 'requirement':
                fas['requirements'][0]['statement'] = 'changed after review'
            elif drift == 'deleted_surface':
                fas['surfaces'] = [row for row in fas['surfaces'] if row['path'] != paths[0]]
            else:
                artifact = repo / 'verification/forged.py'
                artifact.parent.mkdir(exist_ok=True)
                artifact.write_text('forged = True\n')
                fas['surfaces'].append({'path': 'verification/forged.py', 'status': 'unexamined',
                                        'requirement_ids': []})
            fas_path.write_text(json.dumps(fas))
        git(repo, 'add', '-A')
        git(repo, 'commit', '-qm', 'post-review drift')
        changed = git(repo, 'rev-parse', 'HEAD')
        with pytest.raises(ValueError):
            coordinator._review_composition(changed)
        coordinator.config = default_config(repo)
        with pytest.raises(ValueError):
            coordinator._source_context(changed, {'payload': {'kind': 'review_final',
                'proposal': {'head': changed, 'parent': snapshot['session']['base']},
                'check_summary': {}}})
        git(repo, 'reset', '--hard', '-q', candidate['head'])

    # Tamper with persisted identities while retaining the real Git history.
    from copy import deepcopy

    original = store.snapshot(coordinator.session_id)
    receipt = next(e for e in original['events'] if e['kind'] == 'fas_reviewed')
    first_id = receipt['payload']['decision_ids'][0]

    class SnapshotStore:
        def __init__(self, data):
            self.data = data

        def snapshot(self, session_id):
            return self.data

    for field in ('cohort', 'head', 'diff', 'reviewer', 'missing'):
        altered = deepcopy(original)
        decision = next(d for d in altered['decisions'] if d['id'] == first_id)
        if field == 'cohort':
            decision['review_cohort'] = 'forged'
        elif field == 'head':
            decision['head'] = altered['session']['base']
        elif field == 'diff':
            decision['diff_digest'] = 'forged'
        elif field == 'reviewer':
            decision['reviewer_id'] = decision['author_id']
        else:
            altered['decisions'] = [d for d in altered['decisions'] if d['id'] != first_id]
        coordinator.store = SnapshotStore(altered)
        with pytest.raises(ValueError):
            coordinator._review_composition(candidate['head'])
    coordinator.store = store
    store.append_event(coordinator.session_id, 'fas_reviewed', {
        'head': candidate['head'], 'decision_ids': ['forged-decision']})
    with pytest.raises(ValueError):
        coordinator._review_composition(candidate['head'])


@pytest.mark.parametrize('exact_path_exists', [False, True])
def test_review_receipt_binds_same_cited_file_reviewers_saw(repo, tmp_path, exact_path_exists):
    citation = 'evidence.py#L1'
    (repo / 'evidence.py').write_text('plain source\n')
    if exact_path_exists:
        (repo / citation).write_text('literal hash filename\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'citation sources')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    target = citation if exact_path_exists else 'evidence.py'
    captured = []

    class CitationAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            kind = job['payload']['kind']
            if kind in {'review_spec', 'review_final'}:
                context = json.loads(job['payload']['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
                captured.append((kind, context))
            if kind == 'generate_verification':
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'no edits', 'findings': [],
                    'verification_requests': []}}
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'generate_spec':
                fas_path = repo / '.mentat/fas.json'
                fas = json.loads(fas_path.read_text())
                fas['requirements'][0]['citations'] = [citation]
                fas_path.write_text(json.dumps(fas))
            return result

    snapshot, _, github = run(repo, tmp_path, agent=CitationAgent())
    assert github.published, snapshot
    reviews = [context for kind, context in captured if kind == 'review_spec']
    finals = [context for kind, context in captured if kind == 'review_final']
    assert len(reviews) == len(finals) == 2
    for context in reviews:
        assert context['files'][target] == (repo / target).read_text()
    for context in finals:
        assert target in context['reviewed_specification']['source_blobs_sha256']
    head = github.published[0]
    store = Store(tmp_path / 'state/state.db')
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.session_id = snapshot['session']['id']
    from mentat.autonomy import current_tool_identity
    coordinator.tool_identity = current_tool_identity()
    coordinator.authority_digest = next(e['payload']['digest'] for e in snapshot['events']
                                        if e['kind'] == 'authority_pinned')
    coordinator._review_composition(head)
    git(repo, 'checkout', '-q', '--detach', head)
    (repo / target).write_text('changed cited evidence\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'change reviewed citation')
    with pytest.raises(ValueError):
        coordinator._review_composition(git(repo, 'rev-parse', 'HEAD'))


def test_standalone_spec_context_has_no_review_authority_and_dispatch_requires_session(repo, tmp_path):
    import time

    head = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'state/state.db')
    github = GitHub()
    coordinator = Coordinator(repo, store, Agent(), Worker(), github)
    coordinator.config = default_config(repo)
    proposal = {'parent': head, 'head': head, 'diff_digest': 'unused', 'author_id': 'author'}
    job = {'id': 'unbound', 'attempt': 1, 'item_id': 'unbound', 'loop': 'scl',
           'payload': {'kind': 'review_spec', 'proposal': proposal}}
    text, _, _, _ = coordinator._source_context(head, job)
    context = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert 'reviewed_specification' not in context
    assert 'trusted_source_verifier' not in context
    assert not context.get('correspondence_refresh')
    with pytest.raises((AssertionError, ValueError)):
        coordinator._execute(job, time.time() + 60)
    with pytest.raises((AssertionError, ValueError)):
        coordinator._request_reviews(job, proposal, 'review_spec')
    with pytest.raises((AssertionError, ValueError)):
        coordinator._publish({'payload': {'proposal': proposal, 'reviews': []}}, {})
    assert not github.published and not github.merged


def test_review_diff_never_executes_configured_git_textconv(repo, tmp_path):
    import hashlib
    import shlex

    from mentat.autonomy import _diff

    marker = tmp_path / 'converter-ran'
    converter = tmp_path / 'textconv.sh'
    converter.write_text(f'printf ran > {shlex.quote(str(marker))}\ncat "$1"\n')
    git(repo, 'config', 'diff.marker.textconv', f'sh {shlex.quote(str(converter))}')
    (repo / '.gitattributes').write_text('*.dat diff=marker\n')
    source = repo / 'source.dat'
    source.write_text('original source\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'configure text conversion')
    base = git(repo, 'rev-parse', 'HEAD')
    source.write_text('changed source\n')
    git(repo, 'commit', '-qam', 'change source')
    head = git(repo, 'rev-parse', 'HEAD')

    # Establish that the repository-local fixture executes under Git's old flags.
    subprocess.check_output(['git', '-C', str(repo), 'diff', '--binary', '--no-ext-diff', base, head])
    assert marker.read_text() == 'ran'
    marker.unlink()

    expected_diff = subprocess.check_output(['git', '-C', str(repo), 'diff', '--binary',
                                             '--no-ext-diff', '--no-textconv', base, head])
    raw_diff = _diff(repo, base, head)
    assert raw_diff == expected_diff
    assert b'-original source' in raw_diff and b'+changed source' in raw_diff
    assert not marker.exists()
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), Agent(), Worker(), GitHub())
    text, _, _, _ = coordinator._source_context(head, {'payload': {'kind': 'review_spec',
        'proposal': {'head': head, 'parent': base}}})
    context = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert context['proposal_diff'].encode() == raw_diff
    assert hashlib.sha256(context['proposal_diff'].encode()).digest() == hashlib.sha256(expected_diff).digest()
    assert not marker.exists()


def test_concurrent_review_jobs_serialize_git_registration_but_not_agents(repo, tmp_path, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from mentat import autonomy

    git_call = autonomy.git
    registration_guard = threading.Lock()
    registration_overlap = threading.Event()
    in_registration = 0
    max_registration = 0
    started = threading.Barrier(3)
    agents_started = threading.Barrier(3, timeout=10)
    agent_guard = threading.Lock()
    active_agents = 0
    max_agents = 0

    def observed_git(path, *args):
        nonlocal in_registration, max_registration
        if args[:2] != ('worktree', 'add'):
            return git_call(path, *args)
        with registration_guard:
            in_registration += 1
            max_registration = max(max_registration, in_registration)
            if in_registration > 1:
                registration_overlap.set()
        try:
            # Give other already-started jobs an opportunity to enter Git. The
            # event makes overlap deterministic on the previous implementation.
            registration_overlap.wait(1)
            return git_call(path, *args)
        finally:
            with registration_guard:
                in_registration -= 1

    class ConcurrentAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            nonlocal active_agents, max_agents
            with agent_guard:
                active_agents += 1
                max_agents = max(max_agents, active_agents)
            try:
                agents_started.wait()
                return super().run(repo, job, output, timeout_seconds=timeout_seconds)
            finally:
                with agent_guard:
                    active_agents -= 1

    monkeypatch.setattr(autonomy, 'git', observed_git)
    head = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'state/state.db')
    config = default_config(repo)
    coordinator = Coordinator(repo, store, ConcurrentAgent(), Worker(), GitHub())
    coordinator.config = config
    now = time.time()
    coordinator.session_id = store.new_session(str(repo), head, head, config, now, now + 60)
    for index in range(3):
        store.enqueue_job(coordinator.session_id, 'scl', 'code-reviewer', f'review-{index}',
                          f'input-{index}', {'kind': 'review_spec', 'target_head': head,
                          'proposal': {'head': head, 'parent': head, 'diff_digest': 'diff'}})
    jobs = [store.claim_job(coordinator.session_id, f'worker-{index}', now=now, lease_seconds=50)
            for index in range(3)]
    assert all(job is not None for job in jobs)

    def execute(job):
        started.wait(timeout=10)
        return coordinator._execute(job, now + 50)

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(execute, jobs))
    assert all(outcome['status'] == 'succeeded' for outcome in outcomes)
    assert max_registration == 1
    assert not registration_overlap.is_set()
    assert max_agents == 3
    for job, outcome in zip(jobs, outcomes, strict=True):
        store.finish_job(job['id'], job['lease_token'], outcome, now=time.time())
        assert git(outcome['worktree'], 'rev-parse', 'HEAD') == head
    assert len([job for job in store.snapshot(coordinator.session_id)['jobs']
                if job['status'] == 'succeeded']) == 3


def test_worktree_registration_runtime_error_is_bounded_and_preserves_evidence(repo, tmp_path, monkeypatch):
    from mentat import autonomy

    git_call = autonomy.git
    registered = []

    def failing_registration(path, *args):
        result = git_call(path, *args)
        if args[:2] == ('worktree', 'add'):
            registered.append((args[3], args[4]))
            raise RuntimeError('worktree registration failed after creation')
        return result

    monkeypatch.setattr(autonomy, 'git', failing_registration)
    snapshot, _, github = run(repo, tmp_path)
    job = next(job for job in snapshot['jobs'] if job['payload']['kind'] == 'generate_spec')
    events = [event for event in snapshot['events'] if event['kind'] == 'job_claimed'
              and event['payload']['job_id'] == job['id']]
    assert [event['payload']['attempt'] for event in events] == [1, 2, 3]
    assert job['attempt'] == 3 and job['outcome'].get('exhausted')
    assert snapshot['session']['status'] == 'blocked'
    assert any('worktree registration failed after creation' in finding.get('summary', '')
               for finding in snapshot['findings'])
    assert not github.published and not github.merged
    assert len(registered) == 3
    for branch, path in registered:
        assert Path(path).is_dir()
        assert git(repo, 'rev-parse', '--verify', branch)


@pytest.mark.parametrize('drift', [
    'unchanged', 'application', 'caller', 'unicode_caller', 'mode', 'cas', 'requirement',
    'claim', 'contract', 'forged', 'incomparable',
])
def test_repair_checkpoint_validates_reviewed_history_and_witness_context(repo, tmp_path, drift):
    from copy import deepcopy

    from mentat.autonomy import current_tool_identity

    requirements, paths = _review_capacity_repo(repo)
    caller = repo / 'caller.py'
    caller.write_text('from src import value\n')
    unicode_caller = repo / 'é caller.py'
    unicode_caller.write_text('from src import value\n')
    fas_path = repo / '.mentat/fas.json'
    fas = json.loads(fas_path.read_text())
    fas['surfaces'].extend([{'path': 'caller.py', 'status': 'gap', 'requirement_ids': []},
                            {'path': 'é caller.py', 'status': 'gap', 'requirement_ids': []}])
    claim = {'id': str(uuid.uuid4()), 'requirement_ids': [requirements[0]['id']],
             'statement': 'Value is one', 'assumptions': ['integer result'],
             'status': 'unattempted', 'formal_contract': {'path': 'src.py',
             'function': 'value', 'inputs': {}, 'requires': 'True', 'ensures': 'result == 1'}}
    fas['claims'].append(claim)
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'original witness context')
    original = git(repo, 'rev-parse', 'HEAD')
    if drift == 'application':
        (repo / 'src.py').write_text('def value(): return 2\n')
    elif drift == 'caller':
        caller.write_text('from src import value\nprint(value())\n')
    elif drift == 'unicode_caller':
        unicode_caller.write_text('from src import value\nprint(value())\n')
    elif drift == 'mode':
        caller.chmod(0o755)
    elif drift == 'cas':
        spec = repo / 'openspec/specs/application/spec.md'
        spec.write_text(spec.read_text() + '\nAdditional semantic qualification\n')
    elif drift == 'requirement':
        fas['requirements'][0]['statement'] = 'Different adjudicated behavior'
        fas_path.write_text(json.dumps(fas))
    elif drift == 'claim':
        claim['assumptions'] = ['different domain']
        fas_path.write_text(json.dumps(fas))
    elif drift == 'contract':
        claim['formal_contract']['ensures'] = 'result == 2'
        fas_path.write_text(json.dumps(fas))
    if drift not in {'unchanged', 'forged', 'incomparable'}:
        git(repo, 'add', '.')
        git(repo, 'commit', '-qm', 'context changed before reviewed checkpoint')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', base)

    class MappingAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            kind = job['payload']['kind']
            if kind == 'generate_spec':
                path = repo / '.mentat/fas.json'
                data = json.loads(path.read_text())
                next(row for row in data['surfaces'] if row['path'] == paths[0]).update(
                    status='mapped', requirement_ids=[requirements[0]['id']])
                path.write_text(json.dumps(data))
            if kind in {'generate_spec', 'generate_verification'}:
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept' if kind == 'generate_spec' else 'defer',
                    'summary': 'bounded test', 'findings': [], 'verification_requests': []}}
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=MappingAgent())
    assert not github.published
    event = next(e for e in snapshot['events'] if e['kind'] == 'fas_reviewed')
    checkpoint = event['payload']['head']
    assert checkpoint != base
    assert len(event['payload']['decision_ids']) == 2
    store = Store(tmp_path / 'state/state.db')
    coordinator = Coordinator(repo, store, Agent(), Worker(), github)
    coordinator.session_id = snapshot['session']['id']
    coordinator.tool_identity = current_tool_identity()
    coordinator.authority_digest = next(e['payload']['digest'] for e in snapshot['events']
                                        if e['kind'] == 'authority_pinned')
    job = {'id': 'blocked-repair', 'attempt': 1, 'loop': 'iil', 'item_id': 'blocked-repair',
           'payload': {'kind': 'investigate_defect', 'target_head': original,
           'requirement': requirements[0], 'evidence': {'source_identity': {'revision': original}}}}
    if drift in {'forged', 'incomparable'}:
        altered = deepcopy(snapshot)
        accepted = next(e for e in altered['events'] if e['kind'] == 'fas_reviewed')
        if drift == 'forged':
            accepted['payload']['decision_ids'][0] = 'forged'
        else:
            git(repo, 'checkout', '-q', '--detach', original)
            caller.write_text('independent branch\n')
            git(repo, 'commit', '-qam', 'incomparable history')
            accepted['payload']['head'] = git(repo, 'rev-parse', 'HEAD')
            git(repo, 'checkout', '-q', '--detach', base)
        class AlteredSnapshot:
            def snapshot(self, session_id):
                return altered
        coordinator.store = AlteredSnapshot()
    if drift == 'unchanged':
        assert coordinator._repair_checkpoint(job, original) == checkpoint
    else:
        import time

        with pytest.raises(ValueError):
            coordinator._repair_checkpoint(job, original)
        outcome = coordinator._execute(job, time.time() + 60)
        assert outcome['status'] == 'succeeded' and outcome.get('repair_checkpoint_gap')
        assert not github.published and not github.merged


@pytest.mark.parametrize('correction', [False, True])
def test_initial_feature_specification_is_reviewed_from_remote_base(repo, tmp_path, correction):
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('def value(): return 1  # feature source\n')
    requirements = [{'id': str(uuid.uuid4()), 'area': area, 'origin': 'observed',
                     'statement': 'Observed feature behavior', 'citations': ['src.py'],
                     'spec_path': 'openspec/specs/application/spec.md'} for area in AREAS]
    spec = repo / 'openspec/specs/application/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('\n'.join(row['id'] for row in requirements) + '\nManual feature specification\n')
    claim = {'id': str(uuid.uuid4()), 'requirement_ids': [requirements[0]['id']],
             'statement': 'value returns one', 'assumptions': [], 'status': 'unattempted',
             'formal_contract': {'path': 'src.py', 'function': 'value', 'inputs': {},
                                 'requires': 'True', 'ensures': 'result == 1'}}
    tracked = ['src.py', '.mentat/workspace.json', '.mentat/fas.json',
               'openspec/specs/application/spec.md']
    surfaces = [{'path': path, 'status': 'mapped' if path == 'src.py' else 'unexamined',
                 'requirement_ids': [requirements[0]['id']] if path == 'src.py' else []}
                for path in tracked]
    (repo / '.mentat/fas.json').write_text(json.dumps({'schema_version': 1,
        'requirements': requirements, 'claims': [claim], 'surfaces': surfaces}))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'manual feature specification')
    feature = git(repo, 'rev-parse', 'HEAD')
    assert git(repo, 'ls-tree', '-r', '--name-only', base, '--', '.mentat/fas.json') == ''
    observed = []

    class FeatureAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
            observed.append((kind, payload, context, git(repo, 'rev-parse', 'HEAD')))
            if kind == 'generate_spec' and payload.get('round') == 1:
                path = repo / 'openspec/specs/application/spec.md'
                path.write_text(path.read_text() + 'Reviewed correction\n')
            if kind in {'generate_spec', 'generate_verification'}:
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'feature specification', 'findings': [],
                    'verification_requests': []}}
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'review_spec' and correction and not payload['proposal'].get('round'):
                result['result']['decision'] = 'revise'
                result['result']['findings'] = [{'summary': 'Clarify manual specification'}]
            return result

    snapshot, worker, github = run(repo, tmp_path, agent=FeatureAgent())
    generations = [entry for entry in observed if entry[0] == 'generate_spec']
    assert len(generations) == (2 if correction else 1), snapshot
    assert generations[0][3] == feature
    assert generations[0][1]['accepted_spec_base'] == feature
    if correction:
        assert generations[1][1]['review_base'] == base
        assert generations[1][3] == feature
    reviews = [entry for entry in observed if entry[0] == 'review_spec']
    assert len(reviews) == (4 if correction else 2)
    for _, payload, context, checkout in reviews:
        assert payload['proposal']['parent'] == context['proposal_base'] == base
        assert payload['proposal']['observed_source_revision'] == feature
        assert context['proposal_head'] == checkout
        assert '.mentat/fas.json' in context['proposal_diff']
        assert 'Manual feature specification' in context['proposal_diff']
        assert '+def value(): return 1  # feature source' in context['proposal_diff']
        assert context['files']['src.py'] == (repo / 'src.py').read_text()
        assert json.loads(context['files']['.mentat/fas.json'])['requirements'] == requirements
    assert github.published, snapshot
    for kind in ('review_verification', 'review_final'):
        pair = [(payload, context) for stage, payload, context, _ in observed if stage == kind]
        assert len(pair) == 2
        for payload, context in pair:
            receipt = context['reviewed_specification']
            assert set(receipt['covered_records']['requirements']) == {r['id'] for r in requirements}
            assert claim['id'] in receipt['covered_records']['claims']
            assert 'src.py' in receipt['source_blobs_sha256']
            assert 'not proof' in receipt['meaning']
            assert payload['check_summary']['head'] == context['revision'] == github.published[0]
            assert payload['check_summary']['source']
    assert worker.revisions and all(revision == github.published[0] for revision in worker.revisions)

    # Later batches must use their reviewed incremental checkpoint, not the
    # remote base, and a forged subsequent receipt must never authorize them.
    from mentat.autonomy import current_tool_identity
    store = Store(tmp_path / 'state/state.db')
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.session_id = snapshot['session']['id']
    coordinator.config = default_config(repo)
    coordinator.tool_identity = current_tool_identity()
    coordinator.authority_digest = next(e['payload']['digest'] for e in snapshot['events']
                                        if e['kind'] == 'authority_pinned')
    checkpoint = github.published[0]
    coordinator._enqueue('scl', 'implementer', 'later.batch', {'kind': 'generate_spec',
        'target_head': checkpoint, 'accepted_spec_base': checkpoint})
    later = store.snapshot(coordinator.session_id)['jobs'][-1]['payload']
    assert later['target_head'] == later['accepted_spec_base'] == checkpoint
    assert 'review_base' not in later
    store.append_event(coordinator.session_id, 'fas_reviewed',
                       {'head': checkpoint, 'decision_ids': ['forged']})
    with pytest.raises(ValueError):
        coordinator._enqueue('scl', 'implementer', 'forged.batch', {'kind': 'generate_spec',
            'target_head': checkpoint, 'accepted_spec_base': checkpoint})


def test_initial_feature_review_capacity_retry_keeps_feature_checkpoint_and_remote_base(repo, tmp_path):
    source = repo / 'src.py'
    source.write_text('def value(): return 1\n# ' + 'x' * 200_000 + '\n')
    git(repo, 'commit', '-qam', 'large existing source')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', base)
    requirements = [{'id': str(uuid.uuid4()), 'area': area, 'origin': 'observed',
                     'statement': 'Existing manual requirement', 'citations': ['src.py'],
                     'spec_path': 'openspec/specs/application/spec.md'} for area in AREAS]
    spec = repo / 'openspec/specs/application/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('\n'.join(r['id'] for r in requirements) + '\nmanual feature intent\n')
    paths = ['src.py', '.mentat/workspace.json', '.mentat/fas.json',
             'openspec/specs/application/spec.md']
    fas = {'schema_version': 1, 'requirements': requirements, 'claims': [], 'surfaces': [
        {'path': path, 'status': 'mapped' if path == 'src.py' else 'unexamined',
         'requirement_ids': [requirements[0]['id']] if path == 'src.py' else []}
        for path in paths]}
    (repo / '.mentat/fas.json').write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'manual feature specification')
    feature = git(repo, 'rev-parse', 'HEAD')
    calls = []

    class CapacityAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
            calls.append((payload['kind'], payload, context, git(repo, 'rev-parse', 'HEAD')))
            if payload['kind'] == 'generate_spec' and not payload.get('round') and not payload.get('chunk'):
                path = repo / 'openspec/specs/application/spec.md'
                path.write_text(path.read_text() + '\n' + 's' * 170_000)
            if payload['kind'] in {'generate_spec', 'generate_verification'}:
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'feature specification', 'findings': [],
                    'verification_requests': []}}
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=CapacityAgent())
    generations = [entry for entry in calls if entry[0] == 'generate_spec']
    assert len(generations) >= 2, snapshot
    retry = generations[1]
    assert retry[1]['round'] == 1
    assert retry[1]['target_head'] == retry[1]['accepted_spec_base'] == retry[3] == feature
    assert retry[1]['review_base'] == base
    assert json.loads(retry[2]['files']['.mentat/fas.json']) == fas
    assert retry[2]['files']['src.py'] == source.read_text()
    reviews = [entry for entry in calls if entry[0] == 'review_spec'
               and entry[1]['proposal'].get('round') == 1]
    assert len(reviews) == 2
    for _, payload, context, _ in reviews:
        assert payload['proposal']['parent'] == context['proposal_base'] == base
        assert payload['proposal']['head'] == feature
        assert 'manual feature intent' in context['proposal_diff']
        assert context['files']['src.py'] == source.read_text()
    assert github.published, snapshot


def test_repair_spec_queued_before_initial_review_keeps_remote_base_through_capacity(repo, tmp_path):
    from mentat.autonomy import ReviewContextCapacity

    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('def value(): return 1  # feature\n')
    git(repo, 'commit', '-qam', 'feature head')
    feature = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('def value(): return 1  # approved repair\n')
    git(repo, 'commit', '-qam', 'repair head')
    repair = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'state/state.db')
    config = default_config(repo)
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = config
    coordinator.session_id = store.new_session(str(repo), base, feature, config, 1, 100)
    coordinator._enqueue('scl', 'implementer', 'repair.spec', {'kind': 'generate_spec',
        'target_head': repair, 'accepted_spec_base': repair, 'repair': {'source': 'repair'}})
    queued = store.snapshot(coordinator.session_id)['jobs'][0]
    assert queued['payload']['target_head'] == queued['payload']['accepted_spec_base'] == repair
    assert queued['payload']['review_base'] == base
    coordinator._handle_spec_capacity(queued, {'head': repair, 'parent': base,
        'accepted_spec_base': repair, 'round': 0, 'author_id': 'author'},
        ReviewContextCapacity('review capacity', {'src.py'}))
    retry = store.snapshot(coordinator.session_id)['jobs'][-1]['payload']
    assert retry['round'] == 1
    assert retry['target_head'] == retry['accepted_spec_base'] == repair
    assert retry['review_base'] == base
    assert retry['feedback']['rejected_head'] == repair


def test_initial_feature_missing_citation_is_not_approved(repo, tmp_path):
    base = git(repo, 'rev-parse', 'HEAD')
    requirements = [{'id': str(uuid.uuid4()), 'area': area, 'origin': 'observed',
                     'statement': 'Manually asserted', 'citations': ['missing.py'],
                     'spec_path': 'openspec/specs/application/spec.md'} for area in AREAS]
    spec = repo / 'openspec/specs/application/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('\n'.join(item['id'] for item in requirements))
    paths = ['src.py', '.mentat/workspace.json', '.mentat/fas.json',
             'openspec/specs/application/spec.md']
    (repo / '.mentat/fas.json').write_text(json.dumps({'schema_version': 1,
        'requirements': requirements, 'claims': [], 'surfaces': [
            {'path': path, 'status': 'unexamined', 'requirement_ids': []} for path in paths]}))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'feature cites missing source')
    feature = git(repo, 'rev-parse', 'HEAD')

    class NoOpAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                'decision': 'accept', 'summary': 'unchanged', 'findings': [],
                'verification_requests': []}}

    snapshot, _, github = run(repo, tmp_path, agent=NoOpAgent())
    generation = next(j for j in snapshot['jobs'] if j['payload']['kind'] == 'generate_spec')
    assert generation['payload']['target_head'] == feature
    assert generation['payload']['review_base'] == base
    assert generation['attempt'] == 3 and generation['outcome']['exhausted']
    assert not [j for j in snapshot['jobs'] if j['payload']['kind'] == 'review_spec']
    assert not [e for e in snapshot['events'] if e['kind'] == 'fas_reviewed']
    assert not github.published and not github.merged
    assert any('missing.py' in finding.get('summary', '') for finding in snapshot['findings'])
