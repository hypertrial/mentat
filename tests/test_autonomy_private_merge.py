"""Real Git transactions with mocked GitHub metadata; no remote service writes."""

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentat import autonomy_private_merge as private
from mentat import autonomy_runtime as runtime
from mentat.autonomy import default_config, state_path, validate_config
from mentat.autonomy_policy import check_policy, load_policy_path, pin_policy
from mentat.autonomy_state import Store

NAME = "org/private"
URL = "git@github.com:org/private.git"


def run(repo: Path, *argv: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *argv], text=True, stderr=subprocess.PIPE
    ).strip()


class Remote:
    def __init__(self, root: Path, monkeypatch):
        self.repo, self.bare = root / "repo", root / "remote.git"
        self.repo.mkdir()
        run(self.repo, "init", "-q", "-b", "main")
        run(self.repo, "config", "user.name", "Fixture")
        run(self.repo, "config", "user.email", "fixture@example.test")
        (self.repo / "app.py").write_text("value = 1\n")
        run(self.repo, "add", ".")
        run(self.repo, "commit", "-qm", "base")
        self.base = run(self.repo, "rev-parse", "HEAD")
        run(self.repo, "checkout", "-qb", "candidate")
        (self.repo / "app.py").write_text("value = 2\n")
        run(self.repo, "commit", "-qam", "candidate")
        self.head = run(self.repo, "rev-parse", "HEAD")
        self.tree = run(self.repo, "rev-parse", "HEAD^{tree}")
        self.store = Store(root / "state" / "state.db")
        self.sid = self.store.new_session(
            str(self.repo), self.base, self.head, {}, now=0, deadline=7200
        )
        self.branch = "mentat/" + self.sid[:12] + "/" + self.head[:12]
        run(self.repo, "branch", "-m", self.branch)
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        run(self.repo, "remote", "add", "origin", str(self.bare))
        run(self.repo, "push", "-q", "origin", "main", self.branch)
        self.policy = {
            "mode": "controller-private",
            "repository_id": 101,
            "repository_node_id": "R_fixture",
            "owner_id": 42,
            "owner_node_id": "O_fixture",
            "repository": NAME,
            "host": "github.com",
            "visibility": "private",
            "remote": "origin",
            "base_branch": "main",
        }
        self.authority = private._digest(self.policy)
        self.tools = {"fixture": "trusted"}
        self.store.append_event(self.sid, "authority_pinned", {"digest": self.authority})
        self.store.append_event(self.sid, "toolchain_pinned", {"identity": self.tools})
        self.job_id = self.store.enqueue_job(
            self.sid,
            "ivl",
            "verifier",
            "fixture",
            "fixture",
            {"kind": "final_checks", "target_head": self.head},
        )
        claimed = self.store.claim_job(self.sid, "fixture", now=1)
        assert claimed is not None
        self.store.finish_job(self.job_id, claimed["lease_token"], {"status": "succeeded"}, now=2)
        self.store.append_event(
            self.sid,
            "publication",
            {
                "head": self.head,
                "base": self.base,
                "result": {"status": "succeeded", "url": f"https://github.com/{NAME}/pull/7"},
            },
        )
        self.calls, self.pushes, self.preflights = [], [], []
        self.race = None
        self.cancel_on_prepare = False
        self.late_metadata = None
        self.closed_without_merge = False
        self.push_permission = True
        self.active = True
        self.guard_calls = 0
        self.reject_guard_after = None
        self.expire_guard_after = None
        self.clock_now = 3
        self.unknown_after_push = False
        self.lost_push_reply = False
        self.lag_after_push = 0
        self.visibility = True
        self.api_override = None
        self.original_git = runtime.git
        monkeypatch.setattr(runtime, "git", self.git)
        monkeypatch.setattr(runtime, "_gh", self.gh)
        monkeypatch.setattr(private.time, "sleep", lambda _: None)

    def ref(self, name):
        return run(self.bare, "rev-parse", "refs/heads/" + name)

    def git(self, repo, *args):
        # GitHub metadata/SSH identity are mocked. Actual push and both-ref CAS
        # execute against the real disposable receive-pack, using file transport.
        if repo == self.repo and args == ("remote", "get-url", "origin"):
            return URL
        if args[0] == "push" and "--dry-run" in args:
            self.preflights.append(args)
        if args[0] == "push" and "--dry-run" not in args:
            self.pushes.append(args)
            if self.late_metadata:
                self.late_metadata()
            if self.race:
                target, oid = self.race
                run(self.bare, "update-ref", "refs/heads/" + target, oid)
        if args[0] in {"fetch", "ls-remote", "push"}:
            args = tuple(str(self.bare) if a == URL else a for a in args)
        value = (
            run(repo, *args)
            if repo == self.repo and args[0] in {"fetch", "ls-remote", "push"}
            else self.original_git(repo, *args)
        )
        if args[0] == "push" and "--dry-run" not in args and self.lost_push_reply:
            raise RuntimeError("lost actual push acknowledgement")
        if "commit-tree" in args and self.cancel_on_prepare:
            self.active = False
        return value

    def pr(self):
        head, base = self.ref(self.branch), self.ref("main")
        merged = head == base and head != self.head
        return {
            "number": 7,
            "id": 707,
            "node_id": "PR_fixture",
            "html_url": f"https://github.com/{NAME}/pull/7",
            "state": "closed" if merged or self.closed_without_merge else "open",
            "merged": merged and not self.closed_without_merge,
            "head": {
                "sha": head,
                "ref": self.branch,
                "repo": {"full_name": NAME, "id": 101, "node_id": "R_fixture"},
            },
            "base": {
                "sha": base,
                "ref": "main",
                "repo": {"full_name": NAME, "id": 101, "node_id": "R_fixture"},
            },
            "merge_commit_sha": head if merged and not self.closed_without_merge else None,
        }

    def gh(self, repo, *args):
        self.calls.append(args)
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": NAME})
        if args[:2] == ("pr", "list"):
            pr = self.pr()
            return json.dumps(
                [
                    {
                        "number": 7,
                        "url": pr["html_url"],
                        "headRefName": self.branch,
                        "headRefOid": pr["head"]["sha"],
                        "baseRefName": "main",
                        "baseRefOid": pr["base"]["sha"],
                        "state": "MERGED" if pr["merged"] else "OPEN",
                        "isCrossRepository": False,
                    }
                ]
            )
        assert args[0] == "api" and args[1:3] == ("--hostname", "github.com")
        path = args[-1]
        assert not any(
            k in path for k in ("protection", "rulesets", "billing", "check-runs", "/status")
        )
        if self.api_override:
            replacement = self.api_override(path)
            if replacement is not None:
                return json.dumps(replacement)
        if self.unknown_after_push and self.pushes:
            raise RuntimeError("lost response after the atomic push")
        if path == f"repos/{NAME}":
            return json.dumps(
                {
                    "full_name": NAME,
                    "id": 101,
                    "node_id": "R_fixture",
                    "owner": {"id": 42, "node_id": "O_fixture", "login": "org"},
                    "private": self.visibility,
                    "html_url": f"https://github.com/{NAME}",
                    "default_branch": "main",
                    "archived": False,
                    "permissions": {"push": self.push_permission},
                }
            )
        if path.endswith("/files?per_page=100"):
            return '[{"filename":"app.py"}]'
        if path == f"repos/{NAME}/pulls/7":
            pr = self.pr()
            if self.pushes and self.lag_after_push:
                self.lag_after_push -= 1
                pr.update(state="open", merged=False, merge_commit_sha=None)
                pr["head"]["sha"], pr["base"]["sha"] = self.head, self.base
            return json.dumps(pr)
        if "/commits/" in path:
            oid = path.rsplit("/", 1)[1]
            return json.dumps(
                {
                    "sha": oid,
                    "parents": [
                        {"sha": p} for p in run(self.bare, "show", "-s", "--format=%P", oid).split()
                    ],
                    "commit": {"tree": {"sha": run(self.bare, "rev-parse", oid + "^{tree}")}},
                }
            )
        raise AssertionError(path)

    def gateway(self, reviews=None):
        gateway = runtime.GitHub([["verify"]], merge_policy=self.policy)
        actual_reviews = self.evidence()[1] if reviews is None else reviews

        def guard(job, evidence):
            self.guard_calls += 1
            if self.expire_guard_after is not None and self.guard_calls > self.expire_guard_after:
                self.clock_now = 7200
            return self.reject_guard_after is None or self.guard_calls <= self.reject_guard_after

        controller = SimpleNamespace(
            github=gateway,
            store=self.store,
            session_id=self.sid,
            authority_digest=self.authority,
            tool_identity=self.tools,
            clock=lambda: self.clock_now,
            _publication_guard=guard,
        )
        private.bind_gateway(
            controller,
            {
                "id": self.job_id,
                "payload": {"proposal": {"head": self.head}, "reviews": actual_reviews},
            },
            {"id": "fixture", "check_key": "fixture"},
        )
        return gateway

    def evidence(self):
        diff = runtime.gitio.run(
            self.repo, "diff", "--binary", "--no-ext-diff", self.base, self.head, limit=16_000_000
        )
        checks = [
            {
                "head": self.head,
                "base": self.base,
                "status": "passed",
                "command": ["verify"],
                "tools": {"fixture": "pinned"},
                "returncode": 0,
            }
        ]
        reviews = [
            {
                "head": self.head,
                "base": self.base,
                "status": "passed",
                "author_id": "generator",
                "reviewer_id": "independent",
                "diff_digest": hashlib.sha256(diff).hexdigest(),
            }
        ]
        return checks, reviews

    def merge(self, checks=None, reviews=None):
        good_checks, good_reviews = self.evidence()
        if not any(e["kind"] == "merge_started" for e in self.store.snapshot(self.sid)["events"]):
            self.store.begin_remote_effect(
                self.sid,
                "merge_started",
                {"head": self.head, "base": self.base},
                job_id=self.job_id,
            )
        return self.gateway(reviews).merge(
            self.repo,
            7,
            self.head,
            self.base,
            checks=good_checks if checks is None else checks,
            reviews=good_reviews if reviews is None else reviews,
            active=lambda: self.active,
        )


