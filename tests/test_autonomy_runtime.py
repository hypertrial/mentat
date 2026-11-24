import json
from pathlib import Path

import pytest

from mentat import autonomy_runtime as runtime


def test_agent_uses_pinned_readonly_and_validates_output(tmp_path: Path, monkeypatch) -> None:
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        Path(argv[argv.index("--output-last-message") + 1]).write_text(json.dumps({
            "schema_version": 1, "decision": "accept", "summary": "Reviewed", "requirements": [],
            "claims": [], "findings": [], "verification_requests": [], "defects": [], "changes": []
        }))
        return '{"type":"thread.started","thread_id":"thread"}\n'
    features = "\n".join(f"{f} stable true" for f in ("shell_tool", "unified_exec", "apps", "browser_use", "computer_use", "multi_agent", "code_mode", "code_mode_host"))
    monkeypatch.setattr(runtime, "_run", lambda argv, **kwargs: "--ignore-user-config --disable" if "--help" in argv else features)
    monkeypatch.setattr(runtime.CodexAgent, "_execute", lambda self, argv, **kwargs: fake_run(argv, **kwargs))
    result = runtime.CodexAgent().run(tmp_path, {"payload": {"prompt": "review", "purpose": "discriminator"}}, tmp_path / "out", timeout_seconds=5)
    assert result["status"] == "succeeded" and result["session_id"] == "thread"
    assert calls[0][calls[0].index("--sandbox") + 1] == "read-only"
    assert calls[0][calls[0].index("--model") + 1] == "gpt-6.1-terra"


