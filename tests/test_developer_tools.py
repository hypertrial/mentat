import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentat import project_formal, runner
from mentat.showcases.polaris import docker_runtime

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profile = load("profile_runtime")


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def patched():
    return (project_formal._run, docker_runtime.doctor, docker_runtime._stage_cli,
            docker_runtime._docker, runner._process, runner._docker, runner._stage)


def test_wrappers_forward_restore_and_hide_inputs(tmp_path, monkeypatch):
    path = tmp_path / "trace"
    trace = profile.Trace(str(path))
    result = object()
    arguments = []

    def original(*args, **kwargs):
        arguments.append((args, kwargs))
        return result

    monkeypatch.setattr(project_formal, "_run", original)
    originals = patched()
    with pytest.raises(ValueError, match="private"):
        with profile.profiling(trace):
            assert project_formal._run(["secret"], input="private") is result
            raise ValueError("private")
    assert arguments == [((["secret"],), {"input": "private"})]
    assert patched() == originals
    event, = records(path)
    assert set(event) == {"schema_version", "invocation_id", "sequence", "phase",
                          "elapsed_seconds", "outcome"}
    assert event["phase"] == "formal.other" and event["outcome"] == "success"
    assert "secret" not in path.read_text() and "private" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


def test_exception_and_failure_outcomes(tmp_path, monkeypatch):
    path = tmp_path / "trace"
    trace = profile.Trace(str(path))
    marker = RuntimeError("sensitive")

    def fail(*args, **kwargs):
        raise marker

    monkeypatch.setattr(docker_runtime, "_docker", fail)
    monkeypatch.setattr(docker_runtime, "doctor", lambda *a, **k: {"ok": False})
    with profile.profiling(trace):
        with pytest.raises(RuntimeError) as exc:
            docker_runtime._docker("secret-context", "create", "secret")
        assert exc.value is marker
        assert docker_runtime.doctor("secret-context") == {"ok": False}
    assert [(e["phase"], e["outcome"]) for e in records(path)] == [
        ("docker.create", "error"), ("polaris.preflight", "failure")]
    assert "sensitive" not in path.read_text()


def test_unsafe_destinations_and_absent_trace(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_text("unchanged")
    target.chmod(0o644)
    symlink = tmp_path / "link"
    symlink.symlink_to(target)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    for path in (None, "relative", str(target), str(symlink), str(fifo), str(tmp_path / "absent" / "file")):
        trace = profile.Trace(path)
        assert not trace.available()
        trace.emit("formal.other", 1, "success")
    assert target.read_text() == "unchanged"
    calls = []
    monkeypatch.setattr(project_formal, "_run", lambda *a, **k: calls.append(a) or object())
    with profile.profiling(profile.Trace(None)):
        project_formal._run([sys.executable, "-I", "-c", project_formal._Z3_WORKER])
    assert len(calls) == 1


def test_calibration_is_once_and_does_not_replace_worker(tmp_path, monkeypatch):
    trace = profile.Trace(str(tmp_path / "trace"))
    calls = []
    marker = SimpleNamespace(returncode=0)

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 2:
            raise OSError("private")
        return marker

    monkeypatch.setattr(project_formal, "_run", run)
    worker = [sys.executable, "-I", "-c", project_formal._Z3_WORKER]
    with profile.profiling(trace):
        assert project_formal._run(worker, input="private") is marker
        assert project_formal._run(worker, input="private") is marker
    assert len(calls) == 14
    assert calls[-2:] == [((worker,), {"input": "private"})] * 2
    events = records(tmp_path / "trace")
    assert len(events) == 12
    assert sum(e["outcome"] == "unavailable" for e in events) == 1
    assert [e["sequence"] for e in events] == list(range(1, 13))
    assert "private" not in (tmp_path / "trace").read_text()


def test_concurrent_process_append(tmp_path):
    path = tmp_path / "trace"
    code = ("import importlib.util,sys; "
            "s=importlib.util.spec_from_file_location('profile_runtime',sys.argv[1]); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "t=m.Trace(sys.argv[2]); "
            "[(t.emit('formal.other',1,'success') if t.sequence < 30 else None) "
            "for _ in range(100000)]; assert t.sequence == 30")

    def run(_):
        subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts/profile_runtime.py"),
                        str(path)], check=True, timeout=15)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, range(4)))
    events = records(path)
    assert len(events) == 120
    for identifier in {event["invocation_id"] for event in events}:
        assert [e["sequence"] for e in events if e["invocation_id"] == identifier] == list(range(1, 31))


