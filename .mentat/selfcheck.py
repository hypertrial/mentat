"""Exercise candidate Mentat against isolated, committed Git fixtures."""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import traceback
from importlib.util import find_spec
from pathlib import Path

try:
    source = Path(os.environ["MENTAT_CANDIDATE_DIR"]).resolve() / "src"
    sys.path.insert(0, str(source))
    package = find_spec("mentat")
    expected = source / "mentat/__init__.py"
    assert package and package.origin and Path(package.origin).resolve() == expected, (
        "Mentat must import from the candidate's src/mentat package"
    )
    try:
        metadata = tomllib.loads((source.parent / "pyproject.toml").read_text(encoding="utf-8"))
        entry_point = metadata["project"]["scripts"]["mentat"]
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        raise AssertionError("The candidate must declare a mentat console command") from exc
    assert entry_point == "mentat.cli:app", (
        "The candidate must expose mentat.cli:app as the mentat console command"
    )
    from typer.testing import CliRunner

    from mentat import cli
except AssertionError:
    traceback.print_exc()
    sys.exit(1)
except Exception:
    traceback.print_exc()
    sys.exit(2)


class Fixture:
    def __init__(self, root, exits=(0,)):
        self.repo = root / "repo"
        self.repo.mkdir()
        self.state = root / "state"
        self.runner = CliRunner()
        self.git("init", "-q")
        self.git("config", "user.name", "Mentat selfcheck")
        self.git("config", "user.email", "selfcheck@example.invalid")
        checks = self.repo / ".mentat"
        checks.mkdir()
        manifest = {
            "version": 1,
            "claims": [{"id": "fixture", "statement": "Fixture behavior succeeds"}],
            "formal": [
                {
                    "id": "model",
                    "claim": "fixture",
                    "engine": "z3",
                    "source": "model.smt2",
                    "expected": "sat",
                }
            ],
            "behavior": [],
            "allow_host": True,
        }
        (checks / "model.smt2").write_text("(assert true)\n")
        for i, code in enumerate(exits):
            name = f"check{i}.py"
            manifest["behavior"].append(
                {
                    "id": f"behavior{i}",
                    "claim": "fixture",
                    "argv": ["python3", "{checks}/" + name],
                    "artifacts": [f"result{i}.txt"],
                }
            )
            (checks / name).write_text(
                "import os\nfrom pathlib import Path\n"
                f"(Path(os.environ['MENTAT_ARTIFACTS_DIR']) / 'result{i}.txt').write_text('approved')\n"
                "assert not Path('untracked.txt').exists()\n"
                "assert not Path('.mentat').exists()\n"
                "print(Path('candidate.txt').read_text())\n"
                f"raise SystemExit({code})\n"
            )
        (checks / "contract.json").write_text(json.dumps(manifest))
        (self.repo / "candidate.txt").write_text("base")
        self.base = self.commit("base")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def invoke(self, *args):
        result = self.runner.invoke(cli.app, ["--state", str(self.state), *args])
        if result.exception and not isinstance(result.exception, SystemExit):
            raise result.exception
        return result

    def scope(self, base=None):
        return ["--repo", str(self.repo), "--base", base or self.base, "--runner", "host"]

    def approve(self, base=None):
        scope = self.scope(base)
        checked = self.invoke("spec", "check", *scope, "--json")
        assert checked.exit_code == 0, checked.output
        digest = json.loads(checked.output)["contract_digest"]
        previous = cli.operator_confirm
        try:
            cli.operator_confirm = lambda _: None
            approved = self.invoke("spec", "approve", digest, *scope)
        finally:
            cli.operator_confirm = previous
        assert approved.exit_code == 0, approved.output
        return digest

    def verify(self, rev=None, base=None):
        result = self.invoke("verify", *self.scope(base), "--rev", rev or self.base, "--json")
        return result, json.loads(result.output)

    def accept(self, run_id, base=None, confirm=False):
        previous = cli.operator_confirm
        try:
            if confirm:
                cli.operator_confirm = lambda _: None
            return self.invoke("accept", run_id, *self.scope(base))
        finally:
            cli.operator_confirm = previous


