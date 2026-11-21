"""Campaign state tests with a synthetic checker, not Lean acceptance tests."""

import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import git
from typer.testing import CliRunner

import mentat
from mentat import cli, evidence, proof
from mentat.project import load_project, snapshot


def _build_campaign(root, patch, param=None):
    identity = {"checker": "synthetic-v1", "version": "4.35.0", "toolchain_sha256": "a" * 64,
                "bwrap_sha256": "b" * 64, "policy_sha256": "c" * 64}
    outcomes = []
    audits = []
    calls = []
    log_data = {"checker.log": b"synthetic checker output"}

    def check_submission(approved, solution, *, theorem, allowed_imports, permitted_axioms, timeout_seconds):
        calls.append({"approved": approved, "solution": solution, "theorem": theorem, "timeout": timeout_seconds,
                      "allowed_imports": allowed_imports, "permitted_axioms": permitted_axioms})
        status = outcomes.pop(0) if outcomes else "accepted"
        if isinstance(status, BaseException):
            raise status
        overrides = status if isinstance(status, dict) else {}
        outcome = "accepted" if overrides else status
        result = {"status": outcome, "detail": outcome, "identity": identity.copy(),
                  "source_sha256": hashlib.sha256(solution).hexdigest(),
                  "approved_sha256": hashlib.sha256(approved).hexdigest(),
                  "theorem": theorem, "logs": log_data.copy()}
        return {**result, **overrides}

    def audit_approved(*args, **kwargs):
        overrides = audits.pop(0) if audits else {}
        return {"status": "accepted", "detail": "synthetic audit", "identity": identity.copy(),
                "logs": {"audit.log": b"synthetic audit output"}, **overrides}

    fake = SimpleNamespace(checker_identity=lambda: identity.copy(), audit_approved=audit_approved,
                           check_submission=check_submission)
    patch.setitem(sys.modules, "mentat.proof_checker", fake)
    patch.setattr(mentat, "proof_checker", fake, raising=False)
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.test")
    checks = repo / ".mentat"
    checks.mkdir()
    contract = {
        "version": 1, "allow_host": True,
        "claims": [{"id": "claim", "statement": "Synthetic claim"}],
        "formal": [
            {"id": name, "claim": "claim", "engine": "lean-comparator", "source": "proof.lean",
             "theorem": f"Proof.{name}", "allowed_imports": ["Init"], "permitted_axioms": []}
            for name in ("first", "second")
        ],
        "behavior": [{"id": "behavior", "claim": "claim", "argv": [sys.executable, "{checks}/check.py"]}],
    }
    (checks / "contract.json").write_text(json.dumps(contract))
    (checks / "proof.lean").write_text("-- approved synthetic theorem\n")
    (checks / "check.py").write_text("print('B' * 120)\n" if param == "output-budget" else "pass\n")
    (repo / "source.txt").write_text("candidate one\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    project = load_project(repo, base, "host")
    candidate = snapshot(repo, base)
    state = evidence.state_directory(root / "state")
    scope = ["--repo", str(repo), "--base", base, "--runner", "host"]
    def invoke(*args):
        return CliRunner().invoke(cli.app, ["--state", str(state), *args])
    patch.setattr(cli, "operator_confirm", lambda _: None)
    checked = invoke("spec", "check", *scope, "--json")
    assert checked.exit_code == 0, checked.output
    digest = json.loads(checked.output)["contract_digest"]
    approved = invoke("spec", "approve", digest, *scope)
    assert approved.exit_code == 0, approved.output
    approval = evidence.read_json(state / "approvals" / f"{digest}.json")

    def prepare(name="task", targets=None):
        out = root / name
        result = proof.prepare(state, project, candidate, approval, out, targets=targets)
        return result["campaign_id"], out

    def submission(name, *targets):
        directory = root / name
        for target in targets:
            (directory / target).mkdir(parents=True)
            (directory / target / "Solution.lean").write_text(f"-- solution {target}\n")
        return directory

    return SimpleNamespace(repo=repo, base=base, state=state, project=project, candidate=candidate,
                           approval=approval, identity=identity, outcomes=outcomes, audits=audits, calls=calls, log_data=log_data,
                           prepare=prepare, submission=submission, invoke=invoke, scope=scope)


@pytest.fixture
def campaign(tmp_path, monkeypatch, request):
    return _build_campaign(tmp_path, monkeypatch, getattr(request, "param", None))


def test_prepare_check_recheck_bind_exact_inputs_with_synthetic_checker(campaign):
    c = campaign
    ident, task = c.prepare(targets=["first"])
    manifest = evidence.read_json(c.state / "proof-campaigns" / ident / "manifest.json")
    assert manifest["repo"] == str(c.repo) and manifest["base"] == c.base
    assert manifest["revision"] == c.candidate.revision and manifest["snapshot_digest"] == c.candidate.digest
    assert manifest["contract_digest"] == c.project.fingerprint and manifest["verifier_digest"] == c.project.verifier_digest
    assert manifest["approval"] == c.approval and manifest["checker_identity"] == c.identity
    assert manifest["targets"] == ["first"]
    assert (task / "first" / "Solution.lean").read_bytes() == c.project.files["proof.lean"]
    submitted = c.submission("submission", "first")
    checked = proof.check(c.state, ident, submitted)
    assert checked["status"] == "pass" and checked["targets"][0]["status"] == "accepted"
    results, artifacts = proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
    assert [(item["id"], item["status"]) for item in results] == [("first", "pass")]
    assert len(c.calls) == 2 and all(call["timeout"] == 120 for call in c.calls)
    assert all(call["theorem"] == "Proof.first" and call["allowed_imports"] == ["Init"]
               and call["permitted_axioms"] == [] for call in c.calls)
    assert all(call["approved"] == c.project.files["proof.lean"] for call in c.calls)
    assert all(call["solution"] == (submitted / "first" / "Solution.lean").read_bytes() for call in c.calls)
    source_name = results[0]["artifacts"]["proof-source"]
    source = (submitted / "first" / "Solution.lean").read_bytes()
    assert artifacts[source_name] == source
    assert results[0]["evidence"]["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert [data for name, data in artifacts.items() if name != source_name] == [b"synthetic checker output"]


def test_missing_or_latest_rejected_submission_never_falls_back(campaign):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    assert proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])[0][0]["status"] == "incomplete"
    submission = c.submission("one", "first")
    assert proof.check(c.state, ident, submission)["status"] == "pass"
    c.outcomes.append("rejected")
    assert proof.check(c.state, ident, submission)["status"] == "violation"
    count = len(c.calls)
    assert proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])[0][0]["status"] == "incomplete"
    assert len(c.calls) == count