def test_cli_exit_semantics_and_benchmark(tmp_path):
    env = {**os.environ, "MENTAT_PROFILE_TRACE": str(tmp_path / "trace")}
    for args in (["--help"], ["--definitely-invalid"]):
        direct = subprocess.run([sys.executable, "-c", 'from mentat.cli import app; app(prog_name="mentat")', *args],
                                capture_output=True, env=env)
        wrapped = subprocess.run([sys.executable, str(ROOT / "scripts/profile_runtime.py"), "--", *args],
                                 capture_output=True, env=env)
        assert wrapped.returncode == direct.returncode
        assert wrapped.stdout == direct.stdout and wrapped.stderr == direct.stderr
    benchmark = load("benchmark_openspec")
    result = benchmark.benchmark(1)
    assert result["capabilities"] == 1 and len(result["cat_file_counts"]) == 5
    assert all(count > 0 for count in result["cat_file_counts"])
    assert 0 <= result["min_seconds"] <= result["median_seconds"] <= result["max_seconds"]


@pytest.mark.parametrize("operation", ["open", "fstat", "flock", "write"])
@pytest.mark.parametrize("raises", [False, True])
def test_trace_io_failures_preserve_wrapped_result_and_exception(
    tmp_path, monkeypatch, operation, raises
):
    trace = profile.Trace(str(tmp_path / "trace"))
    marker = RuntimeError("original error")
    result = object()
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        if raises:
            raise marker
        return result

    monkeypatch.setattr(project_formal, "_run", original)
    module = profile.fcntl if operation == "flock" else profile.os

    def fail(*args, **kwargs):
        raise OSError("private trace failure")

    with profile.profiling(trace), monkeypatch.context() as patch:
        patch.setattr(module, operation, fail)
        if raises:
            with pytest.raises(RuntimeError) as caught:
                project_formal._run(["secret"])
            assert caught.value is marker
        else:
            assert project_formal._run(["secret"]) is result
    assert calls == [((["secret"],), {})]


@pytest.mark.parametrize("failure", ["timer_start", "timer_end", "emit"])
@pytest.mark.parametrize("raises", [False, True])
def test_instrumentation_failure_preserves_wrapped_result_and_exception(
    tmp_path, monkeypatch, failure, raises
):
    trace = profile.Trace(str(tmp_path / "trace"))
    marker = RuntimeError("original error")
    result = object()
    calls = []

    def original(*args, **kwargs):
        calls.append(1)
        if raises:
            raise marker
        return result

    def fail(*args, **kwargs):
        raise OSError("profiling unavailable")

    monkeypatch.setattr(project_formal, "_run", original)
    with profile.profiling(trace), monkeypatch.context() as patch:
        if failure == "emit":
            patch.setattr(trace, "emit", fail)
        else:
            ticks = iter([0, None] if failure == "timer_end" else [None])

            def timer():
                value = next(ticks)
                if value is None:
                    fail()
                return value

            patch.setattr(profile.time, "monotonic", timer)
        if raises:
            with pytest.raises(RuntimeError) as caught:
                project_formal._run(["secret"])
            assert caught.value is marker
        else:
            assert project_formal._run(["secret"]) is result
    assert calls == [1]


def test_calibration_unavailable_does_not_run_controls_or_change_worker(tmp_path, monkeypatch):
    trace = profile.Trace(str(tmp_path / "trace"))
    monkeypatch.setattr(trace, "available", lambda: False)
    calls = []
    marker = object()
    monkeypatch.setattr(project_formal, "_run", lambda *a, **k: calls.append((a, k)) or marker)
    worker = [sys.executable, "-I", "-c", project_formal._Z3_WORKER]
    with profile.profiling(trace):
        assert project_formal._run(worker, input="private") is marker
    assert calls == [((worker,), {"input": "private"})]
    assert [event["phase"] for event in records(tmp_path / "trace")] == ["formal.z3_worker"]