def base_pinning(root):
    f = Fixture(root)
    manifest_path = f.repo / ".mentat/contract.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["base_files"] = ["approved-fixture.txt"]
    manifest["behavior"][0]["argv"].append("{selectors}")
    manifest["behavior"][0]["selectors"] = ["{checks}/base/approved-fixture.txt"]
    manifest_path.write_text(json.dumps(manifest))
    check_path = f.repo / ".mentat/check0.py"
    check_path.write_text(
        "import sys\nfrom pathlib import Path\n"
        "assert Path(sys.argv[1]).read_text() == 'approved fixture'\n" + check_path.read_text()
    )
    fixture = f.repo / "approved-fixture.txt"
    fixture.write_text("approved fixture")
    f.base = f.commit("select committed fixture")
    digest = f.approve()
    (f.repo / "candidate.txt").write_text("committed candidate")
    (f.repo / ".mentat/check0.py").write_text("raise SystemExit(1)\n")
    (f.repo / ".mentat/model.smt2").write_text("(assert false)\n")
    (f.repo / ".mentat/contract.json").write_text("invalid candidate contract")
    fixture.write_text("candidate fixture")
    candidate = f.commit("candidate")
    (f.repo / "candidate.txt").write_text("dirty candidate")
    (f.repo / ".mentat/check0.py").write_text("raise SystemExit(2)\n")
    fixture.write_text("dirty fixture")
    (f.repo / "untracked.txt").write_text("dirty")
    result, record = f.verify(candidate)
    assert result.exit_code == 0, result.output
    assert record["revision"] == candidate and record["base"] == f.base
    assert record["contract_digest"] == digest
    bundle = f.state / "runs" / record["run_id"]
    check = record["cases"][0]["checks"][0]
    assert (bundle / check["stdout"]).read_text().strip() == "committed candidate"
    assert (bundle / check["artifacts"]["result0.txt"]).read_text() == "approved"
    result, _ = f.verify(candidate, candidate)
    assert result.exit_code == 2, result.output


def outcomes(root):
    for index, (codes, expected, status) in enumerate(
        [
            ((0,), 0, "pass"),
            ((1,), 1, "violation"),
            ((2,), 2, "incomplete"),
            ((1, 2), 2, "incomplete"),
            ((2, 1), 2, "incomplete"),
        ]
    ):
        directory = root / str(index)
        directory.mkdir()
        f = Fixture(directory, codes)
        f.approve()
        result, record = f.verify()
        assert result.exit_code == expected and record["status"] == status, result.output
        assert [case["status"] for case in record["cases"]] == [
            {0: "pass", 1: "violation", 2: "incomplete"}[code] for code in codes
        ]


def acceptance(root):
    f = Fixture(root)
    f.approve()
    result, record = f.verify()
    assert result.exit_code == 0, result.output
    run_id = record["run_id"]
    rejected = f.accept(run_id)
    assert rejected.exit_code == 2 and "interactive" in rejected.output.lower(), rejected.output
    assert not (f.state / "acceptances" / f"{run_id}.json").exists()
    accepted = f.accept(run_id, confirm=True)
    assert accepted.exit_code == 0, accepted.output
    assert (
        json.loads((f.state / "acceptances" / f"{run_id}.json").read_text())["revision"] == f.base
    )
    (f.repo / "candidate.txt").write_text("new base")
    newer = f.commit("new base")
    f.approve(newer)
    assert f.accept(run_id, base=newer, confirm=True).exit_code == 2
    (f.state / "runs" / run_id / "report.md").write_text("modified")
    assert f.accept(run_id, confirm=True).exit_code == 2
    other = root / "failing"
    other.mkdir()
    failed = Fixture(other, (1,))
    failed.approve()
    result, record = failed.verify()
    assert result.exit_code == 1, result.output
    assert failed.accept(record["run_id"], confirm=True).exit_code == 2
    incomplete_dir = root / "incomplete"
    incomplete_dir.mkdir()
    incomplete = Fixture(incomplete_dir, (2,))
    incomplete.approve()
    result, record = incomplete.verify()
    assert result.exit_code == 2, result.output
    assert incomplete.accept(record["run_id"], confirm=True).exit_code == 2