def test_agent_rejects_unstructured_response(tmp_path: Path, monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        Path(argv[argv.index("--output-last-message") + 1]).write_text('{"decision":"accept"}')
        return ""
    features = "\n".join(f"{f} stable true" for f in ("shell_tool", "unified_exec", "apps", "browser_use", "computer_use", "multi_agent", "code_mode", "code_mode_host"))
    monkeypatch.setattr(runtime, "_run", lambda argv, **kwargs: "--ignore-user-config --disable" if "--help" in argv else features)
    monkeypatch.setattr(runtime.CodexAgent, "_execute", lambda self, argv, **kwargs: fake_run(argv, **kwargs))
    result = runtime.CodexAgent().run(tmp_path, {"payload": {"prompt": "review", "purpose": "generator"}}, tmp_path / "out", timeout_seconds=5)
    assert result["status"] == "failed"


def test_worker_never_runs_candidate_on_host(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("host command must not execute")
    monkeypatch.setattr(runtime.LocalWorker, "diagnostics", lambda self: {"status": "incomplete"})
    monkeypatch.setattr(runtime.LocalWorker, "_execute", forbidden)
    assert runtime.LocalWorker().run(tmp_path, "a" * 40, ["./untrusted"])['status'] == 'incomplete'


def test_merge_rejects_inactive_or_changed_base(tmp_path: Path, monkeypatch) -> None:
    calls = []
    def fake_git(repo, *args):
        calls.append(args)
        return "c" * 40
    monkeypatch.setattr(runtime, "git", fake_git)
    github = runtime.GitHub(required_checks=[["pytest"]])
    head, base = "a" * 40, "b" * 40
    assert github.merge(tmp_path, 1, head, base, checks=[], reviews=[], active=False)["status"] == "incomplete"
    assert calls == []
    def fake_gh(repo, *args):
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": "org/repo"})
        return json.dumps({"required_status_checks": {"strict": True}, "enforce_admins": {"enabled": True}})
    monkeypatch.setattr(runtime, "_gh", fake_gh)
    result = github.merge(tmp_path, 1, head, base, checks=[], reviews=[], active=True)
    assert result["reason"] == "base changed"
    assert calls[0][0] == "fetch"


def test_merge_valid_and_head_race(tmp_path: Path, monkeypatch) -> None:
    import hashlib

    head, base = "a" * 40, "b" * 40
    digest = hashlib.sha256(b"diff").hexdigest()
    checks = [{"head": head, "base": base, "status": "passed", "command": ["pytest"], "tools": {"python": "3"}, "returncode": 0}]
    reviews = [{"head": head, "base": base, "status": "passed", "author_id": "generator", "reviewer_id": "reviewer", "diff_digest": digest}]
    calls = []
    monkeypatch.setattr(runtime, "git", lambda repo, *args: base if args[0] == "rev-parse" else "")
    def gh(repo, *args):
        calls.append(args)
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": "org/repo"})
        if args[:2] == ("pr", "merge"):
            return ""
        if "-H" in args:
            return "diff"
        path = args[-1]
        if path.endswith("/protection"):
            return json.dumps({"required_status_checks": {"strict": True, "contexts": ["ci"]}, "enforce_admins": {"enabled": True}})
        if "/rulesets?" in path:
            return "[]"
        if "/files?" in path:
            return json.dumps([{"filename": "app.py"}])
        if "/check-runs?" in path:
            return json.dumps({"total_count": 1, "check_runs": [{"name": "ci", "head_sha": head, "status": "completed", "conclusion": "success"}]})
        if path.endswith("/status"):
            return json.dumps({"statuses": []})
        return json.dumps({"state": "open", "head": {"sha": head}, "base": {"sha": base, "ref": "main"}})
    monkeypatch.setattr(runtime, "_gh", gh)
    monkeypatch.setattr(runtime.gitio, "run", lambda *args, **kwargs: b"diff")
    result = runtime.GitHub(required_checks=[["pytest"]]).merge(tmp_path, 1, head, base, checks=checks, reviews=reviews, active=True)
    assert result["status"] == "succeeded"
    assert any(args[:2] == ("pr", "merge") and "--match-head-commit" in args for args in calls)
    reviews[0]["head"] = "c" * 40
    calls.clear()
    assert runtime.GitHub(required_checks=[["pytest"]]).merge(tmp_path, 1, head, base, checks=checks, reviews=reviews, active=True)["status"] == "incomplete"
    assert not any(args[:2] == ("pr", "merge") for args in calls)


def test_ready_worker_passes_candidate_argv_only_to_guest(tmp_path: Path, monkeypatch) -> None:
    import base64

    calls = []
    monkeypatch.setattr(runtime.LocalWorker, "diagnostics", lambda self: {"status": "ready"})
    monkeypatch.setattr(runtime.gitio, "verify_revision", lambda repo, rev: rev)
    monkeypatch.setattr(runtime.autonomy_worker, "prepare_bundle", lambda repo, rev: b"bundle")
    monkeypatch.setattr(runtime, "_source_tree", lambda repo, rev: {".gitattributes": (0o644, b"* export-ignore"), "app.py": (0o644, b"print('ok')")})
    def fake_guest(self, argv, **kwargs):
        import tarfile
        calls.append(argv)
        if "sh" in argv:
            return "Python 3.12; Python 3.14; uv; node; chromium; lean"
        assert json.loads(base64.b64decode(argv[-4])) == ["/bin/echo", "$(touch /tmp/unsafe)"]
        with tarfile.open(fileobj=kwargs["stdin"], mode="r") as archive:
            assert archive.getnames() == ["source.tar", "source.bundle"]
            with tarfile.open(fileobj=archive.extractfile("source.tar"), mode="r") as source:
                assert source.getnames() == [".gitattributes", "app.py"]
        return "ok"
    monkeypatch.setattr(runtime.LocalWorker, "_execute", fake_guest)
    result = runtime.LocalWorker().run(tmp_path, "a" * 40, ["/bin/echo", "$(touch /tmp/unsafe)"])
    assert result["status"] == "succeeded"
    assert calls[0][0] == "limactl" and calls[1][0] == "limactl"


def test_missing_gate_and_cancelled_active_refuse_merge(tmp_path: Path, monkeypatch) -> None:
    import hashlib

    head, base = "a" * 40, "b" * 40
    git_calls = []
    monkeypatch.setattr(runtime, "git", lambda repo, *args: base if args[0] == "rev-parse" else "")
    monkeypatch.setattr(runtime.gitio, "run", lambda *args, **kwargs: b"diff")
    def gh(repo, *args):
        git_calls.append(args)
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": "org/repo"})
        if args[:2] == ("pr", "merge"):
            raise AssertionError("must not merge")
        path = args[-1]
        if path.endswith("/protection"):
            return json.dumps({"required_status_checks": {"strict": True, "contexts": ["ci"]}, "enforce_admins": {"enabled": True}})
        if "/rulesets?" in path:
            return "[]"
        if "/files?" in path:
            return '[{"filename":"app.py"}]'
        if "/check-runs?" in path:
            return json.dumps({"total_count": 1, "check_runs": [{"name": "ci", "head_sha": head, "status": "completed", "conclusion": "success"}]})
        if path.endswith("/status"):
            return '{"statuses":[]}'
        return json.dumps({"state": "open", "head": {"sha": head}, "base": {"sha": base, "ref": "main"}})
    monkeypatch.setattr(runtime, "_gh", gh)
    checks = [{"head": head, "base": base, "status": "passed", "command": ["pytest"], "tools": {"python": "3"}, "returncode": 0}]
    reviews = [{"head": head, "base": base, "status": "passed", "author_id": "author", "reviewer_id": "reviewer", "diff_digest": hashlib.sha256(b"diff").hexdigest()}]
    result = runtime.GitHub([["pytest"], ["verify"]]).merge(tmp_path, 1, head, base, checks=checks, reviews=reviews, active=True)
    assert "missing" in result["reason"]
    states = iter([True, False])
    result = runtime.GitHub([["pytest"]]).merge(tmp_path, 1, head, base, checks=checks, reviews=reviews, active=lambda: next(states))
    assert result["reason"] == "session is not active"
    assert not any(args[:2] == ("pr", "merge") for args in git_calls)


def test_agent_disables_execution_features(tmp_path: Path, monkeypatch) -> None:
    features = ("shell_tool", "unified_exec", "apps", "browser_use", "computer_use", "multi_agent", "code_mode", "code_mode_host")
    monkeypatch.setattr(runtime, "_run", lambda argv, **kwargs: "--ignore-user-config --disable" if "--help" in argv else "\n".join(f"{f} stable true" for f in features))
    def fake_execute(self, argv, **kwargs):
        assert "--ignore-user-config" in argv and "mcp_servers={}" in argv and 'web_search="disabled"' in argv
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        for feature in features:
            assert any(argv[i:i+2] == ["--disable", feature] for i in range(len(argv) - 1))
        Path(argv[argv.index("--output-last-message") + 1]).write_text(json.dumps({"schema_version": 1, "decision": "defer", "summary": "done", "requirements": [], "claims": [], "findings": [], "verification_requests": [], "defects": [], "changes": []}))
        return '{"item":{"type":"command_execution"}}'
    monkeypatch.setattr(runtime.CodexAgent, "_execute", fake_execute)
    result = runtime.CodexAgent().run(tmp_path, {"payload": {"purpose": "generator", "prompt": "test"}}, tmp_path / "out", timeout_seconds=5)
    assert result["status"] == "failed" and "command execution" in result["error"]


def test_reproduce_counterexample_uses_exact_source_and_witness(tmp_path: Path, monkeypatch) -> None:
    import hashlib
    import subprocess
    import sys

    source = b"def f(x: int) -> int:\n    return x + 1\n"
    rev = "a" * 40
    evidence = {"status": "counterexample", "source_identity": {"revision": rev, "path": "app.py", "function": "f", "source_sha256": hashlib.sha256(source).hexdigest()}, "contract": {"inputs": {"x": "int"}, "requires": "x >= 0", "ensures": "result == x"}, "counterexample": {"x": 1}}
    monkeypatch.setattr(runtime.gitio, "verify_revision", lambda repo, rev: rev)
    monkeypatch.setattr(runtime.gitio, "read_file", lambda *args: source)
    def guest(self, repo, rev, argv, **kwargs):
        p = subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True, timeout=5)
        return {"status": "succeeded" if p.returncode == 0 else "failed", "returncode": p.returncode, "stdout": p.stdout, "tools": {"python": "test"}}
    monkeypatch.setattr(runtime.LocalWorker, "run", guest)
    result = runtime.LocalWorker().reproduce(tmp_path, rev, evidence)
    assert result["status"] == "passed" and result["witness"] == {"x": 1} and result["result"] == 2
    evidence["contract"]["requires"] = "x < 0"
    assert runtime.LocalWorker().reproduce(tmp_path, rev, evidence)["status"] == "incomplete"
    evidence["contract"]["requires"] = "__import__('os').system('true')"
    assert runtime.LocalWorker().reproduce(tmp_path, rev, evidence)["status"] == "incomplete"


def test_cancel_stops_guest_even_if_unit_stop_fails(monkeypatch) -> None:
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "shell":
            raise RuntimeError("guest unreachable")
        if argv[1] == "list":
            return '{"name":"mentat-autonomy","status":"Stopped"}'
        return ""
    monkeypatch.setattr(runtime, "_run", fake_run)
    worker = runtime.LocalWorker()
    worker._units.add("mentat-job-test")
    worker.cancel_all()
    assert any(argv[1:3] == ["stop", "--force"] for argv in calls)
    assert calls[-1][1:3] == ["list", "--json"]


def test_cancel_fails_closed_if_guest_shutdown_unconfirmed(monkeypatch) -> None:
    import pytest

    def fake_run(argv, **kwargs):
        if argv[1] == "list":
            return '{"name":"mentat-autonomy","status":"Running"}'
        return ""
    monkeypatch.setattr(runtime, "_run", fake_run)
    worker = runtime.LocalWorker()
    worker._units.add("mentat-job-test")
    with pytest.raises(RuntimeError, match="could not be confirmed"):
        worker.cancel_all()


def test_verified_tree_does_not_apply_export_attributes(tmp_path: Path) -> None:
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.com")
    (tmp_path / ".gitattributes").write_text("hidden export-ignore\nmarked export-subst\n")
    (tmp_path / "hidden").write_text("included")
    (tmp_path / "marked").write_text("$Format:%H$")
    (tmp_path / ".mentat").mkdir()
    (tmp_path / ".mentat" / "contract.json").write_text("{}")
    git("add", ".")
    git("commit", "-qm", "test")
    rev = runtime.git(tmp_path, "rev-parse", "HEAD")
    files = runtime._source_tree(tmp_path, rev)
    assert files["hidden"][1] == b"included"
    assert files["marked"][1] == b"$Format:%H$"
    assert files[".mentat/contract.json"][1] == b"{}"


def test_structured_agent_records_require_provider_fields() -> None:
    import pytest

    value = {"schema_version": 1, "decision": "accept", "summary": "ok", "requirements": [], "claims": [], "findings": [], "verification_requests": [{"argv": ["pytest"], "claim_ids": ["c"], "method": "test"}], "defects": [], "changes": []}
    assert runtime._validate_result(value) == value
    for key in runtime._ARRAYS:
        item = runtime._SCHEMA["properties"][key]["items"]
        assert item["required"] == list(item["properties"]) and item["additionalProperties"] is False
    value["verification_requests"] = [{"argv": ["pytest"]}]
    with pytest.raises(ValueError, match="fields"):
        runtime._validate_result(value)


def test_embedded_checker_installer_matches_pinned_source() -> None:
    import base64
    import hashlib
    import zlib

    installer = zlib.decompress(base64.b64decode(runtime._EMBEDDED_CHECKER_INSTALLER))
    assert hashlib.sha256(installer).hexdigest() == "cd989b4f9c37e42300aef1181e668ba0bac400e38858370cc491c172dca8043c"
    assert installer == (Path(__file__).resolve().parents[1] / "scripts" / "install-proof-checker").read_bytes()


def test_git_rejects_local_filters_before_checkout(tmp_path: Path) -> None:
    import subprocess

    import pytest

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.com")
    (tmp_path / ".gitattributes").write_text("file filter=unsafe\n")
    (tmp_path / "file").write_text("content")
    git("add", ".")
    git("commit", "-qm", "test")
    marker = tmp_path / "executed"
    git("config", "filter.unsafe.smudge", f"touch {marker}; cat")
    with pytest.raises(ValueError, match="Git filters"):
        runtime.git(tmp_path, "checkout", "--", "file")
    assert not marker.exists()


def test_worker_package_bootstrap_failure_is_incomplete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime.LocalWorker, "diagnostics", lambda self: {"status": "ready"})
    monkeypatch.setattr(runtime.gitio, "verify_revision", lambda repo, rev: rev)
    monkeypatch.setattr(runtime.autonomy_worker, "prepare_bundle", lambda repo, rev: b"bundle")
    monkeypatch.setattr(runtime, "_source_tree", lambda repo, rev: {"pyproject.toml": (0o644, b"[project]")})
    def fake_execute(self, argv, **kwargs):
        if "sh" in argv:
            return "Python; uv; node; chromium; lean"
        unit = argv[-2]
        marker = f"MENTAT_BOOTSTRAP_FAILED:{unit}\nMENTAT_FINISHED:{unit}:125\n"
        raise runtime._CommandFailed("failed", 125, "", marker)
    monkeypatch.setattr(runtime.LocalWorker, "_execute", fake_execute)
    result = runtime.LocalWorker().run(tmp_path, "a" * 40, ["scripts/verify"])
    assert result["status"] == "incomplete" and "package setup" in result["reason"]
    assert "uv pip install --target /tmp/mentat-package --no-deps --no-build-isolation --offline /work" in runtime._GUEST_RUNNER