@pytest.mark.parametrize("broken", ["identity", "source_sha256", "approved_sha256", "theorem", "status", "logs", "log-bytes"])
def test_malformed_checker_evidence_cannot_pass(campaign, broken):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    if broken == "log-bytes":
        c.outcomes.append({"logs": {"checker.log": "not bytes"}})
    else:
        c.outcomes.append({broken: "wrong"})
    checked = proof.check(c.state, ident, c.submission("bad", "first"))
    assert checked["status"] == "incomplete"
    assert proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])[0][0]["status"] == "incomplete"


def test_changed_candidate_approval_verifier_and_checker_are_not_reusable(campaign, tmp_path, monkeypatch):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    assert proof.check(c.state, ident, c.submission("submission", "first"))["status"] == "pass"
    (c.repo / "source.txt").write_text("candidate two\n")
    git(c.repo, "add", "source.txt")
    git(c.repo, "commit", "-qm", "new candidate")
    changed = snapshot(c.repo, git(c.repo, "rev-parse", "HEAD"))
    with pytest.raises(ValueError, match="Proof campaign does not match this verification"):
        proof.recheck(c.state, c.project, changed, c.approval, [ident])
    import mentat.project as project_module

    copied = tmp_path / "changed-verifier"
    copied.mkdir()
    for source in Path(project_module.__file__).parent.glob("*.py"):
        shutil.copyfile(source, copied / source.name)
    altered = copied / "proof.py"
    altered.write_bytes(altered.read_bytes() + b"\n# changed verifier\n")
    with monkeypatch.context() as scoped:
        scoped.setattr(project_module, "__file__", str(copied / "project.py"))
        with pytest.raises(ValueError, match="Project or candidate snapshot changed"):
            proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
    c.identity["checker"] = "synthetic-v2"
    with pytest.raises(ValueError, match="Proof checker identity changed"):
        proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
    c.identity["checker"] = "synthetic-v1"
    approval_path = c.state / "approvals" / f"{c.project.fingerprint}.json"
    evidence.write_record(approval_path, evidence.json_bytes({**c.approval, "approved_at": "changed"}))
    with pytest.raises(ValueError, match="Contract approval changed; approve and prepare a new campaign"):
        proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])


@pytest.mark.parametrize(
    "damage",
    [
        lambda approval: {key: value for key, value in approval.items() if key != "approved_at"},
        lambda approval: {**approval, "approved_at": 1},
    ],
)
def test_malformed_approval_on_disk_refuses_recheck(campaign, damage):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    path = c.state / "approvals" / f"{c.project.fingerprint}.json"
    evidence.write_record(path, evidence.json_bytes(damage(c.approval)))
    with pytest.raises(
        ValueError,
        match=r"^Contract approval changed; approve and prepare a new campaign$",
    ):
        proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])


TAMPERED = {
    "manifest": "Proof campaign manifest failed integrity validation",
    "task": "Proof task instructions failed integrity validation",
    "approved": "Frozen approved source failed integrity validation",
    "submission": "Proof submission failed integrity validation",
    "entry": "Proof ledger entry failed integrity validation",
    "log": "Proof checker log failed integrity validation",
    "approved-symlink": "Too many levels of symbolic links",
}


@pytest.mark.parametrize("damage", list(TAMPERED))
def test_tampered_campaign_data_cannot_recheck(campaign, tmp_path, damage):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    proof.check(c.state, ident, c.submission("submission", "first"))
    path = c.state / "proof-campaigns" / ident
    target = {
        "manifest": path / "manifest.json", "task": path / "TASK.md",
        "approved": path / "approved/first.lean", "approved-symlink": path / "approved/first.lean",
        "submission": next((path / "submissions").iterdir()), "entry": next((path / "entries").iterdir()),
        "log": next((path / "logs").iterdir()),
    }[damage]
    if damage == "approved-symlink":
        duplicate = tmp_path / "outside.lean"
        duplicate.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(duplicate)
    else:
        target.write_bytes(b"tampered")
    with pytest.raises((ValueError, OSError), match=TAMPERED[damage]):
        proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])


WEAK_SOURCE = b"-- weakened theorem\n"


def _forge(c, ident, damage):
    """Rewrite campaign state as same-account code could, keeping manifest and receipt consistent."""
    path = c.state / "proof-campaigns" / ident
    manifest = evidence.read_json(path / "manifest.json")
    target = manifest["all_targets"][0]
    if damage == "theorem":
        target["theorem"] = "Proof.trivial"
    elif damage == "allowed_imports":
        target["allowed_imports"] = ["Init", "Lean"]
    elif damage == "permitted_axioms":
        target["permitted_axioms"] = ["sorryAx"]
    elif damage == "approved-source":
        evidence.write_record(path / "approved" / "first.lean", WEAK_SOURCE)
        target["approved_sha256"] = hashlib.sha256(WEAK_SOURCE).hexdigest()
    elif damage == "dropped-target":
        manifest["all_targets"] = [target]
    elif damage == "unknown-target":
        manifest["targets"] = ["first", "forged"]
    else:
        manifest["targets"] = ["first", "first"]
    raw = evidence.json_bytes(manifest)
    evidence.write_record(path / "manifest.json", raw)
    evidence.write_record(c.state / "proof-campaign-receipts" / ident, hashlib.sha256(raw).hexdigest().encode())


FORGERIES = ["theorem", "allowed_imports", "permitted_axioms", "approved-source", "dropped-target",
             "unknown-target", "duplicate-target"]


@pytest.mark.parametrize("damage", FORGERIES)
def test_forged_campaign_targets_are_rejected_before_checking(campaign, damage):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    _forge(c, ident, damage)
    with pytest.raises(ValueError, match="approved contract"):
        proof.check(c.state, ident, c.submission("weak", "first"))
    assert c.calls == []


@pytest.mark.parametrize("damage", FORGERIES)
def test_forged_campaign_targets_fail_verify_after_acceptance(campaign, damage):
    c = campaign
    ident, _ = c.prepare(targets=["first", "second"])
    assert proof.check(c.state, ident, c.submission("ok", "first", "second"))["status"] == "pass"
    _forge(c, ident, damage)
    with pytest.raises(ValueError, match="approved contract"):
        proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
    result = c.invoke("verify", "--rev", c.base, *c.scope, "--proof-campaign", ident, "--json")
    assert result.exit_code != 0 and json.loads(result.output)["status"] != "pass", result.output
    assert len(c.calls) == 2
    assert all(call["approved"] == c.project.files["proof.lean"] for call in c.calls)