def integrity(root):
    f = Fixture(root)
    f.approve()
    result, record = f.verify()
    assert result.exit_code == 0, result.output
    run_id = record["run_id"]
    bundle = f.state / "runs" / run_id
    destination = root / "export"
    exported = f.invoke("evidence", "export", run_id, "--out", str(destination), "--json")
    assert exported.exit_code == 0, exported.output
    manifest = json.loads((destination / "manifest.json").read_text())
    report = (destination / "report.md").read_text()
    for key in (
        "base",
        "revision",
        "contract_digest",
        "snapshot_digest",
        "reproduce",
        "host_reproduce",
    ):
        assert manifest[key] == record[key], (key, manifest)
    for key in ("base", "revision", "contract_digest", "host_reproduce"):
        assert record[key] in report, (key, report)
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == {
        p.name: p.read_bytes() for p in bundle.iterdir()
    }
    for name in (
        "manifest.json",
        "report.md",
        record["cases"][0]["checks"][0]["artifacts"]["result0.txt"],
    ):
        path = bundle / name
        original = path.read_bytes()
        path.write_bytes(b"tampered")
        rejected = f.invoke("evidence", "export", run_id, "--json")
        assert rejected.exit_code == 2, rejected.output
        path.write_bytes(original)
    extra = bundle / "unexpected.txt"
    extra.write_text("extra")
    assert f.invoke("evidence", "export", run_id, "--json").exit_code == 2
    extra.unlink()
    missing = bundle / "report.md"
    original = missing.read_bytes()
    missing.unlink()
    assert f.invoke("evidence", "export", run_id, "--json").exit_code == 2
    missing.write_bytes(original)


def comparison(root):
    f = Fixture(root)
    script = f.repo / ".mentat/check0.py"
    script.write_text(
        "from pathlib import Path\nraise SystemExit(int(Path('candidate.txt').read_text()))\n"
    )
    (f.repo / "candidate.txt").write_text("1")
    base = f.commit("comparison base")
    f.approve(base)
    first, before = f.verify(base, base=base)
    assert first.exit_code == 1, first.output
    (f.repo / "candidate.txt").write_text("0")
    fixed = f.commit("fix candidate")
    second, after = f.verify(fixed, base=base)
    assert second.exit_code == 0, second.output

    args = "evidence", "compare", before["run_id"], after["run_id"], "--json"
    result = f.invoke(*args)
    assert result.exit_code == 0, result.output
    compared = json.loads(result.output)
    assert compared["before"]["revision"] == base and compared["after"]["revision"] == fixed
    checks = {check["id"]: check for check in compared["claims"][0]["checks"]}
    assert checks["model"]["before"]["status"] == checks["model"]["after"]["status"] == "pass"
    assert checks["behavior0"]["before"]["status"] == "violation"
    assert checks["behavior0"]["after"]["status"] == "pass"
    manifest_path = f.repo / ".mentat/contract.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["claims"][0]["statement"] = "State the changed requirement"
    manifest_path.write_text(json.dumps(manifest))
    evolved = f.commit("change contract claim")
    f.approve(evolved)
    result, latest = f.verify(fixed, base=evolved)
    assert result.exit_code == 0, result.output
    args_history = "evidence", "compare", before["run_id"], latest["run_id"], "--json"
    assert f.invoke(*args_history).exit_code == 2
    result = f.invoke(*args_history, "--across-contracts")
    assert result.exit_code == 0, result.output
    history = json.loads(result.output)
    assert history["claims"][0]["definition"] == "changed"
    assert history["before"]["status"] == "violation" and history["after"]["status"] == "pass"
    assert history["after"]["base"] == evolved
    Path(compared["before"]["report"]).write_text("tampered")
    assert f.invoke(*args).exit_code == 2


