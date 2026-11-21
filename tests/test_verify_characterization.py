"""Pins the saved verification record for fixed runs through `mentat verify`.

Each pin is a digest of the record after `_normalized` plus the saved artifact digests, with
report.md left out because it names the paths. Inputs are fixed: the clock, Git dates and
author, the check scripts (`/bin/sh` or `python3` argv) and the synthetic proof checker. The
digests were recorded from base commit 19cd8a6 and pass unchanged on the refactor, in a checkout
with another repository path and another interpreter path.

Normalized because they vary by machine or run: run identifiers, temporary, resolved and
shell-quoted repository, state, PATH, cwd and interpreter paths; the host platform; resolved
executable paths; the Z3 version string; the contract and verifier digests (the verifier
digest changes whenever verifier sources change); the runtime.sha256 inside
source_qualifications, which hashes the runtime block; and random 32-hex ids. The Z3 results
stay pinned through model_checks.
"""

import hashlib
import json
import os
import re
import shlex
import sys

import pytest
from helpers import commit, git
from test_proof import _build_campaign
from test_run_behavior_characterization import Docker
from typer.testing import CliRunner

from mentat import cli, evidence, proof, runner

FIXED_CONTRACT = {
    "version": 1,
    "claims": [{"id": "claim", "statement": "candidate is valid"}],
    "formal": [
        {"id": "proof", "claim": "claim", "engine": "z3", "source": "proof.smt2", "expected": "sat"}
    ],
    "behavior": [
        {
            "id": "behavior",
            "claim": "claim",
            "argv": ["/bin/sh", "{checks}/check.sh"],
            "artifacts": ["result.txt"],
        }
    ],
    "docker_image": "example/test@sha256:" + "a" * 64,
    "allow_host": True,
}
PASS_CHECK = 'printf ok > "$MENTAT_ARTIFACTS_DIR/result.txt"\n'
VIOLATION_CHECK = "exit 1\n"
CASE_CHECK = """case "$1" in
  --known-bad) printf '%s' '{"schema_version": 1, "cases": {"first": "fail", "second": "pass"}}' > "$MENTAT_ARTIFACTS_DIR/cases.json"; exit 10 ;;
  *) printf '%s' '{"schema_version": 1, "cases": {"first": "pass", "second": "pass"}}' > "$MENTAT_ARTIFACTS_DIR/cases.json"; exit 0 ;;
esac
"""
CASE_CONTRACT = {
    **FIXED_CONTRACT,
    "behavior": [
        {
            "id": "behavior",
            "claim": "claim",
            "argv": ["/bin/sh", "{checks}/check.sh"],
            "artifacts": ["cases.json"],
            "violation_exit_codes": [10],
            "case_report": {"artifact": "cases.json", "required_cases": ["first", "second"]},
            "negative_control": {
                "args": ["--known-bad"],
                "violation_exit_code": 10,
                "failing_case": "first",
            },
        }
    ],
}
DOCKER_INFO = {
    "runner": "docker",
    "context": "test",
    "image": FIXED_CONTRACT["docker_image"],
    "image_id": "image",
    "platform": "linux/amd64",
    "engine_version": "27.0",
    "path": "/bin",
}


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(evidence, "now", lambda: "2026-01-02T03:04:05+00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00+0000")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00+0000")


def _repo(tmp_path, contract, check, formal=b"(assert true)\n", extra=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    checks = repo / ".mentat"
    checks.mkdir()
    (checks / "contract.json").write_text(json.dumps(contract))
    (checks / "check.sh").write_text(check)
    (checks / "proof.smt2").write_bytes(formal)
    (repo / "candidate.txt").write_text("base\n")
    for name, data in (extra or {}).items():
        (repo / name).write_bytes(data)
    git(repo, "add", ".")
    return repo, commit(repo, "base")


def _verify(tmp_path, monkeypatch, repo, base, runner_name):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)
    cli_runner = CliRunner()
    scope = ["--repo", str(repo), "--base", base, "--runner", runner_name]
    prefix = ["--state", str(state)]
    checked = cli_runner.invoke(cli.app, prefix + ["spec", "check", "--json", *scope])
    assert checked.exit_code == 0, checked.output
    digest = json.loads(checked.output)["contract_digest"]
    approved = cli_runner.invoke(cli.app, prefix + ["spec", "approve", digest, *scope])
    assert approved.exit_code == 0, approved.output
    result = cli_runner.invoke(cli.app, prefix + ["verify", "--rev", base, "--json", *scope])
    return result, state


