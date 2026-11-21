"""Bounded child processes: stdin is never inherited and output is capped by the caller."""

import os
import resource
import selectors
import signal
import subprocess
import tempfile
import time


def run(argv, *, output_limit, timeout, cwd=None, env=None, stdin=None, stderr=True, session=True):
    """Return (returncode, stdout, stderr) as bytes, with stdout and stderr read from pipes.

    `stdin` is a rewound binary file, and DEVNULL when None. A stream longer than
    `output_limit` raises RuntimeError. Past the deadline, TimeoutError is raised, or
    subprocess.TimeoutExpired if the child outlives its closed pipes. `stderr=False`
    discards stderr without capturing it. `session=False` kills only the child.
    """
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL if stdin is None else stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE if stderr else subprocess.DEVNULL,
        start_new_session=session,
    )
    streams = {process.stdout: bytearray()}
    if stderr:
        streams[process.stderr] = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for stream in streams:
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("execution timed out")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        data = streams[key.fileobj]
                        data.extend(chunk)
                        if len(data) > output_limit:
                            raise RuntimeError("execution output exceeded limit")
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            return code, bytes(streams[process.stdout]), bytes(streams.get(process.stderr, b""))
    finally:
        _kill(process, session)
        for stream in streams:
            stream.close()


def run_to_files(argv, *, file_size_limit, timeout, cwd=None, env=None, stdin=None):
    """Return (returncode, stdout, stderr) as bytes, with output written to temporary files.

    Writes past `file_size_limit` bytes (RLIMIT_FSIZE) fail inside the child, and the run
    still returns what was written. Timeouts raise subprocess.TimeoutExpired.
    """

    def limit():
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_size_limit, file_size_limit))

    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL if stdin is None else stdin,
            stdout=out,
            stderr=err,
            preexec_fn=limit,
            start_new_session=True,
        )
        try:
            process.communicate(timeout=timeout)
        finally:
            _kill(process, True)
        out.seek(0)
        err.seek(0)
        return process.returncode, out.read(), err.read()


def _kill(process, session):
    # Kill the whole session so a descendant cannot outlive a timeout or a finished read.
    if session:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()
    process.wait()
