"""Explicit controller enforcement for a pinned private GitHub repository.

The caller authenticates local checks, reviews and session/source authority.
These operations never select this policy as a fallback from a protected merge.
"""

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

Git = Callable[..., str]
API = Callable[[str], Any]
_FIELDS = {"mode", "repository", "host", "visibility", "remote", "base_branch"}
_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def validate_policy(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("invalid private merge policy")
    fixed = {
        "mode": "controller-private",
        "host": "github.com",
        "visibility": "private",
        "remote": "origin",
        "base_branch": "main",
    }
    if any(value.get(k) != v for k, v in fixed.items()):
        raise ValueError("invalid private merge authority")
    name = value.get("repository")
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", name)
        or ".." in name
    ):
        raise ValueError("invalid pinned repository identity")
    return dict(value)


def remote_url(policy: dict) -> str:
    validate_policy(policy)
    return f"git@github.com:{policy['repository']}.git"


def inspect_repository(repo: Path, policy: dict, git: Git, api: API) -> None:
    validate_policy(policy)
    name = policy["repository"]
    remote = git(repo, "remote", "get-url", "origin")
    if remote not in {f"git@github.com:{name}.git", f"ssh://git@github.com/{name}.git"}:
        raise ValueError("private merge remote changed")
    value = api(f"repos/{name}")
    if not isinstance(value, dict) or not isinstance(value.get("permissions"), dict):
        raise ValueError("private repository metadata is incomplete")
    if (
        not isinstance(value, dict)
        or value.get("full_name") != name
        or value.get("private") is not True
        or value.get("html_url") != f"https://github.com/{name}"
        or value.get("default_branch") != "main"
        or value.get("archived") is not False
        or (value.get("permissions") or {}).get("push") is not True
    ):
        raise ValueError("private repository identity, visibility or write access changed")


def pin_repository(repo: Path, name: str, git: Git, api: API) -> dict:
    policy = {
        "mode": "controller-private",
        "repository": name,
        "host": "github.com",
        "visibility": "private",
        "remote": "origin",
        "base_branch": "main",
    }
    inspect_repository(repo, policy, git, api)
    return policy


def _branch(pr: dict, policy: dict, number: int, original_head: str) -> str:
    name = policy["repository"]
    if not isinstance(pr, dict) or any(
        not isinstance(pr.get(key), dict) for key in ("head", "base")
    ):
        raise ValueError("private pull request metadata is incomplete")
    if any(not isinstance(pr[key].get("repo"), dict) for key in ("head", "base")):
        raise ValueError("private pull request repository metadata is incomplete")
    if (
        type(pr.get("number")) is not int
        or pr["number"] != number
        or pr.get("html_url") != f"https://github.com/{name}/pull/{number}"
        or (pr.get("head", {}).get("repo") or {}).get("full_name") != name
        or (pr.get("base", {}).get("repo") or {}).get("full_name") != name
        or pr.get("base", {}).get("ref") != "main"
    ):
        raise ValueError("private pull request repository or identity changed")
    branch = pr.get("head", {}).get("ref")
    if not isinstance(branch, str) or not re.fullmatch(
        r"mentat/[0-9a-f]{12}/" + re.escape(original_head[:12]), branch
    ):
        raise ValueError("private merge requires its controller-owned publication branch")
    return branch


def _open(pr: dict, policy: dict, number: int, head: str, base: str) -> str:
    branch = _branch(pr, policy, number, head)
    if (
        pr.get("state") != "open"
        or pr.get("merged") is not False
        or pr["head"].get("sha") != head
        or pr["base"].get("sha") != base
    ):
        raise ValueError("private pull request head or base changed")
    return branch


def _refs(repo: Path, git: Git, branch: str) -> dict[str, str]:
    expected = {"refs/heads/main", "refs/heads/" + branch}
    rows = git(repo, "ls-remote", "--refs", "origin", *sorted(expected)).splitlines()
    refs = {}
    for row in rows:
        parts = row.split("\t")
        if (
            len(parts) != 2
            or not _OID.fullmatch(parts[0])
            or parts[1] not in expected
            or parts[1] in refs
        ):
            raise ValueError("remote private merge refs cannot be authenticated")
        refs[parts[1]] = parts[0]
    if set(refs) != expected:
        raise ValueError("remote private merge refs are missing")
    return refs


def _expect_refs(repo: Path, git: Git, branch: str, head: str, base: str) -> None:
    if _refs(repo, git, branch) != {"refs/heads/main": base, "refs/heads/" + branch: head}:
        raise ValueError("remote private merge head or base changed")