def test_repair_replays_original_witness_against_fixed_source(tmp_path: Path, monkeypatch) -> None:
    import hashlib
    import subprocess
    import sys

    old, new = "a" * 40, "b" * 40
    original = b"def f(x):\n    return x + 1\n"
    fixed = b"def f(x):\n    return x\n"
    evidence = {"id": "e", "status": "counterexample", "source_identity": {"revision": old, "path": "app.py", "function": "f", "source_sha256": hashlib.sha256(original).hexdigest()}, "contract": {"inputs": {"x": "int"}, "requires": "x >= 0", "ensures": "result == x"}, "counterexample": {"x": 1}}
    monkeypatch.setattr(runtime.gitio, "verify_revision", lambda repo, rev: rev)
    monkeypatch.setattr(runtime.gitio, "read_file", lambda repo, rev, path: original if rev == old else fixed)
    def guest(self, repo, rev, argv, **kwargs):
        result = subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True, timeout=5)
        return {"status": "succeeded" if result.returncode == 0 else "failed", "returncode": result.returncode, "stdout": result.stdout, "tools": {"python": "test"}}
    monkeypatch.setattr(runtime.LocalWorker, "run", guest)
    result = runtime.LocalWorker().verify_repair(tmp_path, new, evidence)
    assert result["status"] == "passed" and result["revision"] == new
    assert result["evidence_id"] == "e" and result["inputs"] == {"x": 1}
    assert result["precondition"] is True and result["postcondition"] is True
    assert result["method"] == "protected-runtime-repair"
    assert runtime.LocalWorker().verify_repair(tmp_path, old, evidence)["status"] == "incomplete"
    evidence["source_identity"]["source_sha256"] = "invalid"
    assert runtime.LocalWorker().verify_repair(tmp_path, new, evidence)["status"] == "incomplete"