@pytest.fixture
def remote(tmp_path, monkeypatch):
    return Remote(tmp_path, monkeypatch)


def test_exact_tree_two_parent_merge_and_reconcile(remote):
    result = remote.merge()
    assert result["status"] == "succeeded", result
    oid = remote.ref("main")
    assert oid == remote.ref(remote.branch) == result["derived_pr_head"]
    assert run(remote.bare, "show", "-s", "--format=%P", oid).split() == [remote.base, remote.head]
    assert run(remote.bare, "rev-parse", oid + "^{tree}") == remote.tree
    assert run(remote.bare, "for-each-ref", "--format=%(refname)").splitlines() == [
        "refs/heads/main",
        "refs/heads/" + remote.branch,
    ]
    assert len(remote.pushes) == 1 and "--atomic" in remote.pushes[0]
    assert "--force-with-lease=refs/heads/main:" + remote.base in remote.pushes[0]
    assert "--force-with-lease=refs/heads/" + remote.branch + ":" + remote.head in remote.pushes[0]
    assert not any(a in {"--force", "--delete", "--admin"} for a in remote.pushes[0])
    before = remote.ref("main")
    reconciled = remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)
    assert reconciled["status"] == "found" and reconciled["state"] == "merged"
    assert remote.ref("main") == before and len(remote.pushes) == 1