def test_recheck_binds_the_manifest_read_under_the_ledger_lock(campaign, tmp_path, monkeypatch):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    assert proof.check(c.state, ident, c.submission("ok", "first"))["status"] == "pass"
    # A self-consistent, approved campaign for another repository with a weaker theorem.
    forged = tmp_path / "forged"
    shutil.copytree(c.repo, forged)
    contract = json.loads((forged / ".mentat" / "contract.json").read_text())
    contract["formal"][0]["theorem"] = "Proof.trivial"
    (forged / ".mentat" / "contract.json").write_text(json.dumps(contract))
    git(forged, "commit", "-qam", "weaken")
    base = git(forged, "rev-parse", "HEAD")
    scope = ["--repo", str(forged), "--base", base, "--runner", "host"]
    digest = json.loads(c.invoke("spec", "check", *scope, "--json").output)["contract_digest"]
    assert c.invoke("spec", "approve", digest, *scope).exit_code == 0
    project = load_project(forged, base, "host")
    approval = evidence.read_json(c.state / "approvals" / f"{digest}.json")
    weak = proof.prepare(c.state, project, snapshot(forged, base), approval, tmp_path / "weak-task",
                         targets=["first"])["campaign_id"]
    assert proof.check(c.state, weak, c.submission("weak", "first"))["status"] == "pass"
    original, reads = proof._campaign, []

    def swapped(state, campaign_id):
        reads.append(campaign_id)
        # The first read passes; state is replaced while recheck waits for the ledger lock.
        return original(state, weak if len(reads) > 1 else campaign_id)

    monkeypatch.setattr(proof, "_campaign", swapped)
    count = len(c.calls)
    with pytest.raises(ValueError, match="does not match this verification"):
        proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
    assert len(c.calls) == count


@pytest.mark.parametrize("targets", [[], ["unknown"], ["first", "first"], "first", [1]])
def test_prepare_rejects_invalid_target_selection(campaign, targets):
    with pytest.raises(ValueError, match="Select distinct"):
        campaign.prepare(targets=targets)


SUBMISSION_REFUSALS = {
    "extra-root": "Unexpected proof submission path: extra",
    "extra-file": "Target first must contain only Solution.lean",
    "symlink-target": "Expected a real directory",
    "symlink-source": "Too many levels of symbolic links",
    "invalid-utf8": "Proof source must be UTF-8",
}


@pytest.mark.parametrize("damage", list(SUBMISSION_REFUSALS))
def test_submission_requires_real_target_directories_with_one_utf8_solution(campaign, tmp_path, damage):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    submission = c.submission("submission", "first")
    target = submission / "first"
    solution = target / "Solution.lean"
    if damage == "extra-root":
        (submission / "extra").mkdir()
    elif damage == "extra-file":
        (target / "other.txt").write_text("unexpected")
    elif damage == "symlink-target":
        renamed = tmp_path / "moved-target"
        target.rename(renamed)
        target.symlink_to(renamed, target_is_directory=True)
    elif damage == "symlink-source":
        other = tmp_path / "outside.lean"
        other.write_text("-- outside")
        solution.unlink()
        solution.symlink_to(other)
    else:
        solution.write_bytes(b"\xff")
    with pytest.raises((ValueError, OSError), match=SUBMISSION_REFUSALS[damage]):
        proof.check(c.state, ident, submission)
    assert not c.calls


def test_partial_submission_and_overlapping_campaigns(campaign):
    c = campaign
    first, _ = c.prepare("task-first", targets=["first", "second"])
    second, _ = c.prepare("task-second", targets=["second"])
    result = proof.check(c.state, first, c.submission("partial", "first"))
    assert result["status"] == "pass" and [entry["id"] for entry in result["targets"]] == ["first"]
    checks, _ = proof.recheck(c.state, c.project, c.candidate, c.approval, [first])
    assert {item["id"]: item["status"] for item in checks} == {"first": "pass", "second": "incomplete"}
    with pytest.raises(ValueError, match="overlap"):
        proof.recheck(c.state, c.project, c.candidate, c.approval, [first, second])


def test_round_limit_persists_when_campaign_is_reopened(campaign):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    submission = c.submission("submission", "first")
    c.outcomes.extend(["rejected", "rejected", "rejected"])
    assert [proof.check(c.state, ident, submission)["status"] for _ in range(3)] == ["violation"] * 3
    assert proof.check(c.state, ident, submission)["status"] == "incomplete"
    assert len(c.calls) == 3
    assert proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])[0][0]["status"] == "incomplete"


def test_rounds_used_in_one_campaign_are_not_reset_by_preparing_another(campaign):
    c = campaign
    first, _ = c.prepare("task-first", targets=["first"])
    submission = c.submission("submission", "first")
    c.outcomes.extend(["rejected"] * 3)
    assert [proof.check(c.state, first, submission)["status"] for _ in range(3)] == ["violation"] * 3
    second, _ = c.prepare("task-second", targets=["first"])
    count = len(c.calls)
    checked = proof.check(c.state, second, c.submission("again", "first"))
    assert checked["status"] == "incomplete" and len(c.calls) == count


def test_seconds_used_in_one_campaign_are_not_reset_by_preparing_another(campaign, monkeypatch):
    c = campaign
    first, _ = c.prepare("task-first", targets=["first"])
    # Three rejected proof attempts of 600 seconds each use the whole shared time budget.
    ticks = iter(range(0, 100000, 600))
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    c.outcomes.extend(["rejected"] * 3)
    submission = c.submission("submission", "first")
    assert [proof.check(c.state, first, submission)["status"] for _ in range(3)] == ["violation"] * 3
    assert proof._usage(proof._ledger(c.state / "proof-campaigns" / first))[2] == 1800
    second, _ = c.prepare("task-second", targets=["second"])
    count = len(c.calls)
    assert proof.check(c.state, second, c.submission("again", "second"))["status"] == "incomplete"
    assert len(c.calls) == count