def test_apply_changes_enforces_scope_and_symlink_boundary(tmp_path: Path) -> None:
    import pytest

    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    (outside / "untouched").write_text("safe")
    (work / "openspec").mkdir()
    (work / "openspec" / "specs").symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        runtime.apply_changes(work, [{"path": "openspec/specs/untouched", "operation": "write", "content": "bad"}], kind="generate_spec")
    assert (outside / "untouched").read_text() == "safe"
    (work / "openspec" / "specs").unlink()
    runtime.apply_changes(work, [{"path": "openspec/specs/core/spec.md", "operation": "write", "content": "spec"}], kind="generate_spec")
    assert (work / "openspec/specs/core/spec.md").read_text() == "spec"
    for path in (".git/config", "openspec/specs/../../escape", "openspec/specs/.codex/config", "scripts/verify"):
        with pytest.raises(ValueError):
            runtime.apply_changes(work, [{"path": path, "operation": "write", "content": "bad"}], kind="generate_spec")
    with pytest.raises(ValueError, match="scope"):
        runtime.apply_changes(work, [{"path": "tests/test_existing.py", "operation": "write", "content": "bad"}], kind="investigate_defect", protected_paths={"tests/test_existing.py"})
    runtime.apply_changes(work, [{"path": "openspec/specs/core/spec.md", "operation": "delete", "content": ""}], kind="generate_spec")
    assert not (work / "openspec/specs/core/spec.md").exists()


def test_apply_changes_validates_all_before_writes(tmp_path: Path) -> None:
    import pytest

    (tmp_path / "verification").mkdir()
    (tmp_path / "verification" / "existing").write_text("old")
    changes = [{"path": "verification/existing", "operation": "write", "content": "new"}, {"path": "../bad", "operation": "write", "content": "bad"}]
    with pytest.raises(ValueError):
        runtime.apply_changes(tmp_path, changes, kind="generate_verification")
    assert (tmp_path / "verification/existing").read_text() == "old"