def _commit(api: API, policy: dict, oid: str, head: str, base: str, tree: str) -> None:
    if not isinstance(oid, str) or not _OID.fullmatch(oid):
        raise ValueError("private merge commit identity unavailable")
    value = api(f"repos/{policy['repository']}/commits/{oid}")
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("parents"), list)
        or any(not isinstance(parent, dict) for parent in value["parents"])
        or not isinstance(value.get("commit"), dict)
        or not isinstance(value["commit"].get("tree"), dict)
        or value.get("sha") != oid
        or [p.get("sha") for p in value["parents"]] != [base, head]
        or value.get("commit", {}).get("tree", {}).get("sha") != tree
    ):
        raise ValueError("private merge commit does not match the tested tree and ordered parents")


def reconcile(
    repo: Path, policy: dict, branch: str, number: int, head: str, base: str, git: Git, api: API
) -> dict:
    """Distinguish an untouched open PR from its explicit derived merge shape."""
    inspect_repository(repo, policy, git, api)
    pr = api(f"repos/{policy['repository']}/pulls/{number}")
    if not isinstance(pr, dict) or _branch(pr, policy, number, head) != branch:
        raise ValueError("private publication branch changed")
    result = {
        "status": "found",
        "number": number,
        "url": pr["html_url"],
        "head": head,
        "base": base,
        "merge_policy": "controller-private",
    }
    if pr.get("state") == "open":
        _open(pr, policy, number, head, base)
        _expect_refs(repo, git, branch, head, base)
        return {**result, "state": "open"}
    oid = pr.get("merge_commit_sha")
    if pr.get("state") != "closed" or pr.get("merged") is not True or pr["head"].get("sha") != oid:
        raise ValueError("private merge outcome is not confirmed")
    tree = git(repo, "rev-parse", "--verify", head + "^{tree}")
    _commit(api, policy, oid, head, base, tree)
    return {**result, "state": "merged", "merge_commit": oid, "derived_pr_head": oid}


def merge(
    repo: Path,
    policy: dict,
    number: int,
    head: str,
    base: str,
    git: Git,
    api: API,
    active: Callable[[], bool],
) -> dict:
    """Atomically fence both original refs while preserving the exact tested tree."""
    inspect_repository(repo, policy, git, api)
    pr = api(f"repos/{policy['repository']}/pulls/{number}")
    branch = _open(pr, policy, number, head, base)
    if head == base:
        raise ValueError("private merge has no distinct candidate")
    git(repo, "merge-base", "--is-ancestor", base, head)
    tree = git(repo, "rev-parse", "--verify", head + "^{tree}")
    if not _OID.fullmatch(tree):
        raise ValueError("tested private candidate tree unavailable")
    _expect_refs(repo, git, branch, head, base)
    if not active():
        return {"status": "incomplete", "reason": "session is not active"}
    oid = git(
        repo,
        "commit-tree",
        tree,
        "-p",
        base,
        "-p",
        head,
        "-m",
        f"Merge reviewed Mentat candidate #{number}",
    )
    if (
        not _OID.fullmatch(oid)
        or git(repo, "show", "-s", "--format=%P", oid).split() != [base, head]
        or git(repo, "rev-parse", "--verify", oid + "^{tree}") != tree
    ):
        raise ValueError("prepared private merge lost its exact tree or parents")
    inspect_repository(repo, policy, git, api)
    fresh = api(f"repos/{policy['repository']}/pulls/{number}")
    if _open(fresh, policy, number, head, base) != branch:
        raise ValueError("private publication branch changed")
    _expect_refs(repo, git, branch, head, base)
    if not active():
        return {"status": "incomplete", "reason": "session is not active"}
    # Both refs actually change. Git's atomic transaction must validate both old
    # values; no-op refspecs would not provide an atomic PR-head fence.
    git(
        repo,
        "push",
        "--atomic",
        "--force-with-lease=refs/heads/main:" + base,
        "--force-with-lease=refs/heads/" + branch + ":" + head,
        remote_url(policy),
        oid + ":refs/heads/main",
        oid + ":refs/heads/" + branch,
    )
    # A successful push alone does not establish that GitHub recognized this PR.
    # An uncertain/lagging response is reconciled through the existing journal.
    reason = "private merge awaits remote reconciliation"
    for attempt in range(5):
        try:
            result = reconcile(repo, policy, branch, number, head, base, git, api)
            if result.get("state") == "merged" and result.get("merge_commit") == oid:
                return {**result, "status": "succeeded", "pr_number": number}
        except ValueError as exc:
            reason = str(exc)
        if attempt < 4:
            time.sleep(0.2)
    return {"status": "incomplete", "reason": reason}