@pytest.mark.parametrize(
    "args,phase",
    [
        (["lean", "--version"], "formal.lean_version"),
        (["lean", "--print-libdir"], "formal.lean_libdir"),
        (["lean", "--deps", "secret"], "formal.lean_deps"),
        (["lean", "-o", "secret"], "formal.lean_compile"),
        (["lean", "--run", "secret"], "formal.lean_audit"),
        (["private-command"], "formal.other"),
    ],
)
def test_formal_trace_uses_static_phase_names(tmp_path, monkeypatch, args, phase):
    trace = profile.Trace(str(tmp_path / "trace"))
    monkeypatch.setattr(project_formal, "_run", lambda *_: subprocess.CompletedProcess(args, 1))
    with profile.profiling(trace):
        assert project_formal._run(args).returncode == 1
    event, = records(tmp_path / "trace")
    assert (event["phase"], event["outcome"]) == (phase, "failure")
    assert "secret" not in (tmp_path / "trace").read_text()
    assert "private-command" not in (tmp_path / "trace").read_text()


def test_trace_rejects_file_owned_by_another_user(tmp_path, monkeypatch):
    path = tmp_path / "trace"
    trace = profile.Trace(str(path))
    original = profile.os.fstat

    def wrong_owner(fd):
        info = original(fd)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=profile.os.geteuid() + 1)

    with monkeypatch.context() as patch:
        patch.setattr(profile.os, "fstat", wrong_owner)
        assert not trace.available()
        trace.emit("formal.other", 1, "success")
    assert path.read_bytes() == b""


@pytest.mark.parametrize(
    "args,phase",
    [
        (("context", "network", "create", "private"), "docker.network_create"),
        (("context", "network", "rm", "private"), "docker.network_rm"),
        (("context", "secret-operation", "private"), "docker.other"),
        (("context", "network", "secret-operation"), "docker.other"),
    ],
)
def test_docker_trace_uses_static_phase_names(tmp_path, monkeypatch, args, phase):
    trace = profile.Trace(str(tmp_path / "trace"))
    monkeypatch.setattr(docker_runtime, "_docker", lambda *args: (1, b"private"))
    with profile.profiling(trace):
        assert docker_runtime._docker(*args) == (1, b"private")
    event, = records(tmp_path / "trace")
    assert (event["phase"], event["outcome"]) == (phase, "failure")
    assert "private" not in (tmp_path / "trace").read_text()
    assert "secret-operation" not in (tmp_path / "trace").read_text()