def _changed_inputs(c, change):
    """Approved inputs for a new candidate snapshot or a new contract in the same repository."""
    if change == "snapshot":
        (c.repo / "source.txt").write_text("candidate two\n")
        git(c.repo, "commit", "-qam", "candidate two")
        return c.project, snapshot(c.repo, git(c.repo, "rev-parse", "HEAD")), c.approval
    contract = json.loads((c.repo / ".mentat" / "contract.json").read_text())
    contract["claims"][0]["statement"] = "Synthetic claim, revised"
    (c.repo / ".mentat" / "contract.json").write_text(json.dumps(contract))
    git(c.repo, "commit", "-qam", "revise contract")
    base = git(c.repo, "rev-parse", "HEAD")
    scope = ["--repo", str(c.repo), "--base", base, "--runner", "host"]
    digest = json.loads(c.invoke("spec", "check", *scope, "--json").output)["contract_digest"]
    assert c.invoke("spec", "approve", digest, *scope).exit_code == 0
    approval = evidence.read_json(c.state / "approvals" / f"{digest}.json")
    return load_project(c.repo, base, "host"), snapshot(c.repo, base), approval


@pytest.mark.parametrize("change", ["target", "snapshot", "contract"])
def test_exhausted_rounds_do_not_affect_other_targets_snapshots_or_contracts(campaign, tmp_path, change):
    c = campaign
    first, _ = c.prepare("task-first", targets=["first"])
    submission = c.submission("submission", "first")
    c.outcomes.extend(["rejected"] * 3)
    assert [proof.check(c.state, first, submission)["status"] for _ in range(3)] == ["violation"] * 3
    targets = ["second"] if change == "target" else ["first"]
    project, candidate, approval = (c.project, c.candidate, c.approval)
    if change != "target":
        project, candidate, approval = _changed_inputs(c, change)
    other = proof.prepare(c.state, project, candidate, approval, tmp_path / "other-task", targets=targets)
    checked = proof.check(c.state, other["campaign_id"], c.submission("other", *targets))
    assert checked["status"] == "pass"


@pytest.mark.parametrize("change", ["snapshot", "contract"])
def test_exhausted_seconds_do_not_leak_across_snapshot_or_contract(campaign, tmp_path, monkeypatch, change):
    c = campaign
    first, _ = c.prepare("task-first", targets=["first"])
    ticks = iter(range(0, 10000, 120))
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    assert proof.check(c.state, first, c.submission("submission", "first"))["status"] == "pass"
    for _ in range(14):
        assert proof.recheck(c.state, c.project, c.candidate, c.approval, [first])[0][0]["status"] == "pass"
    project, candidate, approval = _changed_inputs(c, change)
    other = proof.prepare(c.state, project, candidate, approval, tmp_path / "other-task", targets=["first"])
    count = len(c.calls)
    assert proof.check(c.state, other["campaign_id"], c.submission("other", "first"))["status"] == "pass"
    assert len(c.calls) == count + 1


@pytest.mark.parametrize("damage, refusal", [
    ("entry", "Proof campaign {id} in this state is invalid, so budgets cannot be established"),
    ("stray", "Another proof campaign in this state is invalid, so budgets cannot be established"),
])
def test_invalid_sibling_campaign_refuses_checks(campaign, damage, refusal):
    c = campaign
    first, _ = c.prepare("task-first", targets=["first"])
    c.outcomes.extend(["rejected"] * 3)
    submission = c.submission("submission", "first")
    assert [proof.check(c.state, first, submission)["status"] for _ in range(3)] == ["violation"] * 3
    if damage == "entry":
        next((c.state / "proof-campaigns" / first / "entries").iterdir()).write_bytes(b"tampered")
    else:
        (c.state / "proof-campaigns" / "stray").write_text("not a campaign")
    second, _ = c.prepare("task-second", targets=["first"])
    count = len(c.calls)
    with pytest.raises(ValueError, match=refusal.format(id=first)):
        proof.check(c.state, second, c.submission("again", "first"))
    assert len(c.calls) == count


def test_dot_named_campaign_is_not_dropped_from_accounting(campaign):
    c = campaign
    first, _ = c.prepare("task-first", targets=["first"])
    c.outcomes.extend(["rejected"] * 3)
    submission = c.submission("submission", "first")
    assert [proof.check(c.state, first, submission)["status"] for _ in range(3)] == ["violation"] * 3
    campaigns = c.state / "proof-campaigns"
    (campaigns / first).rename(campaigns / ".renamed")
    second, _ = c.prepare("task-second", targets=["first"])
    count = len(c.calls)
    with pytest.raises(ValueError, match="Another proof campaign"):
        proof.check(c.state, second, c.submission("again", "first"))
    assert len(c.calls) == count


def test_prepare_staging_and_finder_metadata_are_not_campaigns(campaign):
    c = campaign
    first, _ = c.prepare("task-first", targets=["first"])
    c.outcomes.extend(["rejected"] * 3)
    submission = c.submission("submission", "first")
    assert [proof.check(c.state, first, submission)["status"] for _ in range(3)] == ["violation"] * 3
    (c.state / "proof-campaigns" / ".prepare-stale").mkdir()
    (c.state / "proof-campaigns" / ".DS_Store").write_bytes(b"")
    (c.state / "proof-campaigns" / f"._{first}").write_bytes(b"AppleDouble")
    second, _ = c.prepare("task-second", targets=["first"])
    assert proof.check(c.state, second, c.submission("again", "first"))["status"] == "incomplete"


def test_recheck_is_charged_only_its_own_campaign_time(campaign, monkeypatch):
    c = campaign
    accepted, _ = c.prepare("task-first", targets=["first"])
    ticks = iter(range(0, 100000, 120))
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    assert proof.check(c.state, accepted, c.submission("submission", "first"))["status"] == "pass"
    spender, _ = c.prepare("task-second", targets=["second"])
    spent = c.submission("spent", "second")
    assert proof.check(c.state, spender, spent)["status"] == "pass"
    for _ in range(14):
        assert proof.recheck(c.state, c.project, c.candidate, c.approval, [spender])[0][0]["status"] == "pass"
    assert proof._usage(proof._ledger(c.state / "proof-campaigns" / spender))[2] == 1800
    results, _ = proof.recheck(c.state, c.project, c.candidate, c.approval, [accepted])
    assert results[0]["status"] == "pass"
    # Verification rechecks in sibling campaigns do not use up a new campaign's budget.
    third, _ = c.prepare("task-third", targets=["first"])
    assert proof.check(c.state, third, c.submission("third", "first"))["status"] == "pass"


def test_reserve_without_target_is_refused_as_invalid_ledger(campaign):
    c = campaign
    first, _ = c.prepare("task-first", targets=["first"])
    other, _ = c.prepare("task-second", targets=["first"])
    path = c.state / "proof-campaigns" / other
    source = b"-- malformed reserve\n"
    attempt = "e" * 32
    evidence.write_record(path / "submissions" / f"{attempt}.lean", source)
    proof._append(path, proof._ledger(path), {"kind": "reserve", "attempt": attempt,
                  "source_sha256": hashlib.sha256(source).hexdigest(), "recheck": False, "denied": False})
    with pytest.raises(ValueError, match="Invalid proof campaign ledger entry"):
        proof._ledger(path)
    with pytest.raises(ValueError, match=f"Proof campaign {other} in this state is invalid"):
        proof.check(c.state, first, c.submission("submission", "first"))
    assert not c.calls


