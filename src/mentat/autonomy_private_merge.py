"""Explicit controller enforcement for a pinned private GitHub repository.

The caller authenticates local checks, reviews and session/source authority.
These operations never select this policy as a fallback from a protected merge.
"""

import base64
import copy
import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import gitio

Git = Callable[..., str]
API = Callable[[str], Any]
_FIELDS = {
    "mode",
    "repository",
    "host",
    "visibility",
    "remote",
    "base_branch",
    "repository_id",
    "repository_node_id",
    "owner_id",
    "owner_node_id",
}
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
    for key in ("repository_id", "owner_id"):
        if type(value.get(key)) is not int or value[key] <= 0:
            raise ValueError("invalid immutable repository identity")
    for key in ("repository_node_id", "owner_node_id"):
        if not isinstance(value.get(key), str) or not value[key] or len(value[key]) > 200:
            raise ValueError("invalid immutable repository node identity")
    return dict(value)


def remote_url(policy: dict) -> str:
    validate_policy(policy)
    return f"git@github.com:{policy['repository']}.git"


def _transport_configuration(repo: Path, git: Git) -> None:
    # The runtime Git adapter clears inherited GIT_* variables and disables
    # system/global Git configuration. Reject local routing/execution hooks;
    # trusted user SSH config and SSH_AUTH_SOCK remain authentication TCB.
    keys = git(repo, "config", "--no-includes", "--name-only", "--list").splitlines()
    for raw in keys:
        key = raw.lower()
        if (
            key.startswith(("url.", "include.", "includeif.", "push."))
            or key
            in {"core.sshcommand", "core.gitproxy", "core.fsmonitor", "core.alternaterefscommand"}
            or (
                key.startswith("remote.")
                and key.rsplit(".", 1)[-1]
                in {"pushurl", "receivepack", "uploadpack", "proxy", "vcs", "mirror", "push"}
            )
        ):
            raise ValueError("private merge refuses local Git transport or execution overrides")


