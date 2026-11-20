import json
import os
from pathlib import Path

import pytest
from helpers import git
from pydantic import ValidationError
from typer.testing import CliRunner

from mentat.cli import app
from mentat.evidence import read_run, save_run
from mentat.showcases.polaris import docker_runtime
from mentat.showcases.polaris.docker_runtime import _stage_cli
from mentat.showcases.polaris.evaluation import BEFORE, FIXED, _control, evaluate
from mentat.showcases.polaris.formal import run_z3
from mentat.showcases.polaris.model_evidence import approval, pack_digest
from mentat.showcases.polaris.replay import Snapshot, snapshot
from mentat.showcases.polaris.spec import PACK, load_cases, load_spec


@pytest.fixture
def spec():
    return load_spec(PACK / "contract.json")


def write_json(path, value):
    path.write_text(json.dumps(value))
    return path


def test_duplicate_json_keys_are_rejected_at_any_depth(tmp_path, spec):
    path = tmp_path / "cases.json"
    path.write_text(
        '{"version":1,"cases":[{"id":"a","namespaces":'
        '[{"path":["a"],"properties":{"mentat.label":"x",'
        '"mentat.label":"y"}}]}]}'
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(path, spec)


@pytest.mark.parametrize(
    ("content", "detail"),
    [(b"[" * 100_000 + b"]" * 100_000, "nesting"), (b" " * 1_000_001, "1 MB")],
)
def test_spec_check_reports_invalid_json_limits(tmp_path, content, detail):
    path = tmp_path / "contract.json"
    path.write_bytes(content)
    result = CliRunner().invoke(
        app, ["showcase", "polaris", "--spec", str(path), "spec", "check", "--json"]
    )
    assert result.exit_code == 2
    record = json.loads(result.stdout)
    assert record["status"] == "incomplete"
    assert detail in record["detail"]


@pytest.mark.parametrize("value", [None, [], ["mentat.label", "mentat.label"], ["../bad"]])
def test_selected_properties_must_be_nonempty_distinct_safe_names(tmp_path, value):
    contract = json.loads((PACK / "contract.json").read_text())
    contract["selected_properties"] = value
    with pytest.raises((ValueError, ValidationError)):
        load_spec(write_json(tmp_path / "contract.json", contract))


@pytest.mark.parametrize(
    "namespaces",
    [
        [],
        [{"path": [], "properties": {}}],
        [{"path": ["a", "b"], "properties": {}}],
        [{"path": ["a"], "properties": {}}, {"path": ["a"], "properties": {}}],
        [{"path": ["a"], "properties": {"unselected": "value"}}],
        [{"path": ["a"], "properties": {"mentat.label": None}}],
        [{"path": ["../a"], "properties": {}}],
    ],
)
def test_fixture_rejects_invalid_namespaces(tmp_path, spec, namespaces):
    path = write_json(
        tmp_path / "cases.json", {"version": 1, "cases": [{"id": "case", "namespaces": namespaces}]}
    )
    with pytest.raises((ValueError, ValidationError)):
        load_cases(path, spec)


def test_ordered_paths_and_literal_dot_are_distinct(tmp_path, spec):
    namespaces = [
        {"path": path, "properties": {}} for path in [["a"], ["b"], ["a", "b"], ["b", "a"], ["a.b"]]
    ]
    path = write_json(
        tmp_path / "cases.json",
        {"version": 1, "cases": [{"id": "paths", "namespaces": namespaces}]},
    )
    cases = load_cases(path, spec)
    assert [list(ns.path) for ns in cases[0].namespaces] == [ns["path"] for ns in namespaces]
    checks = {check["check"]: check for check in run_z3(spec)}
    assert checks["legacy-collision"]["status"] == "pass"
    left, right = checks["legacy-collision"]["witness"]
    assert left != right and ".".join(left) == ".".join(right)
    assert checks["list-identity"]["result"] == "unsat"
    assert checks["list-identity"]["nonvacuity"] == "sat"


def test_approval_tracks_contract_digest(tmp_path, spec):
    current = pack_digest(spec)
    with pytest.raises(ValueError):
        approval(tmp_path, current)
    write_json(tmp_path / "approval.json", {"contract_digest": current})
    assert approval(tmp_path, current)["contract_digest"] == current
    with pytest.raises(ValueError):
        approval(tmp_path, "0" * 64)


def test_approval_rejects_malformed_record(tmp_path):
    write_json(tmp_path / "approval.json", [])
    with pytest.raises(ValueError, match="Approval record is invalid"):
        approval(tmp_path, "0" * 64)


def test_verify_reports_deeply_nested_approval_as_invalid(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "approval.json").write_bytes(b'{"extra":' + b"[" * 100_000 + b"]" * 100_000 + b"}")
    result = CliRunner().invoke(
        app,
        [
            "--state",
            str(state),
            "showcase",
            "polaris",
            "verify",
            "--repo",
            str(tmp_path),
            "--rev",
            "0" * 40,
            "--json",
        ],
    )
    assert result.exit_code == 2
    record = json.loads(result.stdout)
    assert record["status"] == "incomplete"
    assert "nesting" in record["detail"]


def record():
    return {
        "run_id": "a" * 32,
        "status": "pass",
        "revision": "b" * 40,
        "contract_digest": "c" * 64,
        "reproduce": "mentat verify",
    }


@pytest.mark.parametrize("tamper", ["artifact", "manifest", "extra", "missing", "symlink"])
def test_evidence_rejects_bundle_tampering(tmp_path, tamper):
    bundle = save_run(tmp_path, record(), {"result.json": b"original"})
    assert read_run(tmp_path, "a" * 32)[1]["status"] == "pass"
    artifact = bundle / "result.json"
    if tamper == "artifact":
        artifact.write_bytes(b"changed")
    elif tamper == "manifest":
        (bundle / "manifest.json").write_bytes(b"{}")
    elif tamper == "extra":
        (bundle / "extra").touch()
    elif tamper == "missing":
        artifact.unlink()
    else:
        artifact.unlink()
        artifact.symlink_to(bundle / "report.md")
    with pytest.raises((ValueError, FileNotFoundError)):
        read_run(tmp_path, "a" * 32)


def test_polaris_recomputes_models_with_legacy_cache_present(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    legacy = state / "models.sqlite3"
    legacy.write_bytes(b"an existing legacy cache")
    monkeypatch.setattr("mentat.showcases.polaris.formal.run_lean", lambda _: [])
    solver_calls = []

    def solver(spec):
        solver_calls.append(spec)
        return run_z3(spec)

    monkeypatch.setattr("mentat.showcases.polaris.formal.run_z3", solver)
    command = ["--state", str(state), "showcase", "polaris", "spec", "check", "--json"]
    for _ in range(2):
        result = CliRunner().invoke(app, command)
        assert result.exit_code == 0, result.output
        checks = json.loads(result.output)["model_checks"]
        assert [(check["check"], check["result"]) for check in checks] == [
            ("legacy-collision", "sat"),
            ("list-identity", "unsat"),
        ]
    assert len(solver_calls) == 2
    assert legacy.read_bytes() == b"an existing legacy cache"


def test_snapshot_uses_committed_tree_and_full_revision(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    source = tmp_path / "client/python/apache_polaris/cli/command/setup.py"
    source.parent.mkdir(parents=True)
    source.write_text("committed = True\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "fixture")
    sha = git(tmp_path, "rev-parse", "HEAD")
    source.write_text("uncommitted = True\n")
    captured = snapshot(tmp_path, sha)
    assert captured.files == (("command/setup.py", b"committed = True\n"),)
    git(tmp_path, "commit", "-qam", "replacement")
    git(tmp_path, "replace", sha, "HEAD")
    assert snapshot(tmp_path, sha) == captured
    with pytest.raises(ValueError):
        snapshot(tmp_path, sha[:12])


def test_property_control_mutates_an_existing_second_selected_key():
    source = b"""class SetupCommand:
    def _export_namespaces_for_catalog(self, entries):
        return entries
"""
    baseline = Snapshot("a" * 40, "b" * 64, (("command/setup.py", source),))
    mutated = _control(baseline, "change-property", ("mentat.label", "mentat.note"))
    namespace = {}
    exec(mutated.files[0][1], namespace)
    entries = [
        {"name": ["org"], "properties": {"mentat.label": "Organization"}},
        {"name": ["org", "unit"], "properties": {"mentat.note": ""}},
    ]
    result = namespace["SetupCommand"]()._export_namespaces_for_catalog(entries)
    assert result[0]["properties"] == {"mentat.label": "Organization"}
    assert result[1]["properties"] == {"mentat.note": "mentat-altered"}


def test_evaluation_counts_each_baseline_variant_once(tmp_path, monkeypatch, spec):
    call_ids = []

    def outcome(run_id):
        return {
            "status": "violation"
            if any(
                variant in run_id
                for variant in ("historical", "drop-namespace", "change-property", "native-0")
            )
            else "pass"
        }

    def pytest_run(candidate, context, source, run_id, support=None):
        call_ids.append(run_id)
        return {"status": "violation"} if run_id.endswith("upstream-pytest") else outcome(run_id)

    monkeypatch.setattr(
        "mentat.showcases.polaris.evaluation.snapshot",
        lambda repo, rev: Snapshot(rev, rev, (("command/setup.py", b"# sample\n"),)),
    )
    monkeypatch.setattr("mentat.showcases.polaris.evaluation._upstream_test", lambda *args: "")
    monkeypatch.setattr(
        "mentat.showcases.polaris.evaluation.docker_runtime.doctor", lambda _: {"ok": True}
    )
    monkeypatch.setattr("mentat.showcases.polaris.evaluation.docker_runtime.run_pytest", pytest_run)
    monkeypatch.setattr(
        "mentat.showcases.polaris.evaluation.run_case", lambda *args: outcome(args[-1])
    )
    public = load_cases(PACK / "public.json", spec)[0]
    result = evaluate(tmp_path, FIXED, "test-context", spec, [public, public], public, "sample")

    assert result["status"] == "pass"
    assert (
        len(call_ids) == len(set(call_ids)) == 12
    )  # Two native checks and two baselines per variant.
    assert len(result["baseline_rows"]) == 5
    assert len(result["rows"]) == 10
    assert result["baseline_rows"][0]["snapshot_digest"] == BEFORE
    assert result["counts"]["upstream-pytest"]["ambiguous_alert"] == 3
    assert result["counts"]["hypothesis"]["detected"] == 3
    assert result["counts"]["mentat"]["detected"] == 6


def test_evaluation_failed_preflight_keeps_planned_rows(tmp_path, monkeypatch, spec):
    calls = []
    monkeypatch.setattr(
        "mentat.showcases.polaris.evaluation.snapshot",
        lambda repo, rev: Snapshot(rev, rev, (("command/setup.py", b"# sample\n"),)),
    )
    monkeypatch.setattr("mentat.showcases.polaris.evaluation._upstream_test", lambda *args: "")
    monkeypatch.setattr(
        "mentat.showcases.polaris.evaluation.docker_runtime.doctor",
        lambda context: calls.append(context) or {"ok": False},
    )

    def unexpected(*args):
        pytest.fail("preflight failure must skip all Docker executions")

    monkeypatch.setattr("mentat.showcases.polaris.evaluation.docker_runtime.run_pytest", unexpected)
    monkeypatch.setattr("mentat.showcases.polaris.evaluation.run_case", unexpected)
    public = load_cases(PACK / "public.json", spec)[0]
    result = evaluate(
        tmp_path, FIXED, "context with spaces", spec, [public, public], public, "sample"
    )

    assert calls == ["context with spaces"]
    assert result["status"] == "incomplete"
    assert result["native_regression"] == ["incomplete", "incomplete"]
    assert len(result["baseline_rows"]) == 5
    assert len(result["rows"]) == 10
    assert result["counts"]["upstream-pytest"]["incomplete"] == 5
    assert result["counts"]["hypothesis"]["incomplete"] == 5
    assert result["counts"]["mentat"]["incomplete"] == 10
    for row in result["baseline_rows"] + result["rows"]:
        for method in row["methods"].values():
            assert method["status"] == "incomplete"
            assert method["seconds"] == 0.0
            assert "mentat showcase polaris" in method["detail"]
            assert "--docker-context 'context with spaces' doctor --prepare" in method["detail"]


def test_snapshot_staging_is_readable_in_container_under_restrictive_umask(tmp_path):
    cli = tmp_path / "cli"
    previous = os.umask(0o077)
    try:
        cli.mkdir()
        candidate = Snapshot("a" * 40, "b" * 64, (("command/setup.py", b"# fixture\n"),))
        _stage_cli(candidate, cli)
    finally:
        os.umask(previous)
    assert cli.stat().st_mode & 0o777 == 0o755
    assert (cli / "command").stat().st_mode & 0o777 == 0o755
    assert (cli / "command/setup.py").stat().st_mode & 0o777 == 0o644


def test_replay_stages_owner_only_oracle_as_readable_in_container(tmp_path, monkeypatch, spec):
    runtime = tmp_path / "pack/runtime"
    runtime.mkdir(parents=True)
    installed = runtime / "oracle_cli.py"
    installed.write_bytes(b"# installed oracle\n")
    installed.chmod(0o600)
    monkeypatch.setattr(docker_runtime, "_RUNTIME", runtime)
    monkeypatch.setattr(
        docker_runtime,
        "doctor",
        lambda _: {"ok": True, "client_image": "client", "server_image": "server"},
    )
    staged_oracles = []

    def docker(_context, *args, **_kwargs):
        if args[0] == "cp" and args[2].endswith(":/opt/oracle_cli.py"):
            staged = Path(args[1])
            staged_oracles.append((staged.read_bytes(), staged.stat().st_mode & 0o777))
            return 1, b""  # Stop once we've inspected the actual source of docker cp.
        if args[0] == "inspect":
            return 0, b'{"Status":"exited","ExitCode":0,"OOMKilled":false,"Error":""}'
        return 0, b""

    monkeypatch.setattr(docker_runtime, "_docker", docker)
    candidate = Snapshot("a" * 40, "b" * 64, (("command/setup.py", b"# fixture\n"),))
    case = load_cases(PACK / "public.json", spec)[0]
    docker_runtime.run_case(candidate, "colima-mentat", case, spec.selected_properties, "oracle")
    assert staged_oracles == [(installed.read_bytes(), 0o644)]
    assert installed.stat().st_mode & 0o777 == 0o600


def test_fixture_rejects_missing_fields_and_duplicate_case_ids(tmp_path, spec):
    case = {"id": "same", "namespaces": [{"path": ["a"], "properties": {}}]}
    for document in (
        {"version": 1},
        {"version": 1, "cases": [{"id": "missing"}]},
        {"version": 1, "cases": [case, case]},
    ):
        with pytest.raises((ValueError, ValidationError)):
            load_cases(write_json(tmp_path / "cases.json", document), spec)


def test_snapshot_rejects_committed_symlink(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    source = tmp_path / "client/python/apache_polaris/cli/command/setup.py"
    source.parent.mkdir(parents=True)
    source.symlink_to("/etc/passwd")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "fixture")
    with pytest.raises(ValueError, match="unsupported"):
        snapshot(tmp_path, git(tmp_path, "rev-parse", "HEAD"))


def export_yaml(namespaces):
    import yaml

    location = "file:///tmp/mentat-catalog/"
    return yaml.safe_dump(
        {
            "catalogs": [
                {
                    "name": "mentat",
                    "storage_type": "file",
                    "default_base_location": location,
                    "allowed_locations": [location],
                    "namespaces": namespaces,
                }
            ]
        }
    ).encode()


@pytest.mark.parametrize(
    "raw",
    [
        b"catalogs: []\ncatalogs: []\n",
        b"catalogs: &catalogs []\nprincipal_roles: *catalogs\n",
        b"!!python/object/apply:os.system ['touch /tmp/mentat-unsafe']",
        b"catalogs: [",
        b"\xff",
        b"null",
        b"",
    ],
)
def test_export_rejects_untrusted_yaml(raw, spec):
    from mentat.showcases.polaris.oracle import validate_export

    with pytest.raises(ValueError):
        validate_export(raw, spec.selected_properties)


@pytest.mark.parametrize(
    "namespace",
    [
        {"name": ["a"], "properties": None},
        {"name": ["a"], "properties": {"mentat.label": None}},
        {"name": ["a"], "properties": {"unselected": "x"}},
        {"name": [], "properties": {}},
        {"name": [".."], "properties": {}},
    ],
)
def test_export_rejects_invalid_or_unselected_properties(namespace, spec):
    from mentat.showcases.polaris.oracle import validate_export

    with pytest.raises(ValueError):
        validate_export(export_yaml([namespace]), spec.selected_properties)


def test_export_property_limit_excludes_generated_location():
    from mentat.showcases.polaris.oracle import validate_export

    keys = tuple(f"field{i}" for i in range(32))
    user_properties = dict.fromkeys(keys, "value")
    actual = {**user_properties, "location": "file:///tmp/mentat-catalog/a/"}
    namespace = {"name": ["a"], "properties": actual}
    assert validate_export(export_yaml([namespace]), keys) == {
        "namespaces": [{"path": ["a"], "properties": user_properties}]
    }
    actual["extra"] = "value"
    with pytest.raises(ValueError, match="invalid namespace properties"):
        validate_export(export_yaml([namespace]), (*keys, "extra"))


def test_comparison_detects_missing_empty_duplicate_and_path_collision(spec):
    from mentat.showcases.polaris.oracle import compare, validate_export

    case = load_cases(PACK / "public.json", spec)[0]
    entries = [{"name": list(ns.path), "properties": dict(ns.properties)} for ns in case.namespaces]
    projection = validate_export(export_yaml(entries[::-1]), spec.selected_properties)
    assert all(
        check["status"] == "pass"
        for check in compare(case, projection, spec.selected_properties, "export")
    )
    entries[1]["properties"].pop("mentat.label")
    entries[2]["properties"]["mentat.note"] = ""
    entries.append({"name": ["finance"], "properties": dict(entries[0]["properties"])})
    projection = validate_export(export_yaml(entries), spec.selected_properties)
    checks = compare(case, projection, spec.selected_properties, "export")
    assert checks[0]["status"] == "violation"
    assert checks[0]["evidence"]["duplicate"] == [["finance"]]
    assert checks[1]["status"] == "violation"
    assert {diff["reason"] for diff in checks[1]["evidence"]["differences"]} == {"missing", "empty"}
    entries = [{"name": "finance.us", "properties": {}}]
    projection = validate_export(export_yaml(entries), spec.selected_properties)
    assert compare(case, projection, spec.selected_properties, "export")[0]["status"] == "violation"
