import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mentat import cli, project_formal

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/iceberg-go"
BEFORE = "410b789ed781852bbb4376e4f5cb57e76da12c56"
FIXED = "3fb910983b9674efc6092418af90a1c2b821dd42"
PRIMARY = "split=true/partial=true/filtered=false"
CASES = {
    f"split={split}/partial={partial}/filtered={filtered}"
    for split in ("false", "true")
    for partial in ("false", "true")
    for filtered in ("false", "true")
}


def trace_formal_runs(monkeypatch):
    trace_path = os.environ.get("MENTAT_ICEBERG_FORMAL_TRACE")
    if trace_path is None:
        return
    original = project_formal._run

    def traced(*args, **kwargs):
        try:
            started = time.monotonic()
            before = resource.getrusage(resource.RUSAGE_CHILDREN)
            load_before = os.getloadavg()
        except Exception:
            started = before = load_before = None
        status, returncode = "error", None
        try:
            result = original(*args, **kwargs)
            status = "returncode"
            try:
                returncode = result.returncode
            except Exception:
                pass
            return result
        except subprocess.TimeoutExpired:
            status = "timeout"
            raise
        finally:
            try:
                after = resource.getrusage(resource.RUSAGE_CHILDREN)
                argv = [Path(str(arg)).name[:120] for arg in args[0]]
                phase = next((name for name in argv if name in ("Audit.lean", "Model.lean")), argv[0])
                record = {
                    "phase": phase, "argv_basenames": argv, "status": status,
                    "returncode": returncode, "elapsed_seconds": time.monotonic() - started if started is not None else None,
                    "load_start": load_before, "load_end": os.getloadavg(),
                    "rusage_scope": "process-wide cumulative children; deltas may include other children",
                    "maxrss_scope": "process-wide children high-water mark",
                    "maxrss_units": "bytes" if sys.platform == "darwin" else "KiB",
                    "maxrss_end": after.ru_maxrss,
                    "user_cpu_seconds_delta": after.ru_utime - before.ru_utime if before else None,
                    "system_cpu_seconds_delta": after.ru_stime - before.ru_stime if before else None,
                }
                with open(trace_path, "a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record) + "\n")
            except Exception:
                pass

    monkeypatch.setattr(project_formal, "_run", traced)


def test_setup_refuses_existing_destinations(tmp_path):
    existing = tmp_path / "existing path with spaces"
    existing.mkdir()
    marker = existing / "marker"
    marker.write_text("preserve")
    dangling = tmp_path / "dangling link with spaces"
    dangling.symlink_to(tmp_path / "missing")
    for destination in (existing, dangling):
        result = subprocess.run(
            ["sh", str(EXAMPLE / "setup.sh"), str(destination), str(tmp_path / "missing source")],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "exists" in result.stderr.lower()
    assert marker.read_text() == "preserve"
    assert dangling.is_symlink()


def test_iceberg_fresh_checkout_replay(tmp_path, monkeypatch):
    source = os.environ.get("MENTAT_ICEBERG_GO_SOURCE")
    if source is None:
        pytest.skip("set MENTAT_ICEBERG_GO_SOURCE to a local iceberg-go mirror")
    assert Path(source).is_dir()
    for tool in ("git", "go", "mentat", "lean"):
        assert shutil.which(tool), f"required tool missing from PATH: {tool}"
    config = tmp_path / "global git config"
    config.write_text('[core]\n    hooksPath = /dev/null\n[commit]\n    gpgSign = false\n'
                      '[user]\n    name = Test\n    email = test@example.invalid\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(cli, "operator_confirm", lambda _: None)
    # The external host_reproduce subprocess is outside this in-process trace.
    trace_formal_runs(monkeypatch)
    runner = CliRunner()

    def command(*args, cwd=None, expected=0):
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=900)
        assert proc.returncode == expected, f"{args}:\n{proc.stdout}\n{proc.stderr}"
        return proc.stdout.strip()

    def invoke(state, args, expected=0):
        result = runner.invoke(cli.app, ["--state", str(state), *args])
        assert result.exit_code == expected, result.output
        return json.loads(result.output)

    def setup(name, repo_source):
        repo = tmp_path / f"{name} repository with spaces"
        command("sh", str(EXAMPLE / "setup.sh"), str(repo), str(repo_source))
        return repo, command("git", "-C", str(repo), "rev-parse", "HEAD")

    def scope(repo, base):
        return ["--repo", str(repo), "--base", base, "--runner", "host"]

    def approve(repo, base, state):
        args = scope(repo, base)
        assert invoke(state, ["doctor", *args, "--json"])["status"] == "pass"
        assert invoke(state, ["claims", "check", "--repo", str(repo), "--base", base, "--json"])["status"] == "pass"
        checked = invoke(state, ["spec", "check", *args, "--json"])
        result = runner.invoke(cli.app, ["--state", str(state), "spec", "approve", checked["contract_digest"], *args])
        assert result.exit_code == 0, result.output

    def verify(repo, base, state, rev, expected):
        return invoke(state, ["verify", *scope(repo, base), "--rev", rev, "--json"], expected)

    def check(record):
        return next(item for case in record["cases"] for item in case["checks"] if item["id"] == "compaction-rows")

    def artifact(record, name):
        path = check(record)["artifacts"][name]
        bundle = Path(record["bundle"])
        data = (bundle / path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == json.loads((bundle / "manifest.json").read_text())["artifacts"][path]
        return path, data

    repo, base = setup("first", source)
    state = tmp_path / "state with spaces"
    command(os.sys.executable, str(EXAMPLE / "prepare.py"), str(repo), base)
    contract = json.loads((repo / ".mentat/contract.json").read_text())
    argv = next(item for item in contract["behavior"] if item["id"] == "compaction-rows")["argv"]
    assert all(Path(argv[argv.index(flag) + 1]).is_absolute() for flag in ("--mod-cache", "--build-cache"))
    approve(repo, base, state)
    before = verify(repo, base, state, BEFORE, 1)
    after = verify(repo, base, state, FIXED, 0)
    assert (before["status"], after["status"]) == ("violation", "pass")
    for record in (before, after):
        assert {item["id"]: item["status"] for item in record["model_checks"]} == {
            "row-preservation-z3": "pass", "row-preservation-lean": "pass"
        }
        report = json.loads(artifact(record, "case-report.json")[1])
        assert set(report) == {"schema_version", "cases"}
        assert report["schema_version"] == 1
        assert set(report["cases"]) == CASES
    assert json.loads(artifact(before, "case-report.json")[1])["cases"][PRIMARY] == "fail"
    assert set(json.loads(artifact(after, "case-report.json")[1])["cases"].values()) == {"pass"}
    old_case = json.loads(artifact(before, "witness.json")[1])["cases"][PRIMARY]
    new_case = json.loads(artifact(after, "witness.json")[1])["cases"][PRIMARY]
    expected_rows = [[n, "a"] for n in range(1, 13)]
    assert old_case["expected_rows"] == new_case["expected_rows"] == expected_rows
    assert len(old_case["actual_rows"]) == 1
    assert new_case["actual_rows"] == expected_rows
    assert all(rows == expected_rows for rows in new_case["committed_rows"])
    control = check(after)["negative_control"]
    assert control["status"] == "violation" and control["qualified"] is True
    control_report = json.loads((Path(after["bundle"]) / control["artifacts"]["case-report.json"]).read_text())
    assert {case for case, status in control_report["cases"].items() if status == "fail"} == {PRIMARY}
    comparison = invoke(state, ["evidence", "compare", before["run_id"], after["run_id"], "--json"])
    assert comparison["status"] == "pass"
    assert {claim["id"] for claim in comparison["claims"]} == {"compaction.rows"}
    exported = tmp_path / "exported evidence with spaces"
    invoke(state, ["evidence", "export", before["run_id"], "--out", str(exported), "--json"])
    name, original = artifact(before, "witness.json")
    assert (exported / name).read_bytes() == original
    manifest = json.loads((exported / "manifest.json").read_text())
    reproduced = json.loads(command("/bin/sh", "-c", manifest["host_reproduce"], cwd=tmp_path, expected=1))
    assert artifact(reproduced, "witness.json")[1] == original
    second_repo, second_base = setup("second", repo)
    second_state = tmp_path / "second state with spaces"
    shutil.copytree(str(repo) + "-go-cache", str(second_repo) + "-go-cache", dirs_exist_ok=True)
    approve(second_repo, second_base, second_state)
    second = verify(second_repo, second_base, second_state, BEFORE, 1)
    assert artifact(second, "witness.json")[1] == original

    command("git", "-C", str(repo), "switch", "--detach", BEFORE)
    candidate_test = repo / "table/compaction/compaction_file_grain_test.go"
    candidate_test.write_text("package compaction\n")
    command("git", "-C", str(repo), "add", str(candidate_test.relative_to(repo)))
    command("git", "-C", str(repo), "commit", "--no-gpg-sign", "-qm", "replace candidate test")
    altered = command("git", "-C", str(repo), "rev-parse", "HEAD")
    tampered = verify(repo, base, state, altered, 1)
    assert artifact(tampered, "witness.json")[1] == original


def passing_events(data):
    events = []
    for name in ["TestMentatCompactionRows", *("TestMentatCompactionRows/" + name for name in sorted(CASES))]:
        events.extend([{"Action": "run", "Test": name}, {"Action": "pass", "Test": name}])
    events.append({"Action": "output", "Output": "MENTAT_OBSERVATIONS=" + json.dumps(data) + "\n"})
    return events


def run_driver(tmp_path, observations, *, events=None, go_exit=0, extra=(), valid_caches=True, flood=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    checks = tmp_path / "checks with spaces"
    checks.mkdir()
    (checks / "probe_test.go").write_text("package table\n")
    (tmp_path / "table").mkdir()
    bin_dir = tmp_path / "bin with spaces"
    bin_dir.mkdir()
    artifacts = tmp_path / "artifacts with spaces"
    mod_cache = tmp_path / "mod cache"
    build_cache = tmp_path / "build cache"
    mod_cache.mkdir()
    build_cache.mkdir()
    if events is None:
        events = passing_events(observations)
    script = bin_dir / "go"
    script.write_text("#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nassert Path('table/mentat_compaction_replay_test.go').read_text() == 'package table\\n'\nsys.stdout.write(" + repr("".join(json.dumps(event) + "\n" for event in events)) + ")\nsys.exit(" + str(go_exit) + ")\n")
    if flood:
        script.write_text("#!/usr/bin/env python3\nimport os\nwhile True: os.write(1, b'x' * 65536)\n")
    script.chmod(0o755)
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "MENTAT_CHECKS_DIR": str(checks), "MENTAT_ARTIFACTS_DIR": str(artifacts)}
    result = subprocess.run([os.sys.executable, str(EXAMPLE / "contract/check.py"), "--mod-cache",
                             str(mod_cache) if valid_caches else "relative", "--build-cache", str(build_cache), *extra],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=15)
    report = json.loads((artifacts / "case-report.json").read_text())
    witness = json.loads((artifacts / "witness.json").read_text())
    assert set(report) == {"schema_version", "cases"}
    assert report["schema_version"] == 1 and set(report["cases"]) == CASES
    if witness.get("cases"):
        for name, case in witness["cases"].items():
            assert case["status"] == report["cases"][name]
    return result, report, witness


def observations():
    rows = [{"id": n, "data": "a"} for n in range(1, 13)]
    return {"schema_version": 1, "go_version": "go1.26.3", "cases": {
        name: {"initial_rows": rows, "committed_rows": [rows], "final_rows": rows, "operation_error": ""}
        for name in CASES
    }}


def test_native_negative_control_only_changes_primary(tmp_path):
    baseline, report, witness = run_driver(tmp_path / "baseline", observations())
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert set(report["cases"].values()) == {"pass"}
    controlled, controlled_report, controlled_witness = run_driver(tmp_path / "control", observations(), extra=("--negative-control",))
    assert controlled.returncode == 10, controlled.stdout + controlled.stderr
    assert json.loads(controlled.stdout)["failing_case"] == PRIMARY
    assert {name for name, status in controlled_report["cases"].items() if status == "fail"} == {PRIMARY}
    original_primary = witness["cases"][PRIMARY].copy()
    altered_primary = controlled_witness["cases"][PRIMARY].copy()
    assert altered_primary.pop("actual_rows") == [[1, "a"]]
    original_primary.pop("actual_rows")
    assert altered_primary.pop("status") == "fail"
    assert original_primary.pop("status") == "pass"
    assert altered_primary == original_primary
    assert {name: value for name, value in witness["cases"].items() if name != PRIMARY} == {
        name: value for name, value in controlled_witness["cases"].items() if name != PRIMARY
    }


@pytest.mark.parametrize("kind", ["missing", "duplicate", "substitution", "snapshot"])
def test_native_detects_row_multiset_changes(tmp_path, kind):
    data = observations()
    item = data["cases"][PRIMARY]
    if kind == "missing":
        item["final_rows"] = item["final_rows"][:-1]
    elif kind == "duplicate":
        item["final_rows"] = item["final_rows"] + [item["final_rows"][0]]
    elif kind == "substitution":
        item["final_rows"] = item["final_rows"][:-1] + [item["final_rows"][0]]
    else:
        item["committed_rows"] = [item["committed_rows"][0][:-1]]
    result, report, _ = run_driver(tmp_path, data)
    assert result.returncode == 10, result.stdout + result.stderr
    assert report["cases"][PRIMARY] == "fail"


@pytest.mark.parametrize("kind", ["missing-case", "malformed-row", "build-failure", "invalid-cache", "missing-events", "skipped", "duplicate-event"])
def test_native_incomplete_is_never_a_violation(tmp_path, kind):
    data = observations()
    events = None
    if kind == "missing-case":
        del data["cases"][PRIMARY]
    elif kind == "malformed-row":
        data["cases"][PRIMARY]["final_rows"] = [{"id": True, "data": "a"}]
    elif kind in {"missing-events", "skipped", "duplicate-event"}:
        events = passing_events(data)
        target = "TestMentatCompactionRows/" + PRIMARY
        if kind == "missing-events":
            events = [event for event in events if event.get("Test") != target]
        elif kind == "skipped":
            next(event for event in events if event.get("Test") == target and event["Action"] == "pass")["Action"] = "skip"
        else:
            events.append({"Action": "run", "Test": target})
    result, report, _ = run_driver(tmp_path, data, events=events, go_exit=1 if kind == "build-failure" else 0,
                                   valid_caches=kind != "invalid-cache")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "fail" not in report["cases"].values()


def test_native_operation_error_with_preserved_rows_passes(tmp_path):
    data = observations()
    data["cases"][PRIMARY]["operation_error"] = "commit failed"
    result, report, witness = run_driver(tmp_path, data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["cases"][PRIMARY] == "pass"
    assert witness["cases"][PRIMARY]["operation_failed"] is True


@pytest.mark.parametrize("kind", ["malformed-json", "duplicate-key", "duplicate-marker", "missing-marker"])
def test_native_rejects_untrustworthy_observations(tmp_path, kind):
    data = observations()
    events = passing_events(data)
    marker = events[-1]
    if kind == "malformed-json":
        marker["Output"] = "MENTAT_OBSERVATIONS={invalid}\n"
    elif kind == "duplicate-key":
        marker["Output"] = marker["Output"].replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    elif kind == "duplicate-marker":
        events.append(marker.copy())
    else:
        events.pop()
    result, report, witness = run_driver(tmp_path, data, events=events)
    assert result.returncode == 2, result.stdout + result.stderr
    assert set(report["cases"].values()) == {"error"}
    assert witness["cases"] == {}


def test_native_rejects_boolean_schema_version(tmp_path):
    data = observations()
    data["schema_version"] = True
    result, report, witness = run_driver(tmp_path, data)
    assert result.returncode == 2, result.stdout + result.stderr
    assert set(report["cases"].values()) == {"error"}
    assert witness["cases"] == {}


def test_native_limits_unbounded_go_output(tmp_path):
    result, report, witness = run_driver(tmp_path, observations(), flood=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert set(report["cases"].values()) == {"error"}
    assert witness["cases"] == {}
    raw = tmp_path / "artifacts with spaces" / "native-observations.json"
    assert raw.stat().st_size < 200_000


def test_formal_trace_preserves_result_and_arguments(tmp_path, monkeypatch):
    trace = tmp_path / "formal trace.jsonl"
    monkeypatch.setenv("MENTAT_ICEBERG_FORMAL_TRACE", str(trace))
    result = subprocess.CompletedProcess(["lean"], 7, "out", "err")
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(project_formal, "_run", original)
    trace_formal_runs(monkeypatch)
    argv = ["/tools/lean", "/tmp/session/Audit.lean"]
    kwargs = {"input": "private input", "cwd": tmp_path, "timeout": 15, "env": {"SECRET": "value"}}
    assert project_formal._run(argv, **kwargs) is result
    assert calls == [((argv,), kwargs)]
    record = json.loads(trace.read_text())
    assert (record["phase"], record["argv_basenames"], record["status"], record["returncode"]) == (
        "Audit.lean", ["lean", "Audit.lean"], "returncode", 7
    )
    assert record["elapsed_seconds"] >= 0
    assert record["maxrss_units"] in {"bytes", "KiB"}
    assert len(record["load_start"]) == len(record["load_end"]) == 3
    assert "SECRET" not in trace.read_text() and "private input" not in trace.read_text()


def test_formal_trace_preserves_timeout_and_appends(tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("MENTAT_ICEBERG_FORMAL_TRACE", str(trace))
    timeout = subprocess.TimeoutExpired(["lean"], 15)
    def original(*args, **kwargs):
        raise timeout
    monkeypatch.setattr(project_formal, "_run", original)
    trace_formal_runs(monkeypatch)
    for _ in range(2):
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            project_formal._run(["/bin/lean", "--deps"], timeout=15)
        assert caught.value is timeout
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [record["status"] for record in records] == ["timeout", "timeout"]


@pytest.mark.parametrize("failure", [False, True])
def test_formal_trace_write_failure_does_not_mask_outcome(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("MENTAT_ICEBERG_FORMAL_TRACE", str(tmp_path / "absent" / "trace"))
    result = subprocess.CompletedProcess(["lean"], 0)
    error = ValueError("original")
    def original(*args, **kwargs):
        if failure:
            raise error
        return result
    monkeypatch.setattr(project_formal, "_run", original)
    trace_formal_runs(monkeypatch)
    if failure:
        with pytest.raises(ValueError) as caught:
            project_formal._run(["lean"])
        assert caught.value is error
    else:
        assert project_formal._run(["lean"]) is result


def test_formal_trace_unset_leaves_runner_unchanged(monkeypatch):
    monkeypatch.delenv("MENTAT_ICEBERG_FORMAL_TRACE", raising=False)
    original = project_formal._run
    trace_formal_runs(monkeypatch)
    assert project_formal._run is original


def test_formal_trace_logs_error_without_changing_exception(tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("MENTAT_ICEBERG_FORMAL_TRACE", str(trace))
    error = OSError("original")
    def original(*args, **kwargs):
        raise error
    monkeypatch.setattr(project_formal, "_run", original)
    trace_formal_runs(monkeypatch)
    with pytest.raises(OSError) as caught:
        project_formal._run(["lean"])
    assert caught.value is error
    assert json.loads(trace.read_text())["status"] == "error"


def test_formal_trace_collection_failure_does_not_mask_result(monkeypatch, tmp_path):
    monkeypatch.setenv("MENTAT_ICEBERG_FORMAL_TRACE", str(tmp_path / "trace"))
    result = subprocess.CompletedProcess(["lean"], 0)
    monkeypatch.setattr(project_formal, "_run", lambda *args, **kwargs: result)
    monkeypatch.setattr(resource, "getrusage", lambda *_: (_ for _ in ()).throw(OSError("unavailable")))
    trace_formal_runs(monkeypatch)
    assert project_formal._run(["lean"]) is result