def inspect_repository(repo: Path, policy: dict, git: Git, api: API) -> None:
    validate_policy(policy)
    _transport_configuration(repo, git)
    name = policy["repository"]
    remote = git(repo, "remote", "get-url", "origin")
    if remote not in {f"git@github.com:{name}.git", f"ssh://git@github.com/{name}.git"}:
        raise ValueError("private merge remote changed")
    value = api(f"repos/{name}")
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("permissions"), dict)
        or not isinstance(value.get("owner"), dict)
    ):
        raise ValueError("private repository metadata is incomplete")
    if (
        value.get("id") != policy["repository_id"]
        or value.get("node_id") != policy["repository_node_id"]
        or value["owner"].get("id") != policy["owner_id"]
        or value["owner"].get("node_id") != policy["owner_node_id"]
        or value["owner"].get("login") != policy["repository"].split("/", 1)[0]
    ):
        raise ValueError("immutable private repository or owner identity changed")
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
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", name)
        or ".." in name
    ):
        raise ValueError("invalid private repository name")
    value = api(f"repos/{name}")
    if not isinstance(value, dict) or not isinstance(value.get("owner"), dict):
        raise ValueError("immutable private repository metadata unavailable")
    policy = {
        "mode": "controller-private",
        "repository": name,
        "host": "github.com",
        "visibility": "private",
        "remote": "origin",
        "base_branch": "main",
        "repository_id": value.get("id"),
        "repository_node_id": value.get("node_id"),
        "owner_id": value["owner"].get("id"),
        "owner_node_id": value["owner"].get("node_id"),
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
    for part in ("head", "base"):
        metadata = pr[part]["repo"]
        if (
            metadata.get("id") != policy["repository_id"]
            or metadata.get("node_id") != policy["repository_node_id"]
        ):
            raise ValueError("private PR immutable repository identity changed")
    if (
        type(pr.get("id")) is not int
        or pr["id"] <= 0
        or not isinstance(pr.get("node_id"), str)
        or not pr["node_id"]
    ):
        raise ValueError("private PR immutable identity is missing")
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


def _refs(repo: Path, git: Git, branch: str, policy: dict) -> dict[str, str]:
    expected = {"refs/heads/main", "refs/heads/" + branch}
    rows = git(repo, "ls-remote", "--refs", remote_url(policy), *sorted(expected)).splitlines()
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


def _expect_refs(repo: Path, git: Git, branch: str, head: str, base: str, policy: dict) -> None:
    if _refs(repo, git, branch, policy) != {"refs/heads/main": base, "refs/heads/" + branch: head}:
        raise ValueError("remote private merge head or base changed")


def _commit(api: API, policy: dict, oid: object, head: str, base: str, tree: str) -> None:
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


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def bind_gateway(controller: Any, job: dict, evidence: dict) -> None:
    """Bind transport callbacks to the controller's existing publication guard."""
    gateway = controller.github
    gateway.private_prepare = None
    gateway.private_dispatch = None
    gateway.private_lookup = None
    if validate_policy(getattr(gateway, "merge_policy", None)) is None:
        return
    session_id = controller.session_id
    authority = controller.authority_digest
    source_job = job["payload"].get("source_job_id", job.get("id"))
    pinned = copy.deepcopy(gateway.merge_policy)

    def lookup(head: str, base: str, branch: str) -> dict | None:
        record = controller.store.private_merge_intent(session_id, head, base, branch, authority)
        urls = {
            e["payload"].get("result", {}).get("url")
            for e in controller.store.snapshot(session_id)["events"]
            if e["kind"] == "publication"
            and e["payload"].get("head") == head
            and e["payload"].get("base") == base
            and e["payload"].get("result", {}).get("status") == "succeeded"
        }
        if len(urls) > 1:
            raise ValueError("original private publication identity is ambiguous")
        if urls:
            url = next(iter(urls))
            if not isinstance(url, str) or not re.fullmatch(
                r"https://github\.com/" + re.escape(pinned["repository"]) + r"/pull/[1-9][0-9]*",
                url,
            ):
                raise ValueError("original private publication identity is invalid")
            number = int(url.rsplit("/", 1)[1])
            if record is not None:
                if (
                    record["intent"].get("pr_number") != number
                    or record["intent"].get("publication_url") != url
                ):
                    raise ValueError("private merge cannot reassociate another publication")
            else:
                return {
                    "publication_pr_number": number,
                    "publication_url": url,
                    "intent": None,
                    "dispatched": False,
                }
        return record

    def persist(kind: str, intent: dict) -> None:
        if (
            controller.authority_digest != authority
            or gateway.merge_policy != pinned
            or not controller._publication_guard(job, evidence)
        ):
            raise ValueError("private merge session or authority is not active")
        session = controller.store.snapshot(session_id)["session"]
        head = job["payload"]["proposal"]["head"]
        published = lookup(head, session["base"], intent.get("branch", ""))
        if (
            intent.get("head") != head
            or intent.get("base") != session["base"]
            or intent.get("branch") != "mentat/" + session_id[:12] + "/" + head[:12]
            or intent.get("policy") != pinned
            or published is None
        ):
            raise ValueError("private merge intent differs from current publication authority")
        expected_number = (
            published["intent"]["pr_number"]
            if published.get("intent")
            else published["publication_pr_number"]
        )
        if intent.get("pr_number") != expected_number:
            raise ValueError("private merge intent changed original PR identity")
        payload = {
            "head": head,
            "base": session["base"],
            "source_job_id": source_job,
            "authority_digest": authority,
            "tool_identity": copy.deepcopy(controller.tool_identity),
            "evidence_id": evidence["id"],
            "check_key": evidence["check_key"],
            "review_digest": _digest(job["payload"]["reviews"]),
            "intent": intent,
        }
        controller.store.begin_remote_effect(
            session_id, kind, payload, job_id=source_job, now=controller.clock()
        )

    gateway.private_lookup = lookup
    gateway.private_prepare = lambda intent: persist("private_merge_prepared", intent)
    gateway.private_dispatch = lambda intent: persist("private_merge_dispatched", intent)


def _intent(
    record: object, policy: dict, head: str, base: str, branch: str, tree: str, pr: dict
) -> dict:
    if not isinstance(record, dict) or not isinstance(record.get("intent"), dict):
        raise ValueError("durable private merge intent unavailable")
    value = record["intent"]
    if set(value) != {
        "head",
        "base",
        "branch",
        "tree",
        "policy",
        "merge_commit",
        "commit_raw",
        "pr_number",
        "pr_id",
        "pr_node_id",
        "publication_url",
    }:
        raise ValueError("invalid durable private merge intent")
    if any(
        value.get(k) != v
        for k, v in {
            "head": head,
            "base": base,
            "branch": branch,
            "tree": tree,
            "policy": policy,
        }.items()
    ):
        raise ValueError("private merge intent binding changed")
    for key, observed in {
        "pr_number": pr["number"],
        "pr_id": pr["id"],
        "pr_node_id": pr["node_id"],
        "publication_url": pr["html_url"],
    }.items():
        if value.get(key) != observed:
            raise ValueError("private merge original PR identity changed")
    oid, encoded = value.get("merge_commit"), value.get("commit_raw")
    if (
        not isinstance(oid, str)
        or not _OID.fullmatch(oid)
        or not isinstance(encoded, str)
        or len(encoded) > 22000
    ):
        raise ValueError("invalid durable derived commit")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 16384:
        raise ValueError("durable derived commit exceeds its bound")
    hasher = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
    if hasher(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != oid:
        raise ValueError("durable derived commit bytes do not match its identity")
    header = raw.split(b"\n\n", 1)[0].splitlines()
    if [line for line in header if line.startswith(b"tree ")] != [b"tree " + tree.encode()] or [
        line for line in header if line.startswith(b"parent ")
    ] != [b"parent " + base.encode(), b"parent " + head.encode()]:
        raise ValueError("durable derived commit has wrong tree or parents")
    return value


def reconcile(
    repo: Path,
    policy: dict,
    branch: str,
    number: int,
    head: str,
    base: str,
    git: Git,
    api: API,
    lookup: Callable[[str, str, str], dict | None],
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
    record = lookup(head, base, branch)
    if pr.get("state") == "open":
        if record is not None and record.get("dispatched"):
            raise ValueError(
                "private dispatch outcome is unknown; unchanged refs do not authorize replay"
            )
        _open(pr, policy, number, head, base)
        _expect_refs(repo, git, branch, head, base, policy)
        return {**result, "state": "open"}
    tree = git(repo, "rev-parse", "--verify", head + "^{tree}")
    intended = _intent(record, policy, head, base, branch, tree, pr)
    if not record or not record.get("dispatched"):
        raise ValueError("private merge was never durably authorized for dispatch")
    oid = pr.get("merge_commit_sha")
    if oid != intended["merge_commit"]:
        raise ValueError("observed private merge is not the durable intended commit")
    if (
        pr.get("state") != "closed"
        or pr.get("merged") is not True
        or pr["head"].get("sha") not in {head, oid}
    ):
        raise ValueError("private merge outcome is not confirmed")
    tree = git(repo, "rev-parse", "--verify", head + "^{tree}")
    _commit(api, policy, oid, head, base, tree)
    return {
        **result,
        "state": "merged",
        "merge_commit": oid,
        "derived_pr_head": oid,
        "observed_pr_head": pr["head"]["sha"],
    }


def merge(
    repo: Path,
    policy: dict,
    number: int,
    head: str,
    base: str,
    git: Git,
    api: API,
    active: Callable[[], bool],
    prepare: Callable[[dict], None],
    lookup: Callable[[str, str, str], dict | None],
    dispatch: Callable[[dict], None],
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
    _expect_refs(repo, git, branch, head, base, policy)
    if not active():
        return {"status": "incomplete", "reason": "session is not active"}
    record = lookup(head, base, branch)
    if record is not None and record.get("dispatched"):
        result = reconcile(repo, policy, branch, number, head, base, git, api, lookup)
        if result.get("state") == "merged":
            return {**result, "status": "succeeded", "pr_number": number}
        raise ValueError("previous private dispatch outcome remains unknown")
    if record is None or record.get("intent") is None:
        oid = git(
            repo,
            "-c",
            "user.name=Mentat local controller",
            "-c",
            "user.email=mentat@localhost",
            "commit-tree",
            tree,
            "-p",
            base,
            "-p",
            head,
            "--no-gpg-sign",
            "-m",
            f"Merge reviewed Mentat candidate #{number}",
        )
        if (
            not _OID.fullmatch(oid)
            or git(repo, "show", "-s", "--format=%P", oid).split() != [base, head]
            or git(repo, "rev-parse", "--verify", oid + "^{tree}") != tree
        ):
            raise ValueError("prepared private merge lost its exact tree or parents")
        raw = gitio.run(repo, "cat-file", "commit", oid, limit=16384)
        intended = {
            "head": head,
            "base": base,
            "branch": branch,
            "tree": tree,
            "policy": dict(policy),
            "merge_commit": oid,
            "commit_raw": base64.b64encode(raw).decode("ascii"),
            "pr_number": number,
            "pr_id": pr["id"],
            "pr_node_id": pr["node_id"],
            "publication_url": pr["html_url"],
        }
        prepare(intended)
        record = lookup(head, base, branch)
    intended = _intent(record, policy, head, base, branch, tree, pr)
    oid = intended["merge_commit"]
    raw = base64.b64decode(intended["commit_raw"], validate=True)
    restored = (
        gitio.run(
            repo,
            "-c",
            "core.hooksPath=/dev/null",
            "hash-object",
            "-t",
            "commit",
            "-w",
            "--stdin",
            limit=100,
            data=raw,
        )
        .decode()
        .strip()
    )
    if restored != oid:
        raise ValueError("durable private merge commit cannot be restored")
    inspect_repository(repo, policy, git, api)
    fresh = api(f"repos/{policy['repository']}/pulls/{number}")
    if _open(fresh, policy, number, head, base) != branch:
        raise ValueError("private publication branch changed")
    _expect_refs(repo, git, branch, head, base, policy)
    if not active():
        return {"status": "incomplete", "reason": "session is not active"}
    # Both refs actually change. Git's atomic transaction must validate both old
    # values; no-op refspecs would not provide an atomic PR-head fence.
    push_args = (
        "push",
        "--atomic",
        "--no-follow-tags",
        "--recurse-submodules=no",
        "--force-with-lease=refs/heads/main:" + base,
        "--force-with-lease=refs/heads/" + branch + ":" + head,
        remote_url(policy),
        oid + ":refs/heads/main",
        oid + ":refs/heads/" + branch,
    )
    git(repo, *push_args[:1], "--dry-run", *push_args[1:])
    inspect_repository(repo, policy, git, api)
    fresh = api(f"repos/{policy['repository']}/pulls/{number}")
    if _open(fresh, policy, number, head, base) != branch:
        raise ValueError("private publication branch changed")
    _intent(record, policy, head, base, branch, tree, fresh)
    _expect_refs(repo, git, branch, head, base, policy)
    if not active():
        return {"status": "incomplete", "reason": "session is not active"}
    dispatch(intended)
    git(repo, *push_args)

    # A successful push alone does not establish that GitHub recognized this PR.
    # An uncertain/lagging response is reconciled through the existing journal.
    reason = "private merge awaits remote reconciliation"
    for attempt in range(5):
        try:
            result = reconcile(repo, policy, branch, number, head, base, git, api, lookup)
            if result.get("state") == "merged" and result.get("merge_commit") == oid:
                return {**result, "status": "succeeded", "pr_number": number}
        except ValueError as exc:
            reason = str(exc)
        if attempt < 4:
            time.sleep(0.2)
    return {"status": "incomplete", "reason": reason}
