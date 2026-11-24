"""Real Git transactions with mocked GitHub metadata; no remote service writes."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from mentat import autonomy_private_merge as private
from mentat import autonomy_runtime as runtime
from mentat.autonomy import default_config, state_path, validate_config
from mentat.autonomy_policy import check_policy, load_policy_path, pin_policy

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
        self.branch = "mentat/012345abcdef/" + self.head[:12]
        run(self.repo, "branch", "-m", self.branch)
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        run(self.repo, "remote", "add", "origin", str(self.bare))
        run(self.repo, "push", "-q", "origin", "main", self.branch)
        self.policy = {
            "mode": "controller-private",
            "repository": NAME,
            "host": "github.com",
            "visibility": "private",
            "remote": "origin",
            "base_branch": "main",
        }
        self.calls, self.pushes = [], []
        self.race = None
        self.cancel_on_prepare = False
        self.active = True
        self.unknown_after_push = False
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
        if args[0] == "push":
            self.pushes.append(args)
            if self.race:
                target, oid = self.race
                run(self.bare, "update-ref", "refs/heads/" + target, oid)
            args = tuple(str(self.bare) if a == URL else a for a in args)
        value = (
            run(repo, *args)
            if repo == self.repo and args[0] in {"fetch", "ls-remote", "push"}
            else self.original_git(repo, *args)
        )
        if args[0] == "commit-tree" and self.cancel_on_prepare:
            self.active = False
        return value

    def pr(self):
        head, base = self.ref(self.branch), self.ref("main")
        merged = head == base and head != self.head
        return {
            "number": 7,
            "html_url": f"https://github.com/{NAME}/pull/7",
            "state": "closed" if merged else "open",
            "merged": merged,
            "head": {"sha": head, "ref": self.branch, "repo": {"full_name": NAME}},
            "base": {"sha": base, "ref": "main", "repo": {"full_name": NAME}},
            "merge_commit_sha": head if merged else None,
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
                    "private": self.visibility,
                    "html_url": f"https://github.com/{NAME}",
                    "default_branch": "main",
                    "archived": False,
                    "permissions": {"push": True},
                }
            )
        if path.endswith("/files?per_page=100"):
            return '[{"filename":"app.py"}]'
        if path == f"repos/{NAME}/pulls/7":
            return json.dumps(self.pr())
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

    def gateway(self):
        return runtime.GitHub([["verify"]], merge_policy=self.policy)

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
        return self.gateway().merge(
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
    assert len(remote.pushes) == 1


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


def test_lost_push_response_is_reconciled_without_repeating_write(remote):
    remote.unknown_after_push = True
    assert remote.merge()["status"] == "incomplete"
    oid = remote.ref("main")
    assert oid == remote.ref(remote.branch) and oid != remote.base
    remote.unknown_after_push = False
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
                pr["head"]["sha"] = remote.head
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
        "repository": NAME,
        "host": "github.com",
        "visibility": "private",
        "remote": "origin",
        "base_branch": "main",
        key: value,
    }
    with pytest.raises(ValueError):
        runtime.GitHub([["verify"]], merge_policy=value)