@pytest.mark.parametrize("which", ["base", "head"])
def test_real_atomic_ref_race_rejects_all_controller_updates(remote, which):
    target = "main" if which == "base" else remote.branch
    # A concurrent independent fast-forward arrives after the last preflight.
    oid = run(remote.repo, "commit-tree", remote.tree, "-p", remote.ref(target), "-m", "concurrent")
    run(remote.repo, "push", "-q", "origin", oid + ":refs/heads/race-object")
    remote.race = target, oid
    result = remote.merge()
    assert result["status"] == "incomplete"
    assert remote.ref(target) == oid
    other = remote.branch if which == "base" else "main"
    assert remote.ref(other) == (remote.head if which == "base" else remote.base)
    assert len(remote.pushes) == 1


def test_server_without_atomic_support_never_falls_back(remote):
    run(remote.bare, "config", "receive.advertiseAtomic", "false")
    result = remote.merge()
    assert result["status"] == "incomplete"
    assert remote.ref("main") == remote.base and remote.ref(remote.branch) == remote.head
    assert len(remote.pushes) == 0 and len(remote.preflights) == 1


@pytest.mark.parametrize(
    "bad",
    ["checks", "review", "author", "duplicate", "inactive", "public", "cross-repo", "head", "base"],
)
def test_invalid_authority_never_pushes(remote, bad):
    checks, reviews = remote.evidence()
    if bad == "checks":
        checks = []
    elif bad == "review":
        reviews[0]["head"] = "f" * 40
    elif bad == "author":
        reviews[0]["reviewer_id"] = "generator"
    elif bad == "duplicate":
        checks *= 2
    elif bad == "inactive":
        remote.active = False
    elif bad == "public":
        remote.visibility = False
    else:

        def override(path):
            if path != f"repos/{NAME}/pulls/7":
                return None
            pr = remote.pr()
            if bad == "cross-repo":
                pr["head"]["repo"]["full_name"] = "other/repo"
            elif bad == "head":
                pr["head"]["sha"] = "f" * 40
            elif bad == "base":
                pr["base"]["sha"] = "f" * 40
            return pr

        remote.api_override = override
    assert remote.merge(checks, reviews)["status"] == "incomplete"
    assert not remote.pushes
    assert remote.ref("main") == remote.base


def test_cancellation_after_preparation_prevents_dispatch(remote):
    remote.cancel_on_prepare = True
    assert remote.merge()["status"] == "incomplete"
    assert not remote.pushes
    assert remote.ref("main") == remote.base