def test_runner_wrappers_forward_arguments_and_restore(tmp_path, monkeypatch):
    trace = profile.Trace(str(tmp_path / "trace"))
    calls = []
    marker = object()
    monkeypatch.setattr(runner, "_process", lambda *a, **k: calls.append((a, k)) or marker)
    monkeypatch.setattr(runner, "_stage", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(runner, "_docker", lambda *a, **k: calls.append((a, k)) or marker)
    originals = patched()
    with profile.profiling(trace):
        assert runner._process(["secret"], timeout=5, input_file="private") is marker
        assert runner._stage("private", ["secret"], trusted=True) is None
        assert runner._docker("secret-context", "cp", "private", timeout=7) is marker
    assert calls == [
        ((["secret"],), {"timeout": 5, "input_file": "private"}),
        (("private", ["secret"]), {"trusted": True}),
        (("secret-context", "cp", "private"), {"timeout": 7}),
    ]
    assert patched() == originals
    assert [e["phase"] for e in records(tmp_path / "trace")] == [
        "runner.process", "runner.stage", "runner.docker.cp"]
    assert "private" not in (tmp_path / "trace").read_text()


@pytest.mark.parametrize(
    "call,phase,outcome",
    [
        (lambda: runner._process(["secret"], cwd="private", env={"TOKEN": "private"}),
         "runner.process", "failure"),
        (lambda: runner._stage(Path("private"), [("secret", (0o644, b"private"))], trusted=True),
         "runner.stage", "success"),
        (lambda: runner._docker("secret-context", "create", "private"),
         "runner.docker.create", "failure"),
        (lambda: runner._docker("secret-context", "context", "inspect", "private"),
         "runner.docker.context", "failure"),
        (lambda: runner._docker("secret-context", "secret-operation", "private"),
         "runner.docker.other", "failure"),
    ],
)
def test_runner_trace_uses_static_phase_names(tmp_path, monkeypatch, call, phase, outcome):
    trace = profile.Trace(str(tmp_path / "trace"))
    monkeypatch.setattr(runner, "_process", lambda *a, **k: (1, b"private", b""))
    monkeypatch.setattr(runner, "_stage", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_docker", lambda *a, **k: (1, b"private", b""))
    with profile.profiling(trace):
        call()
    event, = records(tmp_path / "trace")
    assert (event["phase"], event["outcome"]) == (phase, outcome)
    assert "private" not in (tmp_path / "trace").read_text()
    assert "secret" not in (tmp_path / "trace").read_text()


@pytest.mark.parametrize("cancellation", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("stage", ["calibration", "emission", "target"])
def test_cancellation_propagates_and_restores_wrappers(tmp_path, monkeypatch, cancellation, stage):
    trace = profile.Trace(str(tmp_path / "trace"))
    marker = cancellation("cancelled")
    worker = [sys.executable, "-I", "-c", project_formal._Z3_WORKER]
    calls = []

    def original(*args, **kwargs):
        calls.append(args[0])
        if stage in {"calibration", "target"}:
            raise marker
        return object()

    def cancel(*args, **kwargs):
        raise marker

    monkeypatch.setattr(project_formal, "_run", original)
    if stage == "emission":
        monkeypatch.setattr(trace, "emit", cancel)
    originals = patched()
    with pytest.raises(cancellation) as caught:
        with profile.profiling(trace):
            project_formal._run(worker if stage == "calibration" else ["other"])
    assert caught.value is marker
    if stage == "calibration":
        assert calls == [[sys.executable, "-I", "-c", "pass"]]
    else:
        assert calls == [["other"]]
    assert patched() == originals


@pytest.mark.parametrize("cancellation", [KeyboardInterrupt, SystemExit])
def test_trace_write_cancellation_propagates(tmp_path, monkeypatch, cancellation):
    trace = profile.Trace(str(tmp_path / "trace"))
    marker = cancellation("cancelled")

    def cancel(*args, **kwargs):
        raise marker

    with monkeypatch.context() as patch:
        patch.setattr(profile.os, "write", cancel)
        with pytest.raises(cancellation) as caught:
            trace.emit("formal.other", 0, "success")
    assert caught.value is marker


@pytest.mark.parametrize("raises", [False, True])
def test_contended_trace_lock_never_blocks_target_or_writes_event(tmp_path, raises):
    path = tmp_path / "trace"
    path.touch(mode=0o600)
    holder_code = (
        "import fcntl,sys; f=open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX); "
        "print('locked', flush=True); sys.stdin.read()"
    )
    target_code = """
import importlib.util, sys
from mentat import project_formal
spec = importlib.util.spec_from_file_location('profiler', sys.argv[1])
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)
marker = RuntimeError('target error')
def target(*args):
    if sys.argv[3] == 'raises':
        raise marker
    return 'target result'
project_formal._run = target
with profiler.profiling(profiler.Trace(sys.argv[2])):
    try:
        assert project_formal._run(['ordinary']) == 'target result'
    except RuntimeError as exc:
        assert exc is marker
        print('exception preserved')
    else:
        print('result preserved')
"""
    holder = subprocess.Popen([sys.executable, "-c", holder_code, str(path)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        result = subprocess.run(
            [sys.executable, "-c", target_code, str(ROOT / "scripts/profile_runtime.py"),
             str(path), "raises" if raises else "returns"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        assert result.stdout.strip() == ("exception preserved" if raises else "result preserved")
        assert path.read_bytes() == b""
    finally:
        holder.stdin.close()
        holder.wait(timeout=5)