def test_protected_checker_runs_only_for_unchanged_pinned_inputs(tmp_path: Path, monkeypatch) -> None:
    base, head = "a" * 40, "b" * 40
    worker = runtime.LocalWorker(trusted_repo=tmp_path, expected_profile={"source_revision": base})
    monkeypatch.setattr(runtime.LocalWorker, "diagnostics", lambda self: {"status": "ready"})
    monkeypatch.setattr(runtime.gitio, "verify_revision", lambda repo, rev: rev)
    monkeypatch.setattr(runtime.gitio, "read_file", lambda repo, rev, path: path.encode())
    monkeypatch.setattr(runtime, "_source_tree", lambda repo, rev: {"tests/test_proof_checker.py": (0o644, b"test")})
    def guest(self, argv, **kwargs):
        if "sh" in argv:
            return "python3.12, python3.14, uv, node, chromium, lean"
        assert runtime._CHECKER_GUEST_RUNNER in argv
        return "15 passed in 8.2s\n"
    monkeypatch.setattr(runtime.LocalWorker, "_execute", guest)
    result = worker.run(tmp_path, head, ["scripts/verify-worker"], trusted_checker=True)
    assert result["status"] == "succeeded" and result["revision"] == head
    assert result["support_baseline_revision"] == base and len(result["protected_input_digests"]) == 5
    assert result["gate_scope"] == "protected checker environment and unchanged checker implementation"
    monkeypatch.setattr(runtime.gitio, "read_file", lambda repo, rev, path: b"changed" if rev == head else b"original")
    assert worker.run(tmp_path, head, ["scripts/verify-worker"], trusted_checker=True)["status"] == "incomplete"
    assert worker.run(tmp_path, head, ["scripts/other"], trusted_checker=True)["status"] == "incomplete"


def test_protected_checker_does_not_accept_skips(tmp_path: Path, monkeypatch) -> None:
    worker = runtime.LocalWorker(trusted_repo=tmp_path, expected_profile={"source_revision": "a" * 40})
    monkeypatch.setattr(runtime.LocalWorker, "diagnostics", lambda self: {"status": "ready"})
    monkeypatch.setattr(runtime.gitio, "verify_revision", lambda repo, rev: rev)
    monkeypatch.setattr(runtime.gitio, "read_file", lambda repo, rev, path: path.encode())
    monkeypatch.setattr(runtime, "_source_tree", lambda repo, rev: {})
    monkeypatch.setattr(runtime.LocalWorker, "_execute", lambda self, argv, **kwargs: "versions" if "sh" in argv else "3 passed, 1 skipped in 1s")
    result = worker.run(tmp_path, "b" * 40, ["scripts/verify-worker"], trusted_checker=True)
    assert result["status"] == "incomplete" and "skipped" in result["reason"]


def test_pr_reconciliation_verifies_pinned_squash_inputs(tmp_path: Path, monkeypatch) -> None:
    head, base, merge, tree = "a" * 40, "b" * 40, "c" * 40, "d" * 40
    pr = {"number": 7, "url": "https://example.test/pr/7", "state": "OPEN", "headRefName": "mentat/work", "headRefOid": head, "baseRefName": "main", "baseRefOid": base, "isCrossRepository": False, "mergeCommit": None}
    commit = {"parents": [{"sha": base}], "commit": {"tree": {"sha": tree}}}
    def gh(repo, *args):
        if args[:2] == ("pr", "list"):
            return json.dumps([pr])
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": "org/repo"})
        return json.dumps(commit)
    monkeypatch.setattr(runtime, "_gh", gh)
    monkeypatch.setattr(runtime, "git", lambda repo, *args: tree)
    github = runtime.GitHub()
    assert github.reconcile(tmp_path, "mentat/work", head, base)["state"] == "open"
    pr.update(state="MERGED", mergeCommit={"oid": merge}, baseRefOid="e" * 40)
    result = github.reconcile(tmp_path, "mentat/work", head, base)
    assert result["state"] == "merged" and result["merge_commit"] == merge
    commit["parents"][0]["sha"] = "f" * 40
    assert github.reconcile(tmp_path, "mentat/work", head, base)["status"] == "incomplete"
    pr["headRefOid"] = "e" * 40
    assert github.reconcile(tmp_path, "mentat/work", head, base)["status"] == "not_found"


def test_discriminator_rejects_changes_and_generator_persists_private_logs(tmp_path: Path, monkeypatch) -> None:
    import stat

    features = ("shell_tool", "unified_exec", "apps", "browser_use", "computer_use", "multi_agent", "code_mode", "code_mode_host")
    monkeypatch.setattr(runtime, "_run", lambda argv, **kwargs: "--ignore-user-config --disable" if "--help" in argv else "\n".join(f"{f} stable true" for f in features))
    def fake_execute(self, argv, **kwargs):
        answer = {"schema_version": 1, "decision": "accept", "summary": "done", "requirements": [], "claims": [], "findings": [], "verification_requests": [], "defects": [], "changes": [{"path": "openspec/specs/a/spec.md", "operation": "write", "content": "spec"}]}
        Path(argv[argv.index("--output-last-message") + 1]).write_text(json.dumps(answer))
        return '\n'.join([json.dumps({"type": "thread.started", "thread_id": "id"}), json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "long private output"}})])
    monkeypatch.setattr(runtime.CodexAgent, "_execute", fake_execute)
    job = {"payload": {"purpose": "discriminator", "prompt": "review"}}
    assert runtime.CodexAgent().run(tmp_path, job, tmp_path / "review", timeout_seconds=5)["status"] == "failed"
    job["payload"]["purpose"] = "generator"
    result = runtime.CodexAgent().run(tmp_path, job, tmp_path / "generate", timeout_seconds=5)
    assert result["status"] == "succeeded" and result["events"] == [{"type": "thread.started", "thread_id": "id"}]
    for name in ("codex-events.jsonl", "codex-result.json"):
        assert stat.S_IMODE((tmp_path / "generate" / name).stat().st_mode) == 0o600