@pytest.mark.parametrize("lost", ["push", "confirmation"])
def test_lost_push_response_is_reconciled_without_repeating_write(remote, lost):
    remote.unknown_after_push = lost == "confirmation"
    remote.lost_push_reply = lost == "push"
    assert remote.merge()["status"] == "incomplete"
    oid = remote.ref("main")
    assert oid == remote.ref(remote.branch) and oid != remote.base
    remote.unknown_after_push = False
    remote.lost_push_reply = False
    outcome = remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)
    assert outcome["state"] == "merged" and outcome["merge_commit"] == oid
    assert len(remote.pushes) == 1


@pytest.mark.parametrize("change", ["tree", "parents", "head", "visibility"])
def test_reconciliation_refuses_changed_binding(remote, change):
    assert remote.merge()["status"] == "succeeded"
    if change == "visibility":
        remote.visibility = False
    else:

        def override(path):
            if change == "head" and path.endswith("/pulls/7"):
                pr = remote.pr()
                pr["head"]["sha"] = "f" * 40
                return pr
            if "/commits/" in path:
                oid = path.rsplit("/", 1)[1]
                return {
                    "sha": oid,
                    "parents": [{"sha": remote.head}, {"sha": remote.base}]
                    if change == "parents"
                    else [{"sha": remote.base}, {"sha": remote.head}],
                    "commit": {"tree": {"sha": "f" * 40 if change == "tree" else remote.tree}},
                }
            return None

        remote.api_override = override
    assert (
        remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)["status"]
        == "incomplete"
    )
    assert len(remote.pushes) == 1


def test_private_policy_is_explicit_and_pinned_without_workspace_schema_change(remote, tmp_path):
    for name in ("verify.py", "titanic.csv"):
        (remote.repo / name).touch()
    config = default_config(remote.repo)
    assert validate_config(config) == config and len(config) == 14
    state = tmp_path / "state"
    policy = pin_policy(remote.repo, state, config, merge_policy="controller-private")
    assert policy["schema_version"] == 3 and policy["merge_policy"] == remote.policy
    path = state_path(remote.repo, state)
    raw = path.with_name("policy.json").read_bytes()
    assert load_policy_path(path) == policy
    assert pin_policy(remote.repo, state, config, merge_policy="controller-private") == policy
    with pytest.raises(ValueError, match="merge authority changed"):
        pin_policy(remote.repo, state, config)
    assert path.with_name("policy.json").read_bytes() == raw
    narrowed = {**config, "auto_merge": False}
    check_policy(narrowed, policy)
    for bad in ({**policy, "schema_version": 2}, {**policy, "merge_policy": None}):
        with pytest.raises(ValueError):
            check_policy(config, bad)
    assert not remote.pushes


def test_private_policy_cannot_rewrite_existing_protected_policy(remote, tmp_path):
    (remote.repo / "verify.py").touch()
    (remote.repo / "titanic.csv").touch()
    config = default_config(remote.repo)
    policy = pin_policy(remote.repo, tmp_path / "state", config)
    assert policy["schema_version"] == 2 and "merge_policy" not in policy
    with pytest.raises(ValueError, match="merge authority changed"):
        pin_policy(remote.repo, tmp_path / "state", config, merge_policy="controller-private")
    assert load_policy_path(state_path(remote.repo, tmp_path / "state")) == policy
    assert not remote.pushes


@pytest.mark.parametrize(
    "key,value",
    [
        ("repository", "../private"),
        ("visibility", "public"),
        ("mode", "fallback"),
        ("host", "other.example"),
    ],
)
def test_malformed_or_expanded_private_policy_rejected(key, value):
    value = {
        "mode": "controller-private",
        "repository_id": 101,
        "repository_node_id": "R_fixture",
        "owner_id": 42,
        "owner_node_id": "O_fixture",
        "repository": NAME,
        "host": "github.com",
        "visibility": "private",
        "remote": "origin",
        "base_branch": "main",
        key: value,
    }
    with pytest.raises(ValueError):
        runtime.GitHub([["verify"]], merge_policy=value)


@pytest.mark.parametrize("lag", [2, 100])
def test_lagging_github_state_is_bounded_and_never_repeats_push(remote, lag):
    remote.lag_after_push = lag
    result = remote.merge()
    assert result["status"] == ("succeeded" if lag == 2 else "incomplete")
    assert remote.ref("main") == remote.ref(remote.branch)
    assert len(remote.pushes) == 1
    remote.lag_after_push = 0
    assert (
        remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)["state"]
        == "merged"
    )
    assert len(remote.pushes) == 1


def test_sensitive_changes_still_need_two_distinct_reviewers(remote):
    remote.api_override = lambda path: (
        [{"filename": ".github/workflows/check.yml"}]
        if path.endswith("/files?per_page=100")
        else None
    )
    checks, reviews = remote.evidence()
    assert remote.merge(checks, reviews)["status"] == "incomplete"
    assert not remote.pushes
    reviews.append({**reviews[0], "reviewer_id": "second-independent"})
    assert remote.merge(checks, reviews)["status"] == "succeeded"