def _normalized(result, state, repo):
    """The printed record and bundle path with machine-specific values replaced.

    The contract and verifier digests bind the absolute repository path and the verifier
    sources, so they are replaced; any change to the verifier sources is expected to change them.
    Machine-specific runtime values are replaced too: the host platform, the resolved executable
    paths and the Z3 version string. The Z3 result is still pinned through the model checks.
    Each source_qualifications[*].bindings.runtime.sha256 hashes the runtime block, so it is
    replaced as well.
    """
    record = json.loads(result.output)
    text = json.dumps(record, sort_keys=True)
    # Most specific values first: the resolved repository and state paths contain the raw ones.
    replacements = [
        (record["contract_digest"], "<contract-digest>"),
        (record["comparison_inputs"]["verifier"], "<verifier-digest>"),
        (record["run_id"], "<run>"),
        (str(state.resolve()), "<state>"),
        (str(repo.resolve()), "<repo>"),
        (str(state), "<state>"),
        (str(repo), "<repo>"),
        (sys.executable, "<python>"),
        (os.environ.get("PATH", os.defpath), "<path>"),
        (os.getcwd(), "<cwd>"),
    ]
    for old, new in replacements:
        # Shell-quoted reproduce commands quote a path only when it needs quoting, so both forms
        # are replaced.
        text = text.replace(shlex.quote(old), new).replace(old, new)
    # Run and proof identifiers are random 32-character hex tokens; digests and commits are not.
    normalized = json.loads(_RANDOM_ID.sub("<id>", text))
    for item in normalized.get("source_qualifications", []):
        # This digest hashes the runtime block, which holds the same machine-specific values.
        item["bindings"]["runtime"]["sha256"] = "<runtime-digest>"
    runtime = normalized["runtime"]
    runtime["platform"] = "<platform>"
    for item in runtime.get("executables", {}).values():
        item["path"] = "<executable>"
    if "z3" in normalized["tools"]:
        normalized["tools"]["z3"]["version"] = "<z3-version>"
    return normalized


def _fingerprint(result, state, repo):
    """Digest of the normalized record and of the saved artifact digests, minus report.md.

    report.md names the repository and state paths, so only the other artifacts are pinned.
    """
    run_id = json.loads(result.output)["run_id"]
    manifest = json.loads((state / "runs" / run_id / "manifest.json").read_bytes())
    saved = {
        _RANDOM_ID.sub("<id>", name): value
        for name, value in manifest["artifacts"].items()
        if name != "report.md"
    }
    return _digest({"record": _normalized(result, state, repo), "artifacts": saved})


_RANDOM_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")


def _digest(value):
    return hashlib.sha256(evidence.json_bytes(value)).hexdigest()


def test_host_pass_record_is_pinned(tmp_path, monkeypatch, fixed_clock):
    repo, base = _repo(tmp_path, FIXED_CONTRACT, PASS_CHECK)
    result, state = _verify(tmp_path, monkeypatch, repo, base, "host")
    assert result.exit_code == 0, result.output
    record = _normalized(result, state, repo)
    assert record["status"] == "pass"
    assert _fingerprint(result, state, repo) == PINNED["host-pass"]