def claim_results(root):
    f = Fixture(root, (1, 2))
    path = f.repo / ".mentat/contract.json"
    manifest = json.loads(path.read_text())
    manifest["claims"][0]["title"] = "Observe fixture"
    manifest["review"] = {
        "overview": "Fixture examples",
        "scope_limits": ["Only the committed fixture."],
        "groups": [{"title": "Fixture", "claim_ids": ["fixture"]}],
    }
    path.write_text(json.dumps(manifest))
    base = f.commit("group the claims")
    f.approve(base)
    result, run = f.verify(base, base)
    assert result.exit_code == 2, result.output
    report = (Path(run["bundle"]) / "report.md").read_text()
    for expected in (
        "## Counterexample",
        "## Evidence gap",
        "## Fixture",
        "### Observe fixture · incomplete",
        "Formal (z3)",
        "Behavior `behavior1`",
    ):
        assert expected in report, report

    state_before = {p.relative_to(f.state): p.read_bytes() for p in f.state.rglob("*") if p.is_file()}
    output = root / "report.html"
    rendered = f.invoke("evidence", "render", run["run_id"], "--out", str(output))
    assert rendered.exit_code == 0, rendered.output
    html = output.read_text()
    for expected in (
        run["revision"],
        run["contract_digest"],
        "Observe fixture",
        "behavior0",
        "behavior1",
        "violation",
        "incomplete",
    ):
        assert expected in html, expected
    assert state_before == {
        p.relative_to(f.state): p.read_bytes() for p in f.state.rglob("*") if p.is_file()
    }
    assert f.invoke("evidence", "render", run["run_id"], "--out", str(output)).exit_code == 2
    (Path(run["bundle"]) / "report.md").write_text("tampered")
    rejected_output = root / "tampered.html"
    rejected = f.invoke("evidence", "render", run["run_id"], "--out", str(rejected_output))
    assert rejected.exit_code == 2 and not rejected_output.exists(), rejected.output


def case_evidence(root):
    f = Fixture(root)
    path = f.repo / ".mentat/contract.json"
    manifest = json.loads(path.read_text())
    manifest["behavior"][0].update(
        artifacts=["cases.json"],
        case_report={"artifact": "cases.json", "required_cases": ["observed"]},
        negative_control={
            "args": ["known-bad"],
            "violation_exit_code": 1,
            "failing_case": "observed",
        },
    )
    path.write_text(json.dumps(manifest))
    (f.repo / ".mentat/check0.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "bad = len(sys.argv) == 2 and sys.argv[1] == 'known-bad'\n"
        "file = Path(os.environ['MENTAT_ARTIFACTS_DIR']) / 'cases.json'\n"
        "file.write_text(json.dumps({'schema_version': 1, 'cases': {"
        "'observed': 'fail' if bad else 'pass'}}))\n"
        "raise SystemExit(1 if bad else 0)\n"
    )
    base = f.commit("require a named case and control")
    f.approve(base)
    result, run = f.verify(base, base)
    assert result.exit_code == 0, result.output
    check = run["cases"][0]["checks"][0]
    assert check["case_results"] == {"observed": "pass"}
    assert check["negative_control"]["qualified"]
    assert check["negative_control"]["case_results"] == {"observed": "fail"}


def openspec_draft(root):
    f = Fixture(root)
    spec = f.repo / "openspec/specs/fixture/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "### Requirement: Run fixture\nThe fixture SHALL finish.\n"
        "#### Scenario: Valid input\nThe check passes.\n"
    )
    revision = f.commit("commit spec")
    spec.write_text("dirty edit")
    result = f.invoke(
        "spec", "import-openspec", "--repo", str(f.repo), "--base", revision, "--json"
    )
    assert result.exit_code == 0, result.output
    (entry,) = json.loads(result.output)["claims"]
    assert entry["requires_behavior_check"]
    citations = entry["claim"]["provenance"]["citations"]
    assert [row["commit"] for row in citations] == [revision, revision]
    assert [row["start_line"] for row in citations] == [1, 3]
    assert spec.read_text() == "dirty edit"
    assert not f.state.exists()


def validation(root):
    import copy

    f = Fixture(root)
    original = json.loads((f.repo / ".mentat/contract.json").read_text())
    mutations = [
        lambda m: m.update(version=2),
        lambda m: m.update(formal=[]),
        lambda m: m["behavior"][0].update(claim="unknown"),
        lambda m: m["formal"][0].update(claim="unknown"),
        lambda m: m["behavior"][0].update(id="model"),
        lambda m: m["formal"][0].update(source="../escape"),
        lambda m: m["formal"][0].update(source="missing"),
        lambda m: m["formal"][0].update(expected="unsat"),
        lambda m: m["behavior"][0].update(argv=["{checks}/../escape"]),
        lambda m: m["claims"].append({"id": "uncovered", "statement": "uncovered"}),
    ]
    for i, mutate in enumerate(mutations):
        manifest = copy.deepcopy(original)
        mutate(manifest)
        (f.repo / ".mentat/contract.json").write_text(json.dumps(manifest))
        bad = f.commit(f"invalid {i}")
        result = f.invoke("spec", "check", *f.scope(bad), "--json")
        assert result.exit_code == 2, result.output
    (f.repo / ".mentat/contract.json").write_text('{"version":1,"version":1}')
    bad = f.commit("duplicate key")
    assert f.invoke("spec", "check", *f.scope(bad), "--json").exit_code == 2