def test_default_mode_does_not_downgrade_on_protection_api_failure(remote, monkeypatch):
    original = remote.gh

    def gh(repo, *args):
        if args[-1].endswith("/protection"):
            raise RuntimeError("HTTP403 unavailable on this plan")
        return original(repo, *args)

    monkeypatch.setattr(runtime, "_gh", gh)
    checks, reviews = remote.evidence()
    result = runtime.GitHub([["verify"]]).merge(
        remote.repo, 7, remote.head, remote.base, checks=checks, reviews=reviews, active=True
    )
    assert result["status"] == "incomplete" and not remote.pushes


def test_cli_init_explicit_private_policy(remote, tmp_path):
    from typer.testing import CliRunner

    from mentat.cli import app

    (remote.repo / "verify.py").touch()
    (remote.repo / "titanic.csv").touch()
    result = CliRunner().invoke(
        app,
        [
            "--state",
            str(tmp_path / "state"),
            "init",
            "--repo",
            str(remote.repo),
            "--merge-policy",
            "controller-private",
        ],
    )
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["local_policy"]["merge_policy"] == remote.policy
    assert value["local_policy"]["schema_version"] == 3
    assert not remote.pushes


@pytest.mark.parametrize("change", ["closed", "visibility", "permissions"])
def test_late_metadata_change_is_not_claimed_atomic_or_successful(remote, change):
    def late():
        if change == "closed":
            remote.closed_without_merge = True
        elif change == "visibility":
            remote.visibility = False
        else:
            remote.push_permission = False

    remote.late_metadata = late
    result = remote.merge()
    assert result["status"] == "incomplete"
    assert remote.ref("main") == remote.ref(remote.branch) != remote.base
    assert len(remote.pushes) == 1
    records = [
        e
        for e in remote.store.snapshot(remote.sid)["events"]
        if e["kind"] == "private_merge_prepared"
    ]
    assert len(records) == 1 and records[0]["payload"]["intent"]["merge_commit"] == remote.ref(
        "main"
    )


@pytest.mark.parametrize(
    "key",
    [
        "url.https://elsewhere.test/.insteadOf",
        "url.https://elsewhere.test/.pushInsteadOf",
        "core.sshCommand",
        "include.path",
        "remote.origin.pushurl",
        "remote.origin.receivepack",
        "push.followTags",
    ],
)
def test_local_transport_overrides_rejected_before_network_write(remote, key):
    value = "true" if key == "push.followTags" else "/not-used"
    run(remote.repo, "config", key, value)
    assert remote.merge()["status"] == "incomplete"
    assert not remote.pushes and remote.ref("main") == remote.base


def test_immutable_repository_identity_change_blocks_same_name(remote):
    def override(path):
        if path == f"repos/{NAME}":
            return {
                "full_name": NAME,
                "id": 999,
                "node_id": "R_other",
                "owner": {"id": 42, "node_id": "O_fixture", "login": "org"},
                "private": True,
                "html_url": f"https://github.com/{NAME}",
                "default_branch": "main",
                "archived": False,
                "permissions": {"push": True},
            }
        return None

    remote.api_override = override
    assert remote.merge()["status"] == "incomplete" and not remote.pushes


def test_exact_intent_rejects_same_shape_spoofed_commit(remote):
    assert remote.merge()["status"] == "succeeded"
    intended = remote.ref("main")
    spoof = run(
        remote.repo,
        "commit-tree",
        remote.tree,
        "-p",
        remote.base,
        "-p",
        remote.head,
        "-m",
        "other actor same tree and parents",
    )
    run(remote.repo, "push", "-q", "origin", spoof + ":refs/heads/spoof-object")
    run(remote.bare, "update-ref", "refs/heads/main", spoof)
    run(remote.bare, "update-ref", "refs/heads/" + remote.branch, spoof)
    assert spoof != intended
    assert (
        remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)["status"]
        == "incomplete"
    )
    assert len(remote.pushes) == 1