def test_recheck_is_exempt_from_the_round_limit_after_last_round(campaign):
    c = campaign
    ident, _ = c.prepare("task-first", targets=["first"])
    submission = c.submission("submission", "first")
    c.outcomes.extend(["rejected", "rejected"])
    assert [proof.check(c.state, ident, submission)["status"] for _ in range(3)] == ["violation", "violation", "pass"]
    second, _ = c.prepare("task-second", targets=["first"])
    assert proof.check(c.state, second, c.submission("again", "first"))["status"] == "incomplete"
    count = len(c.calls)
    results, _ = proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
    assert results[0]["status"] == "pass" and len(c.calls) == count + 1


MALFORMED_MANIFESTS = {
    "non-dict": lambda m: [],
    "repo": lambda m: {**m, "repo": 1},
    "targets": lambda m: {**m, "targets": "first"},
    "all_targets": lambda m: {**m, "all_targets": [{"id": "first"}]},
    "missing-revision": lambda m: {k: v for k, v in m.items() if k != "revision"},
}


@pytest.mark.parametrize("damage", list(MALFORMED_MANIFESTS))
def test_malformed_manifest_shapes_are_user_errors(campaign, damage):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    path = c.state / "proof-campaigns" / ident
    raw = evidence.json_bytes(MALFORMED_MANIFESTS[damage](evidence.read_json(path / "manifest.json")))
    evidence.write_record(path / "manifest.json", raw)
    evidence.write_record(c.state / "proof-campaign-receipts" / ident, hashlib.sha256(raw).hexdigest().encode())
    submission = c.submission("malformed", "first")
    with pytest.raises(ValueError, match="Proof campaign manifest is malformed"):
        proof.check(c.state, ident, submission)
    with pytest.raises(ValueError, match="Proof campaign manifest is malformed"):
        proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
    checked = c.invoke("proof", "check", ident, "--submission", str(submission), "--json")
    assert checked.exit_code == 2, (checked.output, checked.exception)
    assert not c.calls


def test_interrupted_reservation_charges_120_seconds_and_cannot_pass(campaign):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    submission = c.submission("submission", "first")
    c.outcomes.append(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        proof.check(c.state, ident, submission)
    path = c.state / "proof-campaigns" / ident
    ledger = proof._ledger(path)
    assert [entry["kind"] for entry in ledger] == ["reserve"]
    assert proof._usage(ledger)[2] == 120
    count = len(c.calls)
    checks, _ = proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
    assert checks[0]["status"] == "incomplete" and len(c.calls) == count
    assert proof.check(c.state, ident, submission)["status"] == "pass"
    assert proof._usage(proof._ledger(path))[2] >= 120


def test_active_time_limit_includes_rechecks_across_resume(campaign, monkeypatch):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    ticks = iter(range(0, 10000, 120))
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    assert proof.check(c.state, ident, c.submission("submission", "first"))["status"] == "pass"
    for _ in range(14):
        checks, _ = proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
        assert checks[0]["status"] == "pass"
    path = c.state / "proof-campaigns" / ident
    assert proof._usage(proof._ledger(path))[2] == 1800
    checks, _ = proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
    assert checks[0]["status"] == "incomplete"
    assert len(c.calls) == 15


def test_concurrent_build_lock_rejects_without_consuming_attempt(campaign):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    submission = c.submission("submission", "first")
    with proof._lock(c.state / "proof-build.lock", blocking=False):
        with pytest.raises(ValueError, match="running"):
            proof.check(c.state, ident, submission)
    assert proof._ledger(c.state / "proof-campaigns" / ident) == []
    assert not c.calls
    assert proof.check(c.state, ident, submission)["status"] == "pass"


def test_interrupted_ledger_publication_recovers_from_hashed_entry(campaign, monkeypatch):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    submission = c.submission("submission", "first")
    original = evidence.write_record
    interrupted = False

    def fail_ledger(path, data):
        nonlocal interrupted
        if path.name == "ledger.json" and not interrupted:
            interrupted = True
            raise OSError("synthetic interrupted publication")
        return original(path, data)

    with monkeypatch.context() as scoped:
        scoped.setattr(evidence, "write_record", fail_ledger)
        with pytest.raises(OSError, match="interrupted"):
            proof.check(c.state, ident, submission)
    path = c.state / "proof-campaigns" / ident
    recovered = proof._ledger(path)
    assert [entry["kind"] for entry in recovered] == ["reserve"]
    assert proof._usage(recovered)[2] == 120
    assert proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])[0][0]["status"] == "incomplete"
    assert proof.check(c.state, ident, submission)["status"] == "pass"
    assert [entry["kind"] for entry in proof._ledger(path)] == ["reserve", "reserve", "complete"]