def candidate_pinning(root):
    from mentat.project import snapshot

    f = Fixture(root)
    f.approve()
    (f.repo / "candidate.txt").write_text("committed")
    (f.repo / ".mentat/model.smt2").write_text("(assert false)")
    (f.repo / ".mentat/check0.py").unlink()
    (f.repo / ".mentat/check0.py").symlink_to("/etc/passwd")
    candidate = f.commit("candidate")
    (f.repo / "candidate.txt").write_text("dirty")
    (f.repo / "untracked.txt").write_text("dirty")
    captured = snapshot(f.repo, candidate)
    assert captured.files["candidate.txt"][1] == b"committed"
    assert "untracked.txt" not in captured.files
    assert not any(p.startswith(".mentat/") for p in captured.files)
    result, _ = f.verify(candidate)
    assert result.exit_code == 0, result.output
    (f.repo / "outside-link").symlink_to("/etc/passwd")
    bad = f.commit("unsupported candidate entry")
    result, _ = f.verify(bad)
    assert result.exit_code == 2, result.output


def approval_scope(root):
    from unittest.mock import patch

    from mentat import project

    f = Fixture(root)
    checked = f.invoke("spec", "check", *f.scope(), "--json")
    digest = json.loads(checked.output)["contract_digest"]
    assert f.invoke("spec", "approve", "0" * 64, *f.scope()).exit_code == 2
    assert f.invoke("spec", "approve", digest, *f.scope()).exit_code == 2
    f.approve()
    clone = root / "clone"
    subprocess.run(["git", "clone", "-q", str(f.repo), str(clone)], check=True)
    result = f.invoke(
        "verify",
        "--repo",
        str(clone),
        "--base",
        f.base,
        "--runner",
        "host",
        "--rev",
        f.base,
        "--json",
    )
    assert result.exit_code == 2, result.output
    manifest = json.loads((f.repo / ".mentat/contract.json").read_text())
    manifest["docker_image"] = "example/test@sha256:" + "a" * 64
    (f.repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    next_base = f.commit("next base")
    reviewed = f.invoke("spec", "check", *f.scope(next_base), "--against", f.base, "--json")
    assert reviewed.exit_code == 0, reviewed.output
    delta = json.loads(reviewed.output)["delta"]
    assert delta["against_digest"] == digest
    assert delta["settings"]["docker_image"]["after"] == manifest["docker_image"]
    assert f.verify(f.base, next_base)[0].exit_code == 2
    f.approve(next_base)
    with patch.object(cli, "runner_info") as runtime:
        result = f.invoke(
            "verify",
            "--repo",
            str(f.repo),
            "--base",
            next_base,
            "--runner",
            "docker",
            "--rev",
            f.base,
            "--json",
        )
    assert result.exit_code == 2 and "approve" in result.output.lower(), result.output
    runtime.assert_not_called()
    original = project._digest
    with patch.object(
        project,
        "_digest",
        side_effect=lambda files: "changed" if "cli.py" in files else original(files),
    ):
        assert f.verify(f.base, next_base)[0].exit_code == 2


def z3_vacuity(root):
    from dataclasses import replace

    from mentat.project import load_project
    from mentat.project_formal import run_formal, tool_info

    f = Fixture(root)
    project = load_project(f.repo, f.base, "host")
    tools = tool_info(project)
    for source, counter, expected, status in [
        ("(assert true)", None, "sat", "pass"),
        ("(assert false)", None, "sat", "violation"),
        ("(declare-const x Int) (assert (> x 0))", "(assert (<= x 0))", "unsat", "pass"),
        ("(declare-const x Int) (assert (> x 0))", "(assert (> x 1))", "unsat", "violation"),
        ("(assert false)", "(assert false)", "unsat", "incomplete"),
        ("(invalid command)", None, "sat", "incomplete"),
    ]:
        check = project.contract.formal[0].model_copy(
            update={"expected": expected, "counterexample": "counter.smt2" if counter else None}
        )
        modified = replace(
            project,
            contract=project.contract.model_copy(update={"formal": [check]}),
            files={
                **project.files,
                "model.smt2": source.encode(),
                "counter.smt2": (counter or "").encode(),
            },
        )
        result = run_formal(modified, tools)[0]
        assert result["status"] == status, result


def lean_audit(root):
    from dataclasses import replace
    from unittest.mock import patch

    from mentat import project_formal
    from mentat.project import load_project

    f = Fixture(root)
    project = load_project(f.repo, f.base, "host")
    check = project.contract.formal[0].model_copy(
        update={
            "engine": "lean",
            "source": "Model.lean",
            "theorem": "verified",
            "expected": None,
            "counterexample": None,
        }
    )
    project = replace(
        project,
        contract=project.contract.model_copy(update={"formal": [check]}),
        files={**project.files, "Model.lean": b"theorem verified : True := trivial"},
    )
    tools = {"lean": {"version": "4.31.0", "executable": "lean", "libdir": str(root)}}
    for theorem, audit, code, status in [
        ("verified", "'verified' does not depend on any axioms", 0, "pass"),
        ("verified", "'verified' depends on axioms: [bad]", 0, "incomplete"),
        ("verified", "ambiguous", 0, "incomplete"),
        ("missing", "missing theorem", 1, "incomplete"),
    ]:

        def run(args, **kwargs):
            if "--deps" in args:
                return subprocess.CompletedProcess(args, 0, str(root / "Init.olean"), "")
            if "-o" in args:
                return subprocess.CompletedProcess(args, 0, "", "")
            audit_source = Path(args[-1]).read_text()
            assert f"let decl := `{theorem}" in audit_source
            assert "env.getModuleIdxFor? decl == env.getModuleIdx? `Model" in audit_source
            assert "match info with" in audit_source and ".thmInfo" in audit_source
            assert f"#print axioms {theorem}" in audit_source
            return subprocess.CompletedProcess(args, code, audit, "")

        target = project.contract.model_copy(
            update={"formal": [check.model_copy(update={"theorem": theorem})]}
        )
        with patch.object(project_formal, "_run", side_effect=run):
            result = project_formal.run_formal(replace(project, contract=target), tools)[0]
        assert result["status"] == status, result
    manifest = json.loads((f.repo / ".mentat/contract.json").read_text())
    manifest["formal"] = [
        {
            "id": "model",
            "claim": "fixture",
            "engine": "lean",
            "source": "Model.lean",
            "theorem": "verified",
        }
    ]
    manifest["docker_image"] = "example/test@sha256:" + "a" * 64
    (f.repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    (f.repo / ".mentat/Model.lean").write_text("theorem verified : True := trivial")
    base = f.commit("lean contract")
    scope = ["--repo", str(f.repo), "--base", base, "--runner", "docker"]
    checked = f.invoke("spec", "check", *scope, "--json")
    assert json.loads(checked.output)["lean_runs_on_host"] is True
    prompts = []
    with patch.object(cli, "operator_confirm", side_effect=prompts.append):
        result = f.invoke("spec", "approve", json.loads(checked.output)["contract_digest"], *scope)
    assert result.exit_code == 0 and "host" in prompts[0].lower(), result.output


def runner_execution(root):
    from unittest.mock import patch

    from mentat import runner
    from mentat.project import load_project, snapshot

    f = Fixture(root)
    manifest = json.loads((f.repo / ".mentat/contract.json").read_text())
    manifest["docker_image"] = "example/test@sha256:" + "a" * 64
    manifest["base_files"] = ["candidate.txt"]
    manifest["behavior"][0]["argv"].append("{selectors}")
    manifest["behavior"][0]["selectors"] = ["{checks}/base/candidate.txt"]
    (f.repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    base = f.commit("docker enabled")
    project = load_project(f.repo, base, "docker")
    calls = []

    def docker(context, *args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return 0, json.dumps([{"Id": "sha256:" + "b" * 64, "Config": {}}]).encode(), b""
        if args[0] == "info":
            return 0, b"linux/amd64 1 true true true", b""
        if args[0] == "inspect":
            return 0, b'{"Status":"exited","ExitCode":0}', b""
        if args[0] == "cp" and "input_file" in kwargs:
            import tarfile

            with tarfile.open(fileobj=kwargs["input_file"]) as archive:
                assert archive.extractfile("mentat/checks/base/candidate.txt").read() == b"base"
        return 0, b"", b""

    with (
        patch.object(runner, "_local_context") as local,
        patch.object(runner, "_docker", side_effect=docker),
    ):
        info = runner.runner_info(project, "local")
        checks, _ = runner.run_behavior(project, snapshot(f.repo, base), "local", info, "test")
    local.assert_called_once_with("local")
    create = next(c for c in calls if c[0] == "create")
    assert checks[0]["status"] == "pass", checks
    assert "/mentat/checks/base/candidate.txt" in create
    assert create[create.index("--network") + 1] == "none"
    assert create[create.index("--pull") + 1] == "never"
    assert info["image_id"] in create and not any(
        x in create for x in ("--mount", "--volume", "-v")
    )
    assert any(c[:2] == ("image", "inspect") and c[2] == manifest["docker_image"] for c in calls)
    manifest["host_requirements"] = {"executables": ["mentat-nonexistent-test-command"]}
    (f.repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    host_base = f.commit("host prerequisites")
    inspected = f.invoke("doctor", *f.scope(host_base), "--json")
    assert inspected.exit_code == 2, inspected.output
    assert "mentat-nonexistent-test-command" in inspected.output
    for response in (
        (1, b"", b"missing"),
        (
            0,
            json.dumps(
                [{"Id": "sha256:" + "b" * 64, "Config": {"Volumes": {"/data": {}}}}]
            ).encode(),
            b"",
        ),
    ):
        with (
            patch.object(runner, "_local_context"),
            patch.object(runner, "_docker", return_value=response),
        ):
            try:
                runner.runner_info(project, "local")
            except ValueError:
                pass
            else:
                raise AssertionError("unavailable image or image volumes must be rejected")
    for context in (None, "default", "remote/context"):
        try:
            runner._local_context(context)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe Docker context accepted")
    manifest["allow_host"] = False
    (f.repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    restricted = f.commit("host disabled")
    assert f.invoke("spec", "check", *f.scope(restricted), "--json").exit_code == 2


def formal_gate(root):
    from unittest.mock import patch

    f = Fixture(root)
    manifest = json.loads((f.repo / ".mentat/contract.json").read_text())
    manifest["formal"].append({**manifest["formal"][0], "id": "second"})
    (f.repo / ".mentat/contract.json").write_text(json.dumps(manifest))
    f.base = f.commit("two models")
    f.approve()
    for status in ("violation", "incomplete"):
        models = [
            {"id": name, "claim": "fixture", "kind": "z3", "status": value, "detail": value}
            for name, value in [("model", "pass"), ("second", status)]
        ]
        with (
            patch.object(cli, "run_formal", return_value=models),
            patch.object(cli, "run_behavior") as behavior,
        ):
            result, record = f.verify()
        assert result.exit_code == (1 if status == "violation" else 2), result.output
        assert record["behavior_skipped"] and not record["cases"]
        behavior.assert_not_called()
    with (
        patch.object(cli, "run_formal", return_value=[]),
        patch.object(cli, "run_behavior") as behavior,
    ):
        result, _ = f.verify()
    assert result.exit_code == 2
    behavior.assert_not_called()


def provenance(root):
    import shlex

    from mentat.project import snapshot

    f = Fixture(root)
    digest = f.approve()
    (f.repo / "candidate.txt").write_text("candidate")
    revision = f.commit("candidate")
    result, record = f.verify(revision)
    assert result.exit_code == 0, result.output
    assert record["repo"] == str(f.repo.resolve()) and record["base"] == f.base
    assert record["revision"] == revision and record["contract_digest"] == digest
    assert record["snapshot_digest"] == snapshot(f.repo, revision).digest
    assert record["runtime"]["runner"] == "host" and record["runtime"]["platform"]
    assert record["runtime"]["path"] and record["runtime"]["mentat_python"]
    host_replay = shlex.split(record["host_reproduce"])
    assert host_replay[:3] == ["cd", record["runtime"]["invocation_directory"], "&&"]
    assert host_replay[3] == "PATH=" + record["runtime"]["invocation_path"]
    assert host_replay[4] == record["runtime"]["mentat_python"]
    assert record["tools"]["z3"]["version"]
    assert [(model["id"], model["status"]) for model in record["model_checks"]] == [
        ("model", "pass")
    ]
    assert [(case["case_id"], case["status"]) for case in record["cases"]] == [
        ("behavior0", "pass")
    ]
    args = shlex.split(record["reproduce"])
    assert args[0] == "mentat"
    replay = f.runner.invoke(cli.app, args[1:])
    assert replay.exit_code == 0, replay.output
    again = json.loads(replay.output)
    for key in ("base", "revision", "contract_digest", "snapshot_digest", "status"):
        assert again[key] == record[key], key


def review_sync(root):
    from unittest.mock import patch

    from mentat import claims_notion

    f = Fixture(root)
    path = f.repo / ".mentat/contract.json"
    manifest = json.loads(path.read_text())
    manifest["review"] = {
        "overview": "Overview",
        "scope_limits": ["Scope"],
        "groups": [{"title": "Capability", "claim_ids": [manifest["claims"][0]["id"]]}],
        "verification_limits": ["Only the staged fixture."],
        "examples": [
            {"label": "Example", "summary": "Example summary", "url": "https://example.com/demo"}
        ],
    }
    manifest["claims"][0]["title"] = "Short title"
    manifest["behavior"][0]["argv"].append("{selectors}")
    manifest["behavior"][0]["selectors"] = ["test_app.py::test_case"]
    path.write_text(json.dumps(manifest))
    first = f.invoke("claims", "render", "--repo", str(f.repo))
    assert first.exit_code == 0, first.output
    content = (f.repo / ".mentat/claims.md").read_bytes()
    assert b"### 1. Short title" in content
    assert b"## Capability" in content
    assert (
        b"- Selector `test_app.py::test_case`"
        in content.split(b"## Verification coverage and limits")[1]
    )
    assert f.invoke("claims", "render", "--repo", str(f.repo)).exit_code == 0
    assert (f.repo / ".mentat/claims.md").read_bytes() == content
    base = f.commit("review copy")
    page = "a" * 32
    text = "Before\n\n" + claims_notion._START + "\n\n---\n\nAfter"
    calls = []

    def request(page_id, payload=None):
        nonlocal text
        calls.append((page_id, payload))
        if payload:
            update = payload["update_content"]["content_updates"]
            assert len(update) == 1
            published = update[0]["new_str"].replace(
                "`test_app.py::test_case`", "<code>test_app.py::test_case</code>"
            )
            text = text.replace(update[0]["old_str"], published)
        return text

    with patch.object(claims_notion, "_request", side_effect=request):
        result = f.invoke(
            "claims", "publish", "--repo", str(f.repo), "--base", base, "--notion", page
        )
        assert result.exit_code == 0, result.output
        assert text.startswith("Before\n\n") and text.endswith("\n\nAfter")
        assert (
            f.invoke(
                "claims", "check", "--repo", str(f.repo), "--base", base, "--notion", page
            ).exit_code
            == 0
        )
        (f.repo / ".mentat/claims.md").write_text("stale")
        assert f.invoke("claims", "check", "--repo", str(f.repo)).exit_code == 1
        stale = f.commit("stale review copy")
        before = len(calls)
        assert (
            f.invoke(
                "claims", "publish", "--repo", str(f.repo), "--base", stale, "--notion", page
            ).exit_code
            == 1
        )
        assert len(calls) == before


SCENARIOS = {
    "contract.validation": validation,
    "contract.base-pinning": base_pinning,
    "verification.candidate-pinning": candidate_pinning,
    "approval.scope": approval_scope,
    "formal.z3-vacuity": z3_vacuity,
    "formal.lean-audit": lean_audit,
    "runner.execution": runner_execution,
    "runner.case-evidence": case_evidence,
    "verification.formal-gate": formal_gate,
    "verification.outcomes": outcomes,
    "evidence.provenance": provenance,
    "evidence.integrity": integrity,
    "evidence.comparison": comparison,
    "evidence.claim-results": claim_results,
    "acceptance.complete-pass": acceptance,
    "claims.review-sync": review_sync,
    "claims.openspec-draft": openspec_draft,
}

if __name__ == "__main__":
    try:
        with tempfile.TemporaryDirectory(prefix="mentat-selfcheck-") as directory:
            SCENARIOS[sys.argv[1]](Path(directory))
    except AssertionError:
        traceback.print_exc()
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(2)