def test_prepared_intent_restores_exact_commit_after_local_object_loss(remote):
    run(remote.bare, "config", "receive.advertiseAtomic", "false")
    assert remote.merge()["status"] == "incomplete"
    row = remote.store.private_merge_intent(
        remote.sid, remote.head, remote.base, remote.branch, remote.authority
    )
    assert row is not None
    oid = row["intent"]["merge_commit"]
    (remote.repo / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    run(remote.bare, "config", "receive.advertiseAtomic", "true")
    assert remote.merge()["status"] == "succeeded"
    assert remote.ref("main") == oid
    assert (
        len(
            [
                e
                for e in remote.store.snapshot(remote.sid)["events"]
                if e["kind"] == "private_merge_prepared"
            ]
        )
        == 1
    )


def test_derived_commit_does_not_require_global_git_author_configuration(remote):
    run(remote.repo, "config", "--unset", "user.name")
    run(remote.repo, "config", "--unset", "user.email")
    assert remote.merge()["status"] == "succeeded"
    assert (
        run(remote.bare, "show", "-s", "--format=%an <%ae>", remote.ref("main"))
        == "Mentat local controller <mentat@localhost>"
    )


def test_reconciliation_accepts_original_approved_head_metadata(remote):
    assert remote.merge()["status"] == "succeeded"

    def override(path):
        if path.endswith("/pulls/7"):
            pr = remote.pr()
            pr["head"]["sha"] = remote.head
            return pr
        return None

    remote.api_override = override
    assert (
        remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)["state"]
        == "merged"
    )


def test_private_gateway_without_durable_authority_cannot_merge(remote):
    checks, reviews = remote.evidence()
    gateway = runtime.GitHub([["verify"]], merge_policy=remote.policy)
    assert (
        gateway.merge(
            remote.repo, 7, remote.head, remote.base, checks=checks, reviews=reviews, active=True
        )["status"]
        == "incomplete"
    )
    assert not remote.pushes


@pytest.mark.parametrize("lost", ["guard", "deadline"])
def test_authorization_revalidated_after_intent_and_before_write(remote, lost):
    if lost == "guard":
        remote.reject_guard_after = 1
    else:
        remote.expire_guard_after = 1
    assert remote.merge()["status"] == "incomplete"
    assert not remote.pushes and remote.ref("main") == remote.base
    assert remote.guard_calls == 2
    assert (
        len(
            [
                e
                for e in remote.store.snapshot(remote.sid)["events"]
                if e["kind"] == "private_merge_prepared"
            ]
        )
        == 1
    )


def test_closed_unmerged_pr_is_never_dispatched(remote):
    remote.closed_without_merge = True
    assert remote.merge()["status"] == "incomplete"
    assert not remote.pushes and remote.ref("main") == remote.base
    assert (
        remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)["status"]
        == "incomplete"
    )


def test_independently_merged_same_shape_without_intent_is_not_adopted(remote):
    oid = run(
        remote.repo,
        "commit-tree",
        remote.tree,
        "-p",
        remote.base,
        "-p",
        remote.head,
        "-m",
        "unrecorded same-shape merge",
    )
    run(
        remote.repo,
        "push",
        "-q",
        "origin",
        oid + ":refs/heads/main",
        oid + ":refs/heads/" + remote.branch,
    )
    assert (
        remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)["status"]
        == "incomplete"
    )
    assert not remote.pushes


@pytest.mark.parametrize("field", ["repository_node_id", "owner_id", "owner_node_id"])
def test_other_immutable_identity_components_are_checked(remote, field):
    if field == "owner_id":
        remote.policy[field] = 999
    else:
        remote.policy[field] += "changed"
    assert remote.merge()["status"] == "incomplete" and not remote.pushes


def test_runtime_git_neutralizes_inherited_git_transport_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_SSH_COMMAND", "malicious-command")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "url.attacker.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "git@github.com:")
    captures = []

    def fake(argv, **kwargs):
        captures.append(kwargs["env"])
        return (1, b"", b"") if "config" in argv else (0, b"", b"")

    monkeypatch.setattr(runtime.process, "run", fake)
    runtime.git(tmp_path, "ls-remote", URL)
    assert len(captures) == 2
    for env in captures:
        assert not any(
            k in env
            for k in (
                "GIT_SSH_COMMAND",
                "GIT_CONFIG_COUNT",
                "GIT_CONFIG_KEY_0",
                "GIT_CONFIG_VALUE_0",
            )
        )
        assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert env["GIT_ALLOW_PROTOCOL"] == "ssh"


def test_worktree_local_transport_override_is_rejected(remote):
    run(remote.repo, "config", "extensions.worktreeConfig", "true")
    run(remote.repo, "config", "--worktree", "core.sshCommand", "not-executed")
    assert remote.merge()["status"] == "incomplete"
    assert not remote.pushes


def test_dispatched_but_unchanged_remote_is_unknown_and_never_replayed(remote):
    gateway = remote.gateway()
    prepare_dispatch = gateway.private_dispatch

    def stop_after_journal(intent):
        prepare_dispatch(intent)
        raise RuntimeError("interrupted after dispatch marker before client invocation")

    gateway.private_dispatch = stop_after_journal
    checks, reviews = remote.evidence()
    remote.store.begin_remote_effect(
        remote.sid,
        "merge_started",
        {"head": remote.head, "base": remote.base},
        job_id=remote.job_id,
    )
    result = gateway.merge(
        remote.repo, 7, remote.head, remote.base, checks=checks, reviews=reviews, active=True
    )
    assert result["status"] == "incomplete" and not remote.pushes
    assert remote.ref("main") == remote.base and remote.ref(remote.branch) == remote.head
    row = remote.store.private_merge_intent(
        remote.sid, remote.head, remote.base, remote.branch, remote.authority
    )
    assert row is not None and row["dispatched"] is True
    remote.store = Store(remote.store.path)
    assert (
        remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)["status"]
        == "incomplete"
    )
    assert remote.merge()["status"] == "incomplete" and not remote.pushes
    assert len(remote.preflights) == 1