def _fas_patch_fixture(tmp_path):
    import hashlib

    from mentat.autonomy_state import record_digest

    path = tmp_path / '.mentat/fas.json'
    path.parent.mkdir(exist_ok=True)
    old = {'id': '00000000-0000-0000-0000-000000000001', 'statement': 'old'}
    other = {'path': 'src.py', 'status': 'gap', 'requirement_ids': []}
    data = {'schema_version': 1, 'requirements': [old], 'claims': [], 'surfaces': [other]}
    path.write_text(json.dumps(data, indent=2) + '\n')
    patch = {
        'schema_version': 1,
        'expected_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'edits': [{'collection': 'requirements', 'key': old['id'],
                   'expected_digest': record_digest(old),
                   'record': {**old, 'statement': 'new'}}],
    }
    return path, data, patch


def _fas_change(patch, path='.mentat/fas.json'):
    return {'path': path, 'operation': 'patch_fas', 'content': json.dumps(patch)}


def test_patch_fas_updates_and_inserts_without_changing_other_records(tmp_path):
    path, data, patch = _fas_patch_fixture(tmp_path)
    inserted = {'path': 'other.py', 'status': 'unexamined', 'requirement_ids': []}
    patch['edits'].append({'collection': 'surfaces', 'key': 'other.py',
                           'expected_digest': None, 'record': inserted})
    runtime.apply_changes(tmp_path, [_fas_change(patch)], kind='generate_spec')
    result = json.loads(path.read_text())
    assert result['requirements'] == [patch['edits'][0]['record']]
    assert result['surfaces'] == data['surfaces'] + [inserted]
    assert result['claims'] == []


def test_patch_fas_rejects_invalid_edits_without_writing(tmp_path):
    import copy

    import pytest

    path, _, valid = _fas_patch_fixture(tmp_path)
    original = path.read_bytes()
    cases = []
    def case():
        patch = copy.deepcopy(valid)
        cases.append(patch)
        return patch
    case()['expected_sha256'] = '0' * 64
    case()['expected_sha256'] = 'A' * 64
    case()['edits'][0]['expected_digest'] = '0' * 64
    case()['edits'][0]['key'] = '00000000-0000-0000-0000-000000000002'
    case()['edits'][0]['expected_digest'] = None
    case()['edits'][0]['record']['id'] = '00000000-0000-0000-0000-000000000002'
    case()['edits'][0]['collection'] = 'unknown'
    patch = case()
    patch['edits'].append(copy.deepcopy(patch['edits'][0]))
    case()['schema_version'] = True
    case()['edits'] = [{'collection': 'surfaces', 'key': f'file{i}.py',
                        'expected_digest': None, 'record': {'path': f'file{i}.py'}}
                       for i in range(129)]
    case()['edits'][0]['record']['statement'] = 'x' * 300_001
    for patch in cases:
        with pytest.raises((ValueError, OSError)):
            runtime.apply_changes(tmp_path, [_fas_change(patch)], kind='generate_spec')
        assert path.read_bytes() == original
    for content in ('{', '{"schema_version":1,"schema_version":1}',
                    json.dumps(valid)[:-1] + ',"edits":[]}'):
        with pytest.raises((ValueError, OSError)):
            runtime.apply_changes(tmp_path, [{'path': '.mentat/fas.json',
                'operation': 'patch_fas', 'content': content}], kind='generate_spec')
        assert path.read_bytes() == original


def test_patch_fas_rejects_scope_symlinks_and_missing_file(tmp_path):
    import pytest

    path, _, patch = _fas_patch_fixture(tmp_path)
    original = path.read_bytes()
    for kind, name in [('generate_verification', '.mentat/fas.json'),
                       ('investigate_defect', '.mentat/fas.json'),
                       ('generate_spec', 'openspec/specs/fas.json'),
                       ('generate_spec', '.mentat/../.mentat/fas.json')]:
        with pytest.raises((ValueError, OSError)):
            runtime.apply_changes(tmp_path, [_fas_change(patch, name)], kind=kind)
    path.unlink()
    with pytest.raises((ValueError, OSError)):
        runtime.apply_changes(tmp_path, [_fas_change(patch)], kind='generate_spec')
    outside = tmp_path / 'outside'
    outside.write_bytes(original)
    path.symlink_to(outside)
    with pytest.raises((ValueError, OSError)):
        runtime.apply_changes(tmp_path, [_fas_change(patch)], kind='generate_spec')
    assert outside.read_bytes() == original


def test_invalid_later_patch_does_not_write_earlier_change(tmp_path):
    import pytest

    path, _, patch = _fas_patch_fixture(tmp_path)
    spec = tmp_path / 'openspec/specs/a/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('original')
    original = path.read_bytes()
    patch['expected_sha256'] = '0' * 64
    with pytest.raises((ValueError, OSError)):
        runtime.apply_changes(tmp_path, [
            {'path': 'openspec/specs/a/spec.md', 'operation': 'write', 'content': 'changed'},
            _fas_change(patch),
        ], kind='generate_spec')
    assert spec.read_text() == 'original'
    assert path.read_bytes() == original


