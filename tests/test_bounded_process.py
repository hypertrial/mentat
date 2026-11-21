"""Characterization of the bounded subprocess helpers (WORK-54).

Behavior table, recorded on the code before the shared runner (`mentat.process`) existed.

| Helper | Return | Errors (type: message) | Timeout kill | Output cap | stdin | Environment | cwd |
|---|---|---|---|---|---|---|---|
| project_formal._run | CompletedProcess(args, rc, str, str) | subprocess.TimeoutExpired("Command ... timed out after N seconds"); OSError from Popen | process group, SIGKILL | temp files; child writes capped at 1,000,000 bytes (RLIMIT_FSIZE); returned text is the first 16,384 bytes, decoded utf-8 with replacement | DEVNULL, or input text (encoded) | `_env()`: PATH from os.environ, HOME = system temp dir, LANG = C.UTF-8; explicit env used verbatim | operator cwd unless given |
| runner._process | (rc, stdout bytes, stderr bytes) | RuntimeError("execution output exceeded limit") per stream; TimeoutError("execution timed out") while reading; subprocess.TimeoutExpired if the process outlives its closed pipes past the deadline; OSError from Popen | process group, SIGKILL | pipes; each stream capped at output_limit (default 1 MiB) and raises when exceeded | DEVNULL, or input_file | env None inherits os.environ; explicit env used verbatim | inherited unless given |
| runner._docker | `_process` result, unchanged | ValueError("Docker command failed: {exc}") for OSError, SubprocessError, TimeoutError, RuntimeError | via `_process` | via `_process` | via `_process` | inherited | inherited |
| docker_runtime._docker | (rc, stdout bytes) | ValueError("an explicit dedicated Docker context is required"); RuntimeError("Docker output exceeded limit"); RuntimeError("Docker command timed out"); RuntimeError("Docker command unavailable or timed out") for OSError or a wait timeout | direct child only (no new session, pid SIGKILL; WORK-54 CHANGE: process group) | stdout pipe, 1 MiB cap; stderr discarded, never captured or capped | DEVNULL | inherited | inherited |
| colima.validate_colima | ValueError messages below; subprocess.run results | ValueError("Mentat requires a named, dedicated Colima profile"), ValueError("Mentat requires a dedicated local Unix-socket Docker context"), ValueError("Docker context does not match the selected Colima profile"), ValueError("The dedicated Colima VM must have host mounts disabled"); subprocess.CalledProcessError (check=True); subprocess.TimeoutExpired (10 s or 15 s); OSError | direct child only (subprocess.run kill) | uncapped capture_output | inherited from the operator (WORK-54 CHANGE: DEVNULL) | inherited | inherited |

The table records the code before WORK-54. The intended WORK-54 changes are asserted in place
and marked `WORK-54 CHANGE`: Colima stdin is DEVNULL, Colima timeouts kill the process group,
Colima output is capped at 1 MiB per stream (overflow is a ValueError), Polaris Docker timeouts
kill the process group, and formal runs use a private 0700 directory as HOME and cwd. Everything else in the table is unchanged.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from mentat import colima, process, project_formal, runner
from mentat.showcases.polaris import docker_runtime

# A grandchild that outlives its parent unless the whole process group is killed.
GRANDCHILD_SH = "sleep 60 >/dev/null 2>&1 & echo $! > '{pidfile}'; wait"


def fake(bin_dir, name, body):
    script = bin_dir / name
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(0o755)
    return script


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    path = tmp_path / "bin"
    path.mkdir()
    monkeypatch.setenv("PATH", f"{path}{os.pathsep}{os.environ['PATH']}")
    return path


def read_pid(path):
    deadline = time.monotonic() + 5
    while not path.exists() or not path.read_text().strip():
        assert time.monotonic() < deadline, "grandchild never started"
        time.sleep(0.05)
    return int(path.read_text())


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0) until its parent reaps it, but it has already exited.
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def gone_soon(pid, seconds=5):
    deadline = time.monotonic() + seconds
    while alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


@pytest.fixture
def grandchild(tmp_path):
    """Yields a pid file path; kills any grandchild that a test leaves running."""
    pidfile = tmp_path / "grandchild.pid"
    yield pidfile
    if pidfile.exists() and pidfile.read_text().strip():
        try:
            os.kill(int(pidfile.read_text()), signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass


def child(body, *, stdin=b"INHERITED", env=None):
    """Run `body` in a fresh interpreter whose stdin carries 'INHERITED'; it must print JSON."""
    code = (
        "import json, os, subprocess, sys, tempfile\n"
        "from mentat import colima, project_formal, runner\n"
        "from mentat.showcases.polaris import docker_runtime\n" + body
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        input=stdin,
        capture_output=True,
        env=env or os.environ.copy(),
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return json.loads(completed.stdout)


# project_formal._run ---------------------------------------------------------


def test_formal_run_returns_completed_process_with_text_output():
    result = project_formal._run(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"]
    )
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 0
    assert result.stdout == "out\n" and result.stderr == "err\n"
    assert isinstance(result.stdout, str)


def test_formal_run_returns_nonzero_exit_without_raising():
    result = project_formal._run([sys.executable, "-c", "raise SystemExit(7)"])
    assert result.returncode == 7


def test_formal_run_truncates_returned_text_and_replaces_invalid_utf8(tmp_path):
    code = "import sys; sys.stdout.buffer.write(b'\\xff' + b'a' * 20000)"
    result = project_formal._run([sys.executable, "-c", code], cwd=tmp_path)
    # The cap applies to raw bytes; the one invalid byte decodes to U+FFFD.
    assert result.stdout == "\ufffd" + "a" * 16383


def test_formal_run_caps_child_file_writes_at_one_million_bytes(tmp_path):
    code = "open('big.bin', 'wb').write(b'x' * 2000000)"
    result = project_formal._run([sys.executable, "-c", code], cwd=tmp_path)
    assert result.returncode != 0
    assert (tmp_path / "big.bin").stat().st_size <= 1_000_000


def test_formal_run_timeout_raises_timeout_expired_and_kills_group(tmp_path, grandchild):
    args = ["sh", "-c", GRANDCHILD_SH.format(pidfile=grandchild)]
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        project_formal._run(args, timeout=1, cwd=tmp_path)
    assert time.monotonic() - started < 20
    # communicate() reports the remaining time, so only the prefix is stable.
    assert str(caught.value).startswith(f"Command '{args}' timed out after ")
    assert gone_soon(read_pid(grandchild))


def test_formal_run_missing_executable_raises_oserror(tmp_path):
    with pytest.raises(FileNotFoundError) as caught:
        project_formal._run([str(tmp_path / "missing-tool")])
    assert (
        str(caught.value) == f"[Errno 2] No such file or directory: '{tmp_path / 'missing-tool'}'"
    )


def test_formal_run_input_is_stdin_and_stdin_is_never_inherited():
    payload = child(
        "out = {\n"
        "  'none': project_formal._run([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))']).stdout,\n"
        "  'input': project_formal._run([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'], input='private').stdout,\n"
        "}\n"
        "print(json.dumps(out))"
    )
    assert payload == {"none": "''\n", "input": "'private'\n"}


def test_formal_run_default_environment_uses_a_fresh_private_home_and_cwd(tmp_path, monkeypatch):
    # WORK-54 CHANGE: HOME and the default cwd are one fresh 0700 directory, removed afterwards.
    monkeypatch.setenv("UNDECLARED_SECRET", "sentinel")
    monkeypatch.chdir(tmp_path)
    code = (
        "import os, stat; home = os.environ['HOME']; print(home); "
        "print(os.path.samefile(os.getcwd(), home)); "
        "print(stat.S_IMODE(os.stat(home).st_mode)); "
        "print(os.environ['LANG']); print(os.environ.get('UNDECLARED_SECRET'))"
    )
    home, same, mode, lang, secret = project_formal._run(
        [sys.executable, "-c", code]
    ).stdout.splitlines()
    assert Path(home).name.startswith("mentat-formal-")
    assert Path(home).parent.samefile(tempfile.gettempdir())
    assert (same, mode, lang, secret) == ("True", "448", "C.UTF-8", "None")  # mode 0o700
    assert not Path(home).exists()


def test_formal_run_explicit_env_is_used_verbatim(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDECLARED_SECRET", "sentinel")
    env = {"PATH": os.environ["PATH"], "MARK": "1"}
    code = "import os; print(os.environ['MARK'], os.environ.get('UNDECLARED_SECRET'), os.environ.get('HOME'))"
    result = project_formal._run([sys.executable, "-c", code], env=env, cwd=tmp_path)
    assert result.stdout == "1 None None\n"


def test_formal_run_file_size_limit_kills_the_child_with_sigxfsz(tmp_path):
    # `exec` makes head the direct child, so its status is the signal that stopped it.
    result = project_formal._run(
        ["sh", "-c", "exec head -c 2000000 /dev/zero > big.bin"], cwd=tmp_path
    )
    assert result.returncode == -signal.SIGXFSZ
    assert (tmp_path / "big.bin").stat().st_size == 1_000_000


def test_formal_run_timeout_removes_the_private_directory(tmp_path):
    home_file = tmp_path / "home.txt"
    args = ["sh", "-c", f"printf %s \"$HOME\" > '{home_file}'; sleep 60"]
    with pytest.raises(subprocess.TimeoutExpired):
        project_formal._run(args, timeout=1)
    home = Path(home_file.read_text())
    assert home.name.startswith("mentat-formal-")
    assert not home.exists()


# runner._process -------------------------------------------------------------


def test_process_returns_code_and_bytes_per_stream():
    code, out, err = runner._process(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('o'); sys.stderr.write('e'); sys.exit(3)",
        ]
    )
    assert (code, out, err) == (3, b"o", b"e")
    assert isinstance(out, bytes) and isinstance(err, bytes)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_process_caps_each_stream_by_default(stream):
    write = f"import sys; sys.{stream}.write('x' * ({1024 * 1024} + 1))"
    with pytest.raises(RuntimeError) as caught:
        runner._process([sys.executable, "-c", write])
    assert str(caught.value) == "execution output exceeded limit"


def test_process_respects_an_explicit_output_limit():
    with pytest.raises(RuntimeError, match="^execution output exceeded limit$"):
        runner._process([sys.executable, "-c", "print('x' * 200)"], output_limit=100)
    code, out, _ = runner._process([sys.executable, "-c", "print('x' * 20)"], output_limit=100)
    assert (code, out) == (0, b"x" * 20 + b"\n")


def test_process_deadline_raises_timeout_error_and_kills_group(grandchild):
    args = ["sh", "-c", GRANDCHILD_SH.format(pidfile=grandchild)]
    with pytest.raises(TimeoutError) as caught:
        runner._process(args, timeout=1)
    assert str(caught.value) == "execution timed out"
    assert gone_soon(read_pid(grandchild))


def test_process_timeout_after_both_pipes_close_raises_subprocess_timeout():
    code = "import os, time; os.close(1); os.close(2); time.sleep(60)"
    with pytest.raises(subprocess.TimeoutExpired):
        runner._process([sys.executable, "-c", code], timeout=1)


def test_process_timeout_with_stderr_still_open_raises_timeout_error():
    code = "import os, time; os.close(1); time.sleep(60)"
    with pytest.raises(TimeoutError) as caught:
        runner._process([sys.executable, "-c", code], timeout=1)
    assert str(caught.value) == "execution timed out"


def test_process_cleanup_runs_when_killpg_fails(monkeypatch):
    # Item 3: a failing killpg must not skip the child's wait or the pipe closing.
    created = []
    original = subprocess.Popen

    def recording(*args, **kwargs):
        created.append(original(*args, **kwargs))
        return created[-1]

    def fail(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(process.subprocess, "Popen", recording)
    monkeypatch.setattr(process.os, "killpg", fail)
    with pytest.raises(PermissionError):
        process.run([sys.executable, "-c", "pass"], output_limit=100, timeout=10)
    (child,) = created
    assert child.returncode is not None
    assert child.stdout.closed and child.stderr.closed


def test_process_does_not_wait_forever_when_a_live_child_cannot_be_killed(monkeypatch):
    created = []
    original = subprocess.Popen

    def recording(*args, **kwargs):
        created.append(original(*args, **kwargs))
        return created[-1]

    def fail(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(process.subprocess, "Popen", recording)
    monkeypatch.setattr(process.os, "killpg", fail)
    started = time.monotonic()
    try:
        with pytest.raises(PermissionError):
            process.run([sys.executable, "-c", "import time; time.sleep(30)"], output_limit=100, timeout=0.5)
        assert time.monotonic() - started < 10
    finally:
        for child in created:
            child.kill()
            child.wait()


def test_process_missing_executable_raises_oserror(tmp_path):
    with pytest.raises(FileNotFoundError):
        runner._process([str(tmp_path / "missing-tool")])


def test_process_stdin_is_devnull_unless_input_file_is_given():
    payload = child(
        "import tempfile\n"
        "none = runner._process([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'])[1]\n"
        "with tempfile.TemporaryFile() as f:\n"
        "    f.write(b'private'); f.seek(0)\n"
        "    given = runner._process([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'], input_file=f)[1]\n"
        "print(json.dumps([none.decode(), given.decode()]))"
    )
    assert payload == ["''\n", "'private'\n"]


def test_process_inherits_environment_and_cwd_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("MENTAT_CHARACTERIZATION", "inherited")
    monkeypatch.chdir(tmp_path)
    code = "import os; print(os.environ['MENTAT_CHARACTERIZATION'], os.getcwd())"
    _, out, _ = runner._process([sys.executable, "-c", code])
    assert out.decode() == f"inherited {tmp_path}\n"


def test_process_explicit_env_and_cwd_are_used(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDECLARED_SECRET", "sentinel")
    code = "import os; print(os.environ.get('UNDECLARED_SECRET'), os.getcwd())"
    _, out, _ = runner._process(
        [sys.executable, "-c", code], env={"PATH": os.environ["PATH"]}, cwd=tmp_path
    )
    assert out.decode() == f"None {tmp_path}\n"


# runner._docker --------------------------------------------------------------


def test_docker_adapter_calls_process_global_with_docker_argv(monkeypatch):
    calls = []

    def process(argv, **kwargs):
        calls.append((argv, kwargs))
        return 0, b"out", b"err"

    monkeypatch.setattr(runner, "_process", process)
    assert runner._docker("ctx", "info", timeout=5) == (0, b"out", b"err")
    assert calls == [
        (
            ["docker", "--context", "ctx", "info"],
            {"timeout": 5, "input_file": None, "output_limit": runner._MAX_OUTPUT},
        )
    ]


@pytest.mark.parametrize(
    "failure, detail",
    [
        (TimeoutError("execution timed out"), "execution timed out"),
        (RuntimeError("execution output exceeded limit"), "execution output exceeded limit"),
        (subprocess.SubprocessError("boom"), "boom"),
        (OSError("unavailable"), "unavailable"),
    ],
)
def test_docker_adapter_wraps_failures_as_value_error(monkeypatch, failure, detail):
    def process(argv, **kwargs):
        raise failure

    monkeypatch.setattr(runner, "_process", process)
    with pytest.raises(ValueError) as caught:
        runner._docker("ctx", "info")
    assert str(caught.value) == f"Docker command failed: {detail}"
    assert caught.value.__cause__ is failure


def test_docker_real_missing_binary_is_value_error(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ValueError) as caught:
        runner._docker("ctx", "info")
    assert (
        str(caught.value) == "Docker command failed: [Errno 2] No such file or directory: 'docker'"
    )


def test_docker_real_timeout_is_value_error(bin_dir, grandchild):
    fake(bin_dir, "docker", GRANDCHILD_SH.format(pidfile=grandchild))
    with pytest.raises(ValueError) as caught:
        runner._docker("ctx", "info", timeout=1)
    assert str(caught.value) == "Docker command failed: execution timed out"
    assert gone_soon(read_pid(grandchild))


# docker_runtime._docker (Polaris) --------------------------------------------


@pytest.mark.parametrize("context", ["", "default", "bad context!"])
def test_polaris_docker_requires_an_explicit_context(context):
    with pytest.raises(ValueError) as caught:
        docker_runtime._docker(context, "info")
    assert str(caught.value) == "an explicit dedicated Docker context is required"


def test_polaris_docker_returns_code_and_stdout_and_discards_stderr(bin_dir):
    fake(bin_dir, "docker", "printf out; head -c 2000000 /dev/zero | tr '\\0' e >&2")
    result = docker_runtime._docker("ctx", "info")
    assert result == (0, b"out")


def test_polaris_docker_caps_stdout_at_one_mebibyte(bin_dir):
    fake(bin_dir, "docker", "head -c 1100000 /dev/zero | tr '\\0' x")
    with pytest.raises(RuntimeError) as caught:
        docker_runtime._docker("ctx", "info")
    assert str(caught.value) == "Docker output exceeded limit"


def test_polaris_docker_timeout_raises_runtime_error(bin_dir, grandchild):
    fake(bin_dir, "docker", GRANDCHILD_SH.format(pidfile=grandchild))
    with pytest.raises(RuntimeError) as caught:
        docker_runtime._docker("ctx", "info", timeout=1)
    assert str(caught.value) == "Docker command timed out"


def test_polaris_docker_timeout_kills_the_process_group(bin_dir, grandchild):
    # WORK-54 CHANGE: Polaris Docker calls kill the whole group on timeout, like the others.
    fake(bin_dir, "docker", GRANDCHILD_SH.format(pidfile=grandchild))
    with pytest.raises(RuntimeError):
        docker_runtime._docker("ctx", "info", timeout=1)
    assert gone_soon(read_pid(grandchild))


def test_polaris_docker_missing_binary_raises_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError) as caught:
        docker_runtime._docker("ctx", "info")
    assert str(caught.value) == "Docker command unavailable or timed out"


def test_polaris_docker_stdin_is_devnull_and_environment_inherited(bin_dir, monkeypatch, tmp_path):
    out = tmp_path / "seen"
    fake(bin_dir, "docker", f"cat > '{out}'; printf '%s' \"$MENTAT_CHARACTERIZATION\"")
    monkeypatch.setenv("MENTAT_CHARACTERIZATION", "inherited")
    payload = child(
        "print(json.dumps(docker_runtime._docker('ctx', 'info')[1].decode()))",
        env={**os.environ, "PATH": os.environ["PATH"]},
    )
    assert payload == "inherited"
    assert out.read_bytes() == b""


# colima.validate_colima -------------------------------------------------------


@pytest.mark.parametrize("context", ["default", "colima", "colima-bad name", "docker-desktop"])
def test_colima_rejects_unnamed_profiles(context):
    with pytest.raises(ValueError) as caught:
        colima.validate_colima(context)
    assert str(caught.value) == "Mentat requires a named, dedicated Colima profile"


def test_colima_rejects_non_unix_context(bin_dir):
    fake(bin_dir, "docker", "printf 'tcp://127.0.0.1:2375'")
    with pytest.raises(ValueError) as caught:
        colima.validate_colima("colima-test")
    assert str(caught.value) == "Mentat requires a dedicated local Unix-socket Docker context"


def test_colima_inspect_failure_is_the_same_user_error(bin_dir):
    fake(bin_dir, "docker", "exit 1")
    with pytest.raises(ValueError, match="dedicated local Unix-socket Docker context"):
        colima.validate_colima("colima-test")


def _colima_ok(
    bin_dir, status="printf '%s' '{\"docker_socket\": \"unix:///x.sock\"}'", ssh="printf unshared"
):
    fake(bin_dir, "docker", "if [ \"$3\" = context ]; then printf 'unix:///x.sock'; fi")
    fake(bin_dir, "colima", f'case "$1" in status) {status};; --profile) {ssh};; esac')


def test_colima_accepts_a_matching_unshared_profile(bin_dir):
    _colima_ok(bin_dir)
    assert colima.validate_colima("colima-test") is None


def test_colima_socket_mismatch_is_value_error(bin_dir):
    _colima_ok(bin_dir, status="printf '%s' '{\"docker_socket\": \"unix:///other.sock\"}'")
    with pytest.raises(ValueError) as caught:
        colima.validate_colima("colima-test")
    assert str(caught.value) == "Docker context does not match the selected Colima profile"


def test_colima_mounted_vm_is_value_error(bin_dir):
    _colima_ok(bin_dir, ssh="printf mounted")
    with pytest.raises(ValueError) as caught:
        colima.validate_colima("colima-test")
    assert str(caught.value) == "The dedicated Colima VM must have host mounts disabled"


def test_colima_status_failure_raises_called_process_error(bin_dir):
    _colima_ok(bin_dir, status="exit 3")
    with pytest.raises(subprocess.CalledProcessError) as caught:
        colima.validate_colima("colima-test")
    assert caught.value.returncode == 3
    assert str(caught.value) == str(
        subprocess.CalledProcessError(3, ["colima", "status", "test", "--json"])
    )


def test_colima_missing_binary_raises_oserror(bin_dir, monkeypatch):
    fake(bin_dir, "docker", "if [ \"$3\" = context ]; then printf 'unix:///x.sock'; fi")
    monkeypatch.setenv("PATH", str(bin_dir))  # no real colima on the search path
    with pytest.raises(FileNotFoundError):
        colima.validate_colima("colima-test")


def test_colima_stdin_is_never_inherited(bin_dir):
    # WORK-54 CHANGE: the probes read DEVNULL instead of the operator's stdin.
    seen = bin_dir.parent / "seen"
    fake(
        bin_dir,
        "docker",
        f"cat >> '{seen}'; if [ \"$3\" = context ]; then printf 'unix:///x.sock'; fi",
    )
    fake(
        bin_dir,
        "colima",
        'case "$1" in status) printf \'%s\' \'{"docker_socket": "unix:///x.sock"}\';; --profile) printf unshared;; esac',
    )
    payload = child(
        "colima.validate_colima('colima-test'); print(json.dumps('ok'))",
        env={**os.environ, "PATH": os.environ["PATH"]},
    )
    assert payload == "ok"
    assert seen.read_bytes() == b""


def test_colima_timeout_raises_timeout_expired_and_kills_group(bin_dir, grandchild):
    # WORK-54 CHANGE: the timeout kills the process group, so the grandchild stops.
    fake(bin_dir, "docker", GRANDCHILD_SH.format(pidfile=grandchild))
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        colima.validate_colima("colima-test")
    assert time.monotonic() - started < 40
    assert gone_soon(read_pid(grandchild))


def test_colima_stderr_flood_is_a_value_error(bin_dir):
    # WORK-54 CHANGE: each stream is capped at 1 MiB, so stderr overflow is also a ValueError.
    fake(bin_dir, "docker", "head -c 1100000 /dev/zero | tr '\\0' x >&2")
    with pytest.raises(ValueError) as caught:
        colima.validate_colima("colima-test")
    assert str(caught.value) == "Colima validation output exceeded limit"


def test_colima_output_flood_is_a_value_error(bin_dir):
    # WORK-54 CHANGE: Colima probes are capped at 1 MiB, and overflow is a user error.
    fake(bin_dir, "docker", "head -c 1100000 /dev/zero | tr '\\0' x")
    with pytest.raises(ValueError) as caught:
        colima.validate_colima("colima-test")
    assert str(caught.value) == "Colima validation output exceeded limit"