def test_replacement_pr_cannot_inherit_original_publication_identity(remote, monkeypatch):
    gateway = remote.gateway()
    dispatch = gateway.private_dispatch

    def interrupt(intent):
        dispatch(intent)
        raise RuntimeError("in-flight outcome unknown")

    gateway.private_dispatch = interrupt
    remote.store.begin_remote_effect(
        remote.sid,
        "merge_started",
        {"head": remote.head, "base": remote.base},
        job_id=remote.job_id,
    )
    checks, reviews = remote.evidence()
    assert (
        gateway.merge(
            remote.repo, 7, remote.head, remote.base, checks=checks, reviews=reviews, active=True
        )["status"]
        == "incomplete"
    )
    original = remote.gh

    def replaced(repo, *args):
        if args[:2] == ("pr", "list"):
            return json.dumps(
                [
                    {
                        "number": 8,
                        "url": f"https://github.com/{NAME}/pull/8",
                        "headRefName": remote.branch,
                        "headRefOid": remote.head,
                        "baseRefName": "main",
                        "baseRefOid": remote.base,
                        "state": "OPEN",
                        "isCrossRepository": False,
                    }
                ]
            )
        assert not args[-1].endswith("/pulls/8")
        return original(repo, *args)

    monkeypatch.setattr(runtime, "_gh", replaced)
    assert (
        remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)["status"]
        != "found"
    )
    assert not remote.pushes
    row = remote.store.private_merge_intent(
        remote.sid, remote.head, remote.base, remote.branch, remote.authority
    )
    assert row["intent"]["pr_number"] == 7 and row["intent"]["pr_id"] == 707


def test_second_dispatch_authorization_is_rejected_by_sqlite_journal(remote):
    assert remote.merge()["status"] == "succeeded"
    row = remote.store.private_merge_intent(
        remote.sid, remote.head, remote.base, remote.branch, remote.authority
    )
    payload = {k: v for k, v in row.items() if k != "dispatched"}
    with pytest.raises(ValueError, match="never repeat"):
        remote.store.begin_remote_effect(
            remote.sid, "private_merge_dispatched", payload, job_id=remote.job_id, now=4
        )
    assert len(remote.pushes) == 1


def test_private_publication_pins_github_repository_explicitly(remote, tmp_path, monkeypatch):
    original = remote.gh
    calls = []

    def gh(repo, *args):
        if args[:2] == ("pr", "create"):
            calls.append(args)
            assert args[args.index("--repo") + 1] == f"https://github.com/{NAME}"
            return f"https://github.com/{NAME}/pull/7"
        return original(repo, *args)

    monkeypatch.setattr(runtime, "_gh", gh)
    body = tmp_path / "body.md"
    body.write_text("Reviewed fixture only")
    result = remote.gateway().publish(remote.repo, remote.branch, "Reviewed fixture", body)
    assert result["status"] == "succeeded" and len(calls) == 1
    assert remote.ref("main") == remote.base


def test_unresolved_dispatch_fences_new_and_existing_sessions(remote):
    other = remote.store.new_session(
        str(remote.repo), remote.base, remote.head, {}, now=0, deadline=7200
    )
    gateway = remote.gateway()
    dispatch = gateway.private_dispatch

    def interrupted(intent):
        dispatch(intent)
        raise RuntimeError("unknown push")

    gateway.private_dispatch = interrupted
    remote.store.begin_remote_effect(
        remote.sid,
        "merge_started",
        {"head": remote.head, "base": remote.base},
        job_id=remote.job_id,
    )
    checks, reviews = remote.evidence()
    assert (
        gateway.merge(
            remote.repo, 7, remote.head, remote.base, checks=checks, reviews=reviews, active=True
        )["status"]
        == "incomplete"
    )
    reopened = Store(remote.store.path)
    with pytest.raises(ValueError, match="unresolved private"):
        reopened.new_session(str(remote.repo), remote.base, "f" * 40, {}, now=0, deadline=7200)
    record = reopened.private_merge_intent(
        remote.sid, remote.head, remote.base, remote.branch, remote.authority
    )
    assert record and record["dispatched"]
    reopened.append_event(other, "authority_pinned", {"digest": remote.authority})
    reopened.append_event(other, "toolchain_pinned", {"identity": remote.tools})
    job = reopened.enqueue_job(
        other,
        "ivl",
        "verifier",
        "other",
        "other",
        {"kind": "final_checks", "target_head": remote.head},
    )
    claimed = reopened.claim_job(other, "other", now=1)
    reopened.finish_job(job, claimed["lease_token"], {"status": "succeeded"}, now=2)
    reopened.begin_remote_effect(
        other, "merge_started", {"head": remote.head, "base": remote.base}, job_id=job
    )
    payload = {k: v for k, v in record.items() if k != "dispatched"}
    payload["source_job_id"] = job
    reopened.begin_remote_effect(other, "private_merge_prepared", payload, job_id=job, now=3)
    with pytest.raises(ValueError, match="unresolved private"):
        reopened.begin_remote_effect(other, "private_merge_dispatched", payload, job_id=job, now=3)
    assert other != remote.sid and not remote.pushes