def test_host_violation_record_is_pinned(tmp_path, monkeypatch, fixed_clock):
    repo, base = _repo(tmp_path, FIXED_CONTRACT, VIOLATION_CHECK)
    result, state = _verify(tmp_path, monkeypatch, repo, base, "host")
    assert result.exit_code == 1, result.output
    record = _normalized(result, state, repo)
    assert record["status"] == "violation"
    assert _fingerprint(result, state, repo) == PINNED["host-violation"]


def test_formal_violation_skips_behavior_and_is_pinned(tmp_path, monkeypatch, fixed_clock):
    repo, base = _repo(tmp_path, FIXED_CONTRACT, PASS_CHECK, formal=b"(assert false)\n")
    result, state = _verify(tmp_path, monkeypatch, repo, base, "host")
    assert result.exit_code == 1, result.output
    record = _normalized(result, state, repo)
    assert record["behavior_skipped"] is True
    assert _fingerprint(result, state, repo) == PINNED["host-formal-violation"]


def test_host_case_report_and_negative_control_record_is_pinned(tmp_path, monkeypatch, fixed_clock):
    repo, base = _repo(tmp_path, CASE_CONTRACT, CASE_CHECK)
    result, state = _verify(tmp_path, monkeypatch, repo, base, "host")
    assert result.exit_code == 0, result.output
    record = _normalized(result, state, repo)
    assert record["cases"][0]["status"] == "pass"
    assert _fingerprint(result, state, repo) == PINNED["host-case-report"]


def test_docker_pass_record_is_pinned(tmp_path, monkeypatch, fixed_clock):
    repo, base = _repo(tmp_path, FIXED_CONTRACT, PASS_CHECK)
    monkeypatch.setattr(cli, "runner_info", lambda project, context: dict(DOCKER_INFO))
    stub = Docker(
        monkeypatch, responses={"start": (0, b"ok\n", b"")}, artifacts={"result.txt": b"ok"}
    )
    result, state = _verify(tmp_path, monkeypatch, repo, base, "docker")
    assert result.exit_code == 0, result.output
    record = _normalized(result, state, repo)
    assert record["status"] == "pass"
    assert [kind for kind, _, _ in stub.calls] == [
        "create",
        "cp-in",
        "start",
        "inspect",
        "cp-out",
        "rm",
    ]
    assert _fingerprint(result, state, repo) == PINNED["docker-pass"]


MUTATION_CHECK = (
    'case "$(cat source.py)" in\n'
    '  killed) cases=\'{"schema_version": 1, "cases": {"first": "fail", "second": "pass"}}\'; code=10 ;;\n'
    '  *) cases=\'{"schema_version": 1, "cases": {"first": "pass", "second": "pass"}}\'; code=0 ;;\n'
    "esac\n"
    'printf \'%s\' "$cases" > "$MENTAT_ARTIFACTS_DIR/cases.json"\n'
    'exit "$code"\n'
)
MUTATION_CONTRACT = {
    **FIXED_CONTRACT,
    "source_mutations": "mutants.json",
    "behavior": [
        {
            "id": "behavior",
            "claim": "claim",
            "argv": ["/bin/sh", "{checks}/check.sh"],
            "artifacts": ["cases.json"],
            "violation_exit_codes": [10],
            "case_report": {"artifact": "cases.json", "required_cases": ["first", "second"]},
        }
    ],
}
MUTANTS = {
    "version": 1,
    "mutants": {
        "mutant": {
            "check": "behavior",
            "path": "source.py",
            "before": "baseline",
            "after": "killed",
            "expected_case": "first",
        }
    },
}


def test_host_source_mutation_record_is_pinned(tmp_path, monkeypatch, fixed_clock):
    extra = {
        ".mentat/mutants.json": json.dumps(MUTANTS).encode(),
        "source.py": b"baseline",
    }
    repo, base = _repo(tmp_path, MUTATION_CONTRACT, MUTATION_CHECK, extra=extra)
    result, state = _verify(tmp_path, monkeypatch, repo, base, "host")
    assert result.exit_code == 0, result.output
    record = _normalized(result, state, repo)
    assert record["source_qualification_status"] == "pass"
    assert _fingerprint(result, state, repo) == PINNED["host-source-mutation"]