def test_interrupted_completion_publication_recovers_accepted_hash_chain(campaign, monkeypatch):
    c = campaign
    ident, _ = c.prepare(targets=["first"])
    submission = c.submission("submission", "first")
    original = evidence.write_record
    writes = 0

    def fail_second_ledger(path, data):
        nonlocal writes
        if path.name == "ledger.json":
            writes += 1
            if writes == 2:
                raise OSError("synthetic interrupted completion")
        return original(path, data)

    with monkeypatch.context() as scoped:
        scoped.setattr(evidence, "write_record", fail_second_ledger)
        with pytest.raises(OSError, match="interrupted completion"):
            proof.check(c.state, ident, submission)
    path = c.state / "proof-campaigns" / ident
    recovered = proof._ledger(path)
    assert [entry["kind"] for entry in recovered] == ["reserve", "complete"]
    assert recovered[-1]["status"] == "accepted"
    results, artifacts = proof.recheck(c.state, c.project, c.candidate, c.approval, [ident])
    assert results[0]["status"] == "pass"
    source_name = results[0]["artifacts"]["proof-source"]
    source = (submission / "first" / "Solution.lean").read_bytes()
    assert artifacts[source_name] == source
    assert results[0]["evidence"]["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert [data for name, data in artifacts.items() if name != source_name] == [b"synthetic checker output"]


INVALID_AUDIT = "Approved proof source audit returned invalid evidence"


@pytest.mark.parametrize("overrides, message", [
    ({"status": "rejected"}, "Approved proof source audit failed: synthetic audit"),
    ({"status": "unknown"}, INVALID_AUDIT),
    ({"identity": {"checker": "other"}}, INVALID_AUDIT),
    ({"detail": None}, INVALID_AUDIT),
    ({"logs": "not a mapping"}, INVALID_AUDIT),
    ({"logs": {"audit.log": "not bytes"}}, INVALID_AUDIT),
])
def test_prepare_rejects_malformed_or_unaccepted_approved_audit(campaign, tmp_path, overrides, message):
    c = campaign
    c.audits.append(overrides)
    with pytest.raises(ValueError, match=message):
        c.prepare("failed-audit", targets=["first"])
    assert not (tmp_path / "failed-audit").exists()
    assert not (c.state / "proof-campaigns").exists()


def test_cli_prepare_check_and_repeat_campaigns_recheck_formal_gate(campaign, tmp_path):
    """Synthetic checker exercises the CLI integration; it does not prove Lean acceptance."""
    c = campaign
    ids = []
    for target in ("first", "second"):
        task = tmp_path / f"cli-task-{target}"
        prepared = c.invoke("proof", "prepare", *c.scope, "--rev", c.base,
                            "--target", target, "--out", str(task), "--json")
        assert prepared.exit_code == 0, (prepared.output, prepared.exception)
        campaign_id = json.loads(prepared.output)["campaign_id"]
        ids.append(campaign_id)
        submission = c.submission(f"cli-submission-{target}", target)
        checked = c.invoke("proof", "check", campaign_id, "--submission", str(submission), "--json")
        assert checked.exit_code == 0, (checked.output, checked.exception)
        assert json.loads(checked.output)["targets"][0]["status"] == "accepted"
    one = c.invoke("verify", "--rev", c.base, *c.scope, "--proof-campaign", ids[0], "--json")
    assert one.exit_code == 2, (one.output, one.exception)
    partial = json.loads(one.output)
    assert partial["behavior_skipped"] is True
    assert {item["id"]: item["status"] for item in partial["model_checks"]} == {"first": "pass", "second": "incomplete"}
    complete = c.invoke("verify", "--rev", c.base, *c.scope, "--proof-campaign", ids[0],
                        "--proof-campaign", ids[1], "--json")
    assert complete.exit_code == 0, (complete.output, complete.exception)
    record = json.loads(complete.output)
    assert record["status"] == "pass" and record["behavior_skipped"] is False
    assert {item["id"]: item["status"] for item in record["model_checks"]} == {"first": "pass", "second": "pass"}
    assert {item["id"]: item["evidence"]["campaign_id"] for item in record["model_checks"]} == dict(zip(("first", "second"), ids))
    assert len(c.calls) == 5  # two checks, one partial recheck, two full rechecks


@pytest.fixture(scope="module")
def recorded_source(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        c = _build_campaign(tmp_path_factory.mktemp("recorded"), patch)
        ident, _ = c.prepare(targets=["first", "second"])
        submission = c.submission("saved-submission", "first", "second")
        (submission / "first" / "Solution.lean").write_text('-- SECRET_PROOF <script>alert("x")</script>\n')
        assert proof.check(c.state, ident, submission)["status"] == "pass"
        verified = c.invoke("verify", "--rev", c.base, *c.scope, "--proof-campaign", ident, "--json")
        assert verified.exit_code == 0, (verified.output, verified.exception)
        run_id = json.loads(verified.output)["run_id"]
    return c, submission, run_id


@pytest.fixture
def recorded_proof(recorded_source, tmp_path):
    c, submission, run_id = recorded_source
    # Damage tests mutate the saved bundle and receipt, so each test reads and writes its
    # own copy, through both `state` and `invoke`. read_run_snapshot and the HTML report
    # need only these two items, and copytree keeps the 0700 modes.
    state = evidence.state_directory(tmp_path / "state")
    (state / "runs").mkdir(mode=0o700)
    (state / "receipts").mkdir(mode=0o700)
    shutil.copytree(c.state / "runs" / run_id, state / "runs" / run_id)
    shutil.copy2(c.state / "receipts" / run_id, state / "receipts" / run_id)
    bundle, record, _ = evidence.read_run_snapshot(state, run_id)

    def invoke(*args):
        return CliRunner().invoke(cli.app, ["--state", str(state), *args])

    copy = SimpleNamespace(**{**vars(c), "state": state, "invoke": invoke})
    return copy, submission, bundle, record


SAVED_PROOF_REFUSALS = {
    "missing-source": "Passing proof source is missing or mismatched",
    "wrong-source": "Passing proof source is missing or mismatched",
    "source-hash": "Passing proof source is missing or mismatched",
    "approved-hash": "Invalid saved proof source identity",
    "revision": "Saved proof does not match its verification run",
    "base": "Saved proof does not match its verification run",
    "contract": "Saved proof does not match its verification run",
    "verifier": "Saved proof does not match its verification run",
    "environment": "Invalid saved proof environment",
    "environment-hash": "Invalid saved proof environment hash",
    "environment-conflict": "Saved proof environment does not match the recorded checker",
    "theorem": "Invalid saved proof theorem identity",
    "source-bytes": "Evidence artifact failed its integrity check",
    "source-symlink": "Evidence artifact failed its integrity check",
}


@pytest.mark.parametrize("damage", list(SAVED_PROOF_REFUSALS))
def test_saved_passing_proof_rejects_corrupted_source_or_bindings(recorded_proof, tmp_path, damage):
    c, _, bundle, record = recorded_proof
    check = record["model_checks"][0]
    source_name = check["artifacts"]["proof-source"]
    if damage == "source-bytes":
        (bundle / source_name).write_bytes(b"tampered")
    elif damage == "source-symlink":
        source = bundle / source_name
        other = tmp_path / "other-source"
        other.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(other)
    else:
        if damage == "missing-source":
            del check["artifacts"]["proof-source"]
        elif damage == "wrong-source":
            check["artifacts"]["proof-source"] = "report.md"
        elif damage == "source-hash":
            check["evidence"]["source_sha256"] = "f" * 64
        elif damage == "approved-hash":
            check["evidence"]["approved_sha256"] = "not-a-hash"
        elif damage == "environment":
            check["evidence"]["environment"]["version"] = "wrong"
        elif damage == "environment-hash":
            check["evidence"]["environment"]["toolchain_sha256"] = "/private/not-a-hash"
        elif damage == "environment-conflict":
            check["evidence"]["environment"]["toolchain_sha256"] = "f" * 64
        elif damage == "theorem":
            check["evidence"]["theorem"] = "../private"
        else:
            key = {"revision": "revision", "base": "base", "contract": "contract_digest", "verifier": "verifier_digest"}[damage]
            check["evidence"][key] = "f" * len(check["evidence"][key])
        raw = evidence.json_bytes(record)
        (bundle / "manifest.json").write_bytes(raw)
        (c.state / "receipts" / record["run_id"]).write_text(evidence.digest(raw))
    with pytest.raises(ValueError, match=SAVED_PROOF_REFUSALS[damage]):
        evidence.read_run_snapshot(c.state, record["run_id"])


@pytest.mark.parametrize("version", ["4.36.0", "4.31.0", ["4.35.0"]])
def test_saved_proof_environment_accepts_only_historical_checker_versions(recorded_proof, version):
    _, _, _, record = recorded_proof
    evidence.proof_evidence(record)
    record["model_checks"][0]["evidence"]["environment"]["version"] = version
    with pytest.raises(ValueError, match="Invalid saved proof environment"):
        evidence.proof_evidence(record)


def test_proof_html_shows_source_filename_without_source_or_runtime(recorded_proof, tmp_path):
    c, submission, _, record = recorded_proof
    output = tmp_path / "proof-report.html"
    result = c.invoke("evidence", "render", record["run_id"], "--out", str(output))
    assert result.exit_code == 0, (result.output, result.exception)
    html = output.read_text()
    source_name = record["model_checks"][0]["artifacts"]["proof-source"]
    assert source_name in html
    assert "SECRET_PROOF" not in html and '<script>alert("x")</script>' not in html
    assert str(submission) not in html and str(c.repo) not in html


def test_legacy_saved_run_without_proof_campaign_remains_readable(campaign):
    c = campaign
    record = {"schema_version": 2, "kind": "project-verification", "run_id": "a" * 32,
              "revision": c.base, "base": c.base, "contract_digest": c.project.fingerprint,
              "runner": "host", "status": "pass", "claims": [{"id": "claim", "statement": "Old claim"}],
              "model_checks": [{"id": "model", "claim": "claim", "kind": "z3", "status": "pass", "detail": "ok"}],
              "cases": [], "behavior_skipped": True, "reproduce": "mentat verify"}
    evidence.save_run(c.state, record, {})
    _, restored, _ = evidence.read_run_snapshot(c.state, record["run_id"])
    assert restored["run_id"] == record["run_id"] and "proof_campaigns" not in restored


@pytest.mark.parametrize("campaign", ["output-budget"], indirect=True)
def test_proof_and_behavior_share_one_output_budget(campaign, monkeypatch):
    from mentat import runner

    c = campaign
    c.log_data["checker.log"] = b"L" * 300
    ident, _ = c.prepare(targets=["first", "second"])
    submission = c.submission("budget-submission", "first", "second")
    assert proof.check(c.state, ident, submission)["status"] == "pass"
    monkeypatch.setattr(runner, "MAX_RUN_OUTPUT", 512)
    verified = c.invoke("verify", "--rev", c.base, *c.scope, "--proof-campaign", ident, "--json")
    assert verified.exit_code == 0, (verified.output, verified.exception)
    payload = json.loads(verified.output)
    bundle, record, _ = evidence.read_run_snapshot(c.state, payload["run_id"])
    kept = {name: (bundle / name).read_bytes() for name in record["artifacts"] if name != "report.md"}
    assert sum(map(len, kept.values())) <= 512
    for model in record["model_checks"]:
        source_name = model["artifacts"]["proof-source"]
        assert kept[source_name] == (submission / model["id"] / "Solution.lean").read_bytes()
    assert sum(data == b"L" * 300 for data in kept.values()) == 1
    assert any(model.get("artifact_omissions") for model in record["model_checks"])
    behavior = record["cases"][0]["checks"][0]
    assert behavior["status"] == "pass" and kept[behavior["stdout"]] == b"B" * 120 + b"\n"


def test_optional_proof_diagnostics_are_limited_to_one_mib(campaign):
    c = campaign
    c.log_data["checker.log"] = b"D" * 600_000
    ident, _ = c.prepare(targets=["first", "second"])
    submission = c.submission("large-logs", "first", "second")
    assert proof.check(c.state, ident, submission)["status"] == "pass"
    verified = c.invoke("verify", "--rev", c.base, *c.scope, "--proof-campaign", ident, "--json")
    assert verified.exit_code == 0, (verified.output, verified.exception)
    payload = json.loads(verified.output)
    bundle, record, _ = evidence.read_run_snapshot(c.state, payload["run_id"])
    kept = [(bundle / name).read_bytes() for name in record["artifacts"] if name != "report.md"]
    assert sum(len(data) for data in kept if data == b"D" * 600_000) <= 1024 * 1024
    assert sum(data == b"D" * 600_000 for data in kept) == 1
    assert any(model.get("artifact_omissions") for model in record["model_checks"])
    assert all(model["status"] == "pass" for model in record["model_checks"])


def test_source_exceeding_run_budget_makes_formal_incomplete_and_skips_behavior(campaign, monkeypatch):
    from mentat import runner

    c = campaign
    ident, _ = c.prepare(targets=["first", "second"])
    submission = c.submission("large-source", "first", "second")
    (submission / "first" / "Solution.lean").write_bytes(b"x" * 129)
    assert proof.check(c.state, ident, submission)["status"] == "pass"
    monkeypatch.setattr(runner, "MAX_RUN_OUTPUT", 128)
    verified = c.invoke("verify", "--rev", c.base, *c.scope, "--proof-campaign", ident, "--json")
    assert verified.exit_code == 2, (verified.output, verified.exception)
    payload = json.loads(verified.output)
    assert payload["status"] == "incomplete" and payload["behavior_skipped"] is True
    assert payload["cases"] == []
    first = next(model for model in payload["model_checks"] if model["id"] == "first")
    assert first["status"] == "incomplete" and "proof-source" not in first.get("artifacts", {})
    bundle, record, _ = evidence.read_run_snapshot(c.state, payload["run_id"])
    assert sum((bundle / name).stat().st_size for name in record["artifacts"] if name != "report.md") <= 128


def test_large_campaign_ledger_remains_bounded_and_resumes_legacy_prefix(tmp_path):
    path = tmp_path / "campaign"
    (path / "entries").mkdir(parents=True)
    (path / "submissions").mkdir()
    ledger = []
    previous = "0" * 64
    for target in range(128):
        for round_number in range(2):
            attempt = f"{target * 2 + round_number + 1:032x}"
            source = f"-- proof {target} {round_number}\n".encode()
            (path / "submissions" / f"{attempt}.lean").write_bytes(source)
            for body in (
                {"kind": "reserve", "attempt": attempt, "target": f"target{target}",
                 "source_sha256": hashlib.sha256(source).hexdigest(), "recheck": False, "denied": False},
                {"kind": "complete", "attempt": attempt, "target": f"target{target}",
                 "status": "accepted", "detail": "x" * 4096, "duration_seconds": 0.001, "logs": {}},
            ):
                item = {**body, "previous": previous}
                raw = evidence.json_bytes(item)
                previous = hashlib.sha256(raw).hexdigest()
                (path / "entries" / f"{previous}.json").write_bytes(raw)
                ledger.append(item)
    assert sum(file.stat().st_size for file in (path / "entries").iterdir()) > 1_000_000
    (path / "ledger.json").write_bytes(evidence.json_bytes({"length": len(ledger), "head": previous}))
    reopened = proof._ledger(path)
    assert reopened == ledger and len(reopened) == 512
    assert proof._usage(reopened)[2] == pytest.approx(0.256)
    (path / "ledger.json").write_bytes(evidence.json_bytes(ledger[:2]))
    assert proof._ledger(path) == ledger
    attempt = "f" * 32
    source = b"-- resumed proof\n"
    (path / "submissions" / f"{attempt}.lean").write_bytes(source)
    proof._append(path, reopened, {"kind": "reserve", "attempt": attempt, "target": "resumed",
                                  "source_sha256": hashlib.sha256(source).hexdigest(), "recheck": False, "denied": False})
    proof._append(path, reopened, {"kind": "complete", "attempt": attempt, "target": "resumed",
                                  "status": "accepted", "detail": "ok", "duration_seconds": 0.001, "logs": {}})
    assert len(proof._ledger(path)) == 514
    assert (path / "ledger.json").stat().st_size < 512


def test_task_directory_appears_only_after_complete_publication(campaign, tmp_path, monkeypatch):
    c = campaign
    output = tmp_path / "atomic-task"
    original_write = evidence.write_record
    original_publish = proof._publish_directory
    observed = []

    def track_write(path, data):
        if any(part.startswith(".mentat-tasks-") for part in path.parts):
            assert not output.exists() and not output.is_symlink()
            observed.append(path.name)
        return original_write(path, data)

    def inspect_publication(source, destination):
        assert destination == output and not output.exists()
        assert {str(item.relative_to(source)) for item in source.rglob("*") if item.is_file()} == {
            "TASK.md", "manifest.json", "first/Solution.lean", "second/Solution.lean"
        }
        result = original_publish(source, destination)
        assert {str(item.relative_to(output)) for item in output.rglob("*") if item.is_file()} == {
            "TASK.md", "manifest.json", "first/Solution.lean", "second/Solution.lean"
        }
        return result

    monkeypatch.setattr(evidence, "write_record", track_write)
    monkeypatch.setattr(proof, "_publish_directory", inspect_publication)
    result = proof.prepare(c.state, c.project, c.candidate, c.approval, output, targets=["first", "second"])
    assert result["status"] == "pass" and observed.count("Solution.lean") == 2


@pytest.mark.parametrize("kind", ["empty-directory", "symlink"])
def test_prepare_preserves_preexisting_task_destination(campaign, tmp_path, kind):
    c = campaign
    output = tmp_path / "occupied-task"
    if kind == "empty-directory":
        output.mkdir()
        before = output.stat().st_ino
    else:
        target = tmp_path / "outside-target"
        target.mkdir()
        (target / "keep").write_text("unchanged")
        output.symlink_to(target, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        proof.prepare(c.state, c.project, c.candidate, c.approval, output, targets=["first"])
    if kind == "empty-directory":
        assert output.is_dir() and output.stat().st_ino == before and list(output.iterdir()) == []
    else:
        assert output.is_symlink() and (target / "keep").read_text() == "unchanged"
    assert not list((c.state / "proof-campaigns").iterdir())
    assert not list((c.state / "proof-campaign-receipts").iterdir())


def test_prepare_race_does_not_replace_new_empty_destination(campaign, tmp_path, monkeypatch):
    c = campaign
    output = tmp_path / "raced-task"
    original = proof._publish_directory
    created_inode = None

    def race(source, destination):
        nonlocal created_inode
        destination.mkdir()
        created_inode = destination.stat().st_ino
        return original(source, destination)

    monkeypatch.setattr(proof, "_publish_directory", race)
    with pytest.raises(OSError):
        proof.prepare(c.state, c.project, c.candidate, c.approval, output, targets=["first"])
    assert output.is_dir() and output.stat().st_ino == created_inode and list(output.iterdir()) == []
    assert not list((c.state / "proof-campaigns").iterdir())
    assert not list((c.state / "proof-campaign-receipts").iterdir())
    assert not list(tmp_path.glob(".mentat-tasks-*"))


@pytest.mark.parametrize("failure", ["state-publication", "task-publication"])
def test_prepare_failure_cleans_only_its_owned_state(campaign, tmp_path, monkeypatch, failure):
    c = campaign
    existing_id, _ = c.prepare("existing-task", targets=["second"])
    existing_manifest = (c.state / "proof-campaigns" / existing_id / "manifest.json").read_bytes()
    existing_receipt = (c.state / "proof-campaign-receipts" / existing_id).read_bytes()
    output = tmp_path / "failed-task"
    sentinel = tmp_path / "untouched"
    sentinel.write_text("preserve")
    if failure == "state-publication":
        original = Path.rename

        def interrupted(source, destination):
            if source.name == "campaign" and destination.parent.name == "proof-campaigns":
                raise OSError("synthetic state publication failure")
            return original(source, destination)

        monkeypatch.setattr(Path, "rename", interrupted)
    else:
        def interrupted(*args):
            raise OSError("synthetic task publication failure")

        monkeypatch.setattr(proof, "_publish_directory", interrupted)
    with pytest.raises(OSError, match="publication failure"):
        proof.prepare(c.state, c.project, c.candidate, c.approval, output, targets=["first"])
    assert not output.exists() and sentinel.read_text() == "preserve"
    assert [item.name for item in (c.state / "proof-campaigns").iterdir()] == [existing_id]
    assert [item.name for item in (c.state / "proof-campaign-receipts").iterdir()] == [existing_id]
    assert (c.state / "proof-campaigns" / existing_id / "manifest.json").read_bytes() == existing_manifest
    assert (c.state / "proof-campaign-receipts" / existing_id).read_bytes() == existing_receipt
    assert not list(tmp_path.glob(".mentat-tasks-*"))