def test_positive_exact_recovery_clears_cross_session_fence(remote):
    assert remote.merge()["status"] == "succeeded"
    reopened = Store(remote.store.path)
    new = reopened.new_session(str(remote.repo), remote.base, remote.head, {}, now=0, deadline=7200)
    assert new != remote.sid


def test_merged_metadata_with_original_branch_ref_is_not_accepted(remote):
    assert remote.merge()["status"] == "succeeded"
    # Remove resolution solely in disposable fixture to model an interrupted recovery.
    with remote.store._connect() as db:
        db.execute("DELETE FROM events WHERE kind='private_merge_resolved'")
        db.commit()
    oid = remote.ref("main")
    run(remote.bare, "update-ref", "refs/heads/" + remote.branch, remote.head)
    original = remote.pr

    def spoof():
        value = original()
        value.update(state="closed", merged=True, merge_commit_sha=oid)
        return value

    remote.pr = spoof
    assert (
        remote.gateway().reconcile(remote.repo, remote.branch, remote.head, remote.base)["status"]
        == "incomplete"
    )
    with pytest.raises(ValueError, match="unresolved private"):
        remote.store.new_session(
            str(remote.repo), remote.base, remote.head, {}, now=0, deadline=7200
        )


def test_candidate_textconv_never_runs_on_controller(remote, tmp_path):
    marker = tmp_path / "converter-ran"
    converter = tmp_path / "converter"
    converter.write_text("#!/bin/sh\ntouch '" + str(marker) + '\'\ncat "$1"\n')
    converter.chmod(0o755)
    run(remote.repo, "config", "diff.trap.textconv", str(converter))
    (remote.repo / ".gitattributes").write_text("app.py diff=trap\n")
    # Committed attributes affect the actual revision-bound diff, not just the worktree.
    run(remote.repo, "add", ".gitattributes")
    run(remote.repo, "commit", "-qm", "attributes")
    head = run(remote.repo, "rev-parse", "HEAD")
    raw = runtime.gitio.run(
        remote.repo,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        remote.base,
        head,
        limit=16_000_000,
    )
    assert b"value = 2" in raw and not marker.exists()
    checks = [
        {
            "head": head,
            "base": remote.base,
            "status": "passed",
            "command": ["verify"],
            "tools": {"fixture": "pinned"},
            "returncode": 0,
        }
    ]
    reviews = [
        {
            "head": head,
            "base": remote.base,
            "status": "passed",
            "author_id": "generator",
            "reviewer_id": "independent",
            "diff_digest": hashlib.sha256(raw).hexdigest(),
        }
    ]
    original = remote.gh

    def gh(repo, *args):
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": NAME})
        if args[:2] == ("pr", "merge"):
            return ""
        path = args[-1]
        if path.endswith("/protection"):
            return json.dumps(
                {
                    "required_status_checks": {"strict": True, "contexts": ["ci"]},
                    "enforce_admins": {"enabled": True},
                }
            )
        if "/rulesets?" in path:
            return "[]"
        if "/files?" in path:
            return json.dumps([{"filename": "app.py"}])
        if "/check-runs?" in path:
            return json.dumps(
                {
                    "total_count": 1,
                    "check_runs": [
                        {
                            "name": "ci",
                            "head_sha": head,
                            "status": "completed",
                            "conclusion": "success",
                        }
                    ],
                }
            )
        if path.endswith("/status"):
            return json.dumps({"statuses": []})
        return json.dumps(
            {"state": "open", "head": {"sha": head}, "base": {"sha": remote.base, "ref": "main"}}
        )

    runtime._gh = gh
    try:
        result = runtime.GitHub([["verify"]]).merge(
            remote.repo, 7, head, remote.base, checks=checks, reviews=reviews, active=True
        )
        assert result["status"] == "succeeded", result
        assert not marker.exists()
    finally:
        runtime._gh = original