def test_patch_fas_compact_change_to_large_file(tmp_path):
    path, data, patch = _fas_patch_fixture(tmp_path)
    data['surfaces'][0]['description'] = 'x' * 70_000
    path.write_text(json.dumps(data))
    import hashlib
    patch['expected_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(json.dumps(patch)) < 1_000
    runtime.apply_changes(tmp_path, [_fas_change(patch)], kind='generate_spec')
    result = json.loads(path.read_text())
    assert result['requirements'][0]['statement'] == 'new'
    assert result['surfaces'] == data['surfaces']


def test_patch_fas_rejects_oversized_materialized_output(tmp_path):
    import hashlib

    import pytest

    path, data, patch = _fas_patch_fixture(tmp_path)
    data['surfaces'][0]['description'] = 'x' * 300_000
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    patch['expected_sha256'] = hashlib.sha256(original).hexdigest()
    assert len(json.dumps(patch)) < 1_000
    with pytest.raises((ValueError, OSError)):
        runtime.apply_changes(tmp_path, [_fas_change(patch)], kind='generate_spec')
    assert path.read_bytes() == original


def test_patch_fas_rejects_malformed_existing_json(tmp_path):
    import hashlib

    import pytest

    path, _, patch = _fas_patch_fixture(tmp_path)
    for raw in (b'{"schema_version": 1, "schema_version": 1}',
                b'{"schema_version": 1, "requirements": NaN}'):
        path.write_bytes(raw)
        patch['expected_sha256'] = hashlib.sha256(raw).hexdigest()
        with pytest.raises((ValueError, OSError)):
            runtime.apply_changes(tmp_path, [_fas_change(patch)], kind='generate_spec')
        assert path.read_bytes() == raw


def _exercise_guest_cache_layout(tmp_path, monkeypatch, version, inspect):
    import base64
    import io
    import os
    import shutil
    import subprocess
    import sys
    import tarfile
    import tempfile
    from unittest.mock import Mock

    source = io.BytesIO()
    with tarfile.open(fileobj=source, mode='w') as archive:
        for name, content in {
            '.python-version': version.encode(),
            'pyproject.toml': b'[build-system]\nrequires = ["hatchling==1.29.0"]\nbuild-backend = "hatchling.build"\n[project]\nname = "example"\nversion = "1.0"\n[tool.hatch.build.targets.wheel]\npackages = ["src/example"]\n',
            'src/example/__init__.py': b'value = 1\n',
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    envelope = io.BytesIO()
    with tarfile.open(fileobj=envelope, mode='w') as archive:
        for name, data in [('source.tar', source.getvalue()), ('source.bundle', b'bundle')]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    envelope.seek(0)
    folder = tmp_path / 'guest'
    original_copytree = shutil.copytree
    def copytree(src, dst, **kwargs):
        if src == '/opt/mentat-tools/cache':
            src = tmp_path / 'tool-cache'
        return original_copytree(src, dst, **kwargs)
    (tmp_path / 'tool-cache').mkdir()
    (tmp_path / 'tool-cache' / 'cache-marker').write_text('private cache')
    captured = {}
    def fake_run(argv, **kwargs):
        if argv[0] == 'git':
            Path(argv[-1]).mkdir()
        else:
            captured['cmd'] = argv
            inspect(folder / 'work', folder / 'uv-cache')
        return Mock(returncode=0)
    monkeypatch.setattr(tempfile, 'mkdtemp', lambda **kwargs: str(folder))
    folder.mkdir()
    monkeypatch.setattr(shutil, 'copytree', copytree)
    ownership = []
    monkeypatch.setattr(os, 'chown', lambda path, uid, gid, **kwargs: ownership.append((str(path), uid, gid)))
    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: b'fixed-head\n')
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(envelope))
    monkeypatch.setattr(sys, 'argv', ['guest', base64.b64encode(b'["true"]').decode(), '30', 'job', 'fixed-head'])
    import pytest
    with pytest.raises(SystemExit):
        exec(runtime._GUEST_RUNNER, {})
    assert not folder.exists()
    assert (str(folder / 'uv-cache/cache-marker'), 65534, 65534) in ownership
    assert (str(folder / 'work/pyproject.toml'), 65534, 65534) in ownership
    return captured['cmd']


def test_guest_cache_is_separate_bound_and_offline_for_both_python_versions(tmp_path, monkeypatch):
    for version in ('3.12', '3.14'):
        with monkeypatch.context() as patch:
            case = tmp_path / version
            case.mkdir()
            def inspect(work, cache, expected_version=version):
                assert (cache / 'cache-marker').read_text() == 'private cache'
                assert not (work / '.mentat-uv-cache').exists()
                assert not any(p.name == 'cache-marker' for p in work.rglob('*'))
                assert (work / 'src/example/__init__.py').read_text() == 'value = 1\n'
                assert (work / '.python-version').read_text() == expected_version
            cmd = _exercise_guest_cache_layout(case, patch, version, inspect)
            assert (case / 'tool-cache/cache-marker').read_text() == 'private cache'
            assert cmd[cmd.index('--tmpfs') + 1] == '/tmp'
            bind = cmd.index('/tmp/mentat-uv-cache')
            assert cmd[bind - 2] == '--bind' and cmd[bind - 1] == str(case / 'guest/uv-cache')
            assert cmd.index('--tmpfs') < bind
            assert cmd[cmd.index('UV_CACHE_DIR') + 1] == '/tmp/mentat-uv-cache'
            assert cmd[cmd.index('UV_OFFLINE') + 1] == '1'
            assert '--unshare-all' in cmd and '--uid=nobody' in cmd
            assert '--property=LimitFSIZE=256M' in cmd
            expected = '/opt/mentat-tools/.venv-3.14' if version == '3.14' else '/opt/mentat-tools/.venv'
            assert expected in cmd