def test_verify_calls_the_cli_run_formal_seam(tmp_path, monkeypatch, fixed_clock):
    repo, base = _repo(tmp_path, FIXED_CONTRACT, PASS_CHECK)
    calls = []

    def short_formal(project, tools):
        calls.append(project.root)
        return []

    monkeypatch.setattr(cli, "run_formal", short_formal)
    result, _ = _verify(tmp_path, monkeypatch, repo, base, "host")
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["detail"] == "Formal runner returned incomplete check results"
    assert len(calls) == 1


def test_verify_calls_the_cli_run_behavior_seam(tmp_path, monkeypatch, fixed_clock):
    repo, base = _repo(tmp_path, FIXED_CONTRACT, PASS_CHECK)
    calls = []

    def short_behavior(*args, **kwargs):
        calls.append(args)
        return [], {}

    monkeypatch.setattr(cli, "run_behavior", short_behavior)
    result, _ = _verify(tmp_path, monkeypatch, repo, base, "host")
    assert result.exit_code == 2, result.output
    detail = json.loads(result.output)["detail"]
    assert detail == "Behavior runner returned incomplete check results"
    assert len(calls) == 1


def test_verify_reads_the_cli_tool_info_seam(tmp_path, monkeypatch, fixed_clock):
    repo, base = _repo(tmp_path, FIXED_CONTRACT, PASS_CHECK)

    def missing_tool(project):
        raise ValueError("Install Lean 4.31.0")

    monkeypatch.setattr(cli, "tool_info", missing_tool)
    result, _ = _verify(tmp_path, monkeypatch, repo, base, "host")
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["detail"] == "Install Lean 4.31.0"


@pytest.fixture
def proof_campaign(tmp_path, monkeypatch, fixed_clock):
    # A fixed interpreter name keeps the committed contract, and so the commit ids, the same on
    # every machine; the record's resolved executable path is normalized below.
    return _build_campaign(
        tmp_path,
        monkeypatch,
        "output-budget",
        check_argv=["python3", "{checks}/check.py"],
    )


def test_proof_campaign_output_budget_record_is_pinned(proof_campaign, monkeypatch, fixed_clock):
    c = proof_campaign
    c.log_data["checker.log"] = b"L" * 300
    ident, _ = c.prepare(targets=["first", "second"])
    submission = c.submission("budget-submission", "first", "second")
    assert proof.check(c.state, ident, submission)["status"] == "pass"
    monkeypatch.setattr(runner, "MAX_RUN_OUTPUT", 512)
    result = c.invoke("verify", "--rev", c.base, *c.scope, "--proof-campaign", ident, "--json")
    assert result.exit_code == 0, result.output
    assert _fingerprint(result, c.state, c.repo) == PINNED["proof-campaign-budget"]


PINNED = {
    "host-pass": "e7c8ec0ab17e4c8a3cd90697124cafc4fff0ebdf5fdcf4c979f3f9c911428d36",
    "host-violation": "85c3b6112d64449b662c81246369586c33fb26c3c487aa9b467db2b394664e3b",
    "host-formal-violation": "a71da6bc2cf11e5a66e9145d7ec09be412e23d778dffd576438759e26b264f16",
    "host-case-report": "73e0b06872ca6eff6438ca91dee023e247987e95b956141e043a5b4875d23d99",
    "docker-pass": "0e5bdcf2d6977049991c79fe80999b49c36314b247b7e36b6911014c9afabfef",
    "host-source-mutation": "20945ee4c453114582367d1c755af0435fc37d749c403186495561d7229394ab",
    "proof-campaign-budget": "76125209fd89e77c793fdd431d454bee5f8cabddc454351016648fbedbd710cc",
}
