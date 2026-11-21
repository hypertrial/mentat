"""Bounded child processes for the formal runner, behavior runner, Polaris Docker and Colima helpers.

Each child runs in its own session with stdin closed unless given input. A timeout kills
the whole process group, and so does the return from a call, so no group member survives it.
Git reads in project.py, the proof checker and Polaris formal.py still call subprocess directly.
"""

import os
import resource
import selectors
import signal
import subprocess
import tempfile
import time


def run(argv, *, output_limit, timeout, cwd=None, env=None, stdin=None, stderr=True):
    """Return (returncode, stdout, stderr) as bytes, with stdout and stderr read from pipes.

    `stdin` is a rewound binary file, and DEVNULL when None. A stream longer than
    `output_limit` raises RuntimeError. Past the deadline, TimeoutError is raised, or
    subprocess.TimeoutExpired if the child outlives its closed pipes. `stderr=False`
    discards stderr without capturing it.
    """
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL if stdin is None else stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE if stderr else subprocess.DEVNULL,
        start_new_session=True,
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
        try:
            _kill(process)
        finally:
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
            _kill(process)
        out.seek(0)
        err.seek(0)
        return process.returncode, out.read(), err.read()


def _kill(process):
    # Kill the whole group, which also stops descendants a finished child left running.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        # The group could not be signalled, so waiting could block forever on a live child.
        process.poll()
        raise
    process.wait()