def test_guest_cache_marker_excluded_from_real_build_artifacts(tmp_path, monkeypatch):
    import subprocess
    import sys
    import tarfile
    import zipfile

    original_run = subprocess.run
    def inspect(work, cache):
        assert (cache / 'cache-marker').is_file()
        dist = tmp_path / 'dist'
        original_run(['uv', 'build', '--offline', '--no-sources', '--no-python-downloads',
                      '--python', sys.executable, '--out-dir', str(dist)],
                     cwd=work, check=True, capture_output=True)
        with tarfile.open(next(dist.glob('*.tar.gz'))) as artifact:
            assert not any('cache-marker' in name for name in artifact.getnames())
        with zipfile.ZipFile(next(dist.glob('*.whl'))) as artifact:
            assert not any('cache-marker' in name for name in artifact.namelist())
        assert not (work / '.mentat-uv-cache').exists()
        old_cache = work / '.mentat-uv-cache'
        old_cache.mkdir()
        (old_cache / 'cache-marker').write_text('private cache')
        control = tmp_path / 'control'
        original_run(['uv', 'build', '--offline', '--no-sources', '--no-python-downloads',
                      '--python', sys.executable, '--sdist', '--out-dir', str(control)],
                     cwd=work, check=True, capture_output=True)
        with tarfile.open(next(control.glob('*.tar.gz'))) as artifact:
            assert any('cache-marker' in name for name in artifact.getnames())
    _exercise_guest_cache_layout(tmp_path, monkeypatch, '3.12', inspect)


@pytest.mark.parametrize('preexisting_symlink', [False, True])
def test_rejected_reviewer_changes_are_privately_audited_without_execution(tmp_path, monkeypatch, preexisting_symlink):
    import hashlib
    import os

    repo = tmp_path / 'repo'
    repo.mkdir()
    source = repo / 'source.py'
    source.write_text('value = 1\n')
    output = tmp_path / 'agent-output'
    answer = {'schema_version': 1, 'decision': 'revise', 'summary': 'Review found a problem.',
              'requirements': [], 'claims': [], 'findings': [], 'verification_requests': [], 'defects': [],
              'changes': [{'path': 'source.py', 'operation': 'write', 'content': 'value = 99\n'}]}
    raw = json.dumps(answer, ensure_ascii=False).encode()
    artifact = output / f'codex-rejected-result-{hashlib.sha256(raw).hexdigest()}.json'
    outside = tmp_path / 'outside'
    outside.write_text('untouched')
    if preexisting_symlink:
        output.mkdir(mode=0o700)
        artifact.symlink_to(outside)
    features = ('shell_tool', 'unified_exec', 'apps', 'browser_use', 'computer_use',
                'multi_agent', 'code_mode', 'code_mode_host')
    monkeypatch.setattr(runtime, '_run', lambda argv, **kwargs:
                        '--ignore-user-config --disable' if '--help' in argv else
                        '\n'.join(f'{feature} stable true' for feature in features))

    def fake_execute(self, argv, **kwargs):
        schema = json.loads(Path(argv[argv.index('--output-schema') + 1]).read_text())
        assert 'changes' in schema['required'] and argv[-1] == 'Review source'
        Path(argv[argv.index('--output-last-message') + 1]).write_bytes(raw)
        return '{"type":"thread.started","thread_id":"reviewer"}\n'

    monkeypatch.setattr(runtime.CodexAgent, '_execute', fake_execute)
    job = {'payload': {'purpose': 'discriminator', 'prompt': 'Review source'}}
    result = runtime.CodexAgent().run(repo, job, output, timeout_seconds=5)
    assert result['status'] == 'failed'
    assert not result.get('result') and not result.get('session_id')
    assert source.read_text() == 'value = 1\n'
    if preexisting_symlink:
        assert artifact.is_symlink() and outside.read_text() == 'untouched'
        return
    assert 'reviewer' in result['error']
    assert artifact.read_bytes() == raw
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert os.stat(output).st_mode & 0o777 == 0o700
    # Replaying identical rejected bytes must not replace or weaken the audit artifact.
    assert runtime.CodexAgent().run(repo, job, output, timeout_seconds=5)['status'] == 'failed'
    assert artifact.read_bytes() == raw and artifact.stat().st_mode & 0o777 == 0o600


def test_oversized_rejected_reviewer_output_is_not_labeled_complete(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    output = tmp_path / 'agent-output'
    features = ('shell_tool', 'unified_exec', 'apps', 'browser_use', 'computer_use',
                'multi_agent', 'code_mode', 'code_mode_host')
    monkeypatch.setattr(runtime, '_run', lambda argv, **kwargs:
                        '--ignore-user-config --disable' if '--help' in argv else
                        '\n'.join(f'{feature} stable true' for feature in features))

    def fake_execute(self, argv, **kwargs):
        Path(argv[argv.index('--output-last-message') + 1]).write_bytes(b'x' * 1_000_001)
        return '{"type":"thread.started","thread_id":"reviewer"}\n'

    monkeypatch.setattr(runtime.CodexAgent, '_execute', fake_execute)
    result = runtime.CodexAgent().run(repo, {'payload': {
        'purpose': 'discriminator', 'prompt': 'Review source'}}, output, timeout_seconds=5)
    assert result['status'] == 'failed'
    assert not result.get('result') and not result.get('session_id')
    assert not list(output.glob('codex-rejected-result-*.json'))
