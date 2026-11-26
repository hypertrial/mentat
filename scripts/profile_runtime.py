#!/usr/bin/env python3
"""Profile Mentat boundaries to a private JSONL file.

Timings are inclusive, so nested Docker events overlap preflight. Z3 controls
include process startup, IPC, scheduling and teardown; they are not solve times.
Tracing and calibration are best effort and do not affect command results.
"""

import fcntl
import json
import os
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

from mentat import project_formal, runner
from mentat.cli import app
from mentat.showcases.polaris import docker_runtime


class Trace:
    def __init__(self, path):
        self.path = path
        self.invocation_id = str(uuid4())
        self.sequence = 0
        self.lock = threading.Lock()
        self.calibrated = False

    def available(self):
        if not self.path or not os.path.isabs(self.path) or not hasattr(os, "O_NOFOLLOW"):
            return False
        try:
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
            flags |= getattr(os, "O_NONBLOCK", 0)
            fd = os.open(self.path, flags, 0o600)
            try:
                info = os.fstat(fd)
                return (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                        and not info.st_mode & 0o077)
            finally:
                os.close(fd)
        except OSError:
            return False

    def emit(self, phase, elapsed, outcome):
        try:
            if not self.path or not os.path.isabs(self.path):
                return
            with self.lock:
                flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
                flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                # Without no-follow, a symlink cannot safely be opened for append.
                if not hasattr(os, "O_NOFOLLOW"):
                    return
                fd = os.open(self.path, flags, 0o600)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                        return
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.sequence += 1
                    record = {"schema_version": 1, "invocation_id": self.invocation_id,
                              "sequence": self.sequence, "phase": phase,
                              "elapsed_seconds": elapsed, "outcome": outcome}
                    data = (json.dumps(record, separators=(",", ":")) + "\n").encode()
                    while data:
                        written = os.write(fd, data)
                        if not written:
                            break
                        data = data[written:]
                finally:
                    os.close(fd)
        except Exception:
            pass

    def calibrate(self, original, executable):
        if self.calibrated or not self.available():
            return
        self.calibrated = True
        for phase, code in (("calibration.noop", "pass"), ("calibration.import_z3", "import z3")):
            for sample in range(6):
                started = time.monotonic()
                outcome = "success"
                try:
                    result = original([executable, "-I", "-c", code])
                    if result.returncode:
                        outcome = "unavailable"
                except Exception:
                    outcome = "unavailable"
                if sample:
                    self.emit(phase, time.monotonic() - started, outcome)


def formal_phase(args):
    if len(args) >= 4 and args[1:3] == ["-I", "-c"] and args[3] == project_formal._Z3_WORKER:
        return "formal.z3_worker"
    if "--version" in args:
        return "formal.lean_version"
    if "--print-libdir" in args:
        return "formal.lean_libdir"
    if "--deps" in args:
        return "formal.lean_deps"
    if "-o" in args:
        return "formal.lean_compile"
    if "--run" in args:
        return "formal.lean_audit"
    return "formal.other"


def docker_phase(args):
    operation = args[1] if len(args) > 1 else ""
    if operation == "network" and len(args) > 2:
        operation = "network_" + args[2]
    allowed = {"version", "info", "inspect", "image", "context", "create", "cp", "start", "wait",
               "logs", "rm", "network_create", "network_rm"}
    return "docker." + (operation if operation in allowed else "other")


@contextmanager
def profiling(trace):
    originals = (project_formal._run, docker_runtime.doctor, docker_runtime._stage_cli,
                 docker_runtime._docker, runner._process, runner._docker, runner._stage,
                 runner._stage_archive)

    def wrap(original, phase_for):
        def traced(*args, **kwargs):
            try:
                phase = phase_for(args)
            except Exception:
                phase = "unknown"
            if phase == "formal.z3_worker":
                try:
                    trace.calibrate(original, args[0][0])
                except Exception:
                    pass
            try:
                started = time.monotonic()
            except Exception:
                started = None
            outcome = "error"
            try:
                result = original(*args, **kwargs)
                outcome = "success"
                try:
                    if isinstance(result, subprocess.CompletedProcess) and result.returncode:
                        outcome = "failure"
                    elif isinstance(result, tuple) and result and isinstance(result[0], int) and result[0]:
                        outcome = "failure"
                    elif isinstance(result, dict) and result.get("ok") is False:
                        outcome = "failure"
                except Exception:
                    pass
                return result
            finally:
                try:
                    if started is not None:
                        trace.emit(phase, time.monotonic() - started, outcome)
                except Exception:
                    pass
        return traced

    try:
        project_formal._run = wrap(originals[0], lambda args: formal_phase(args[0]))
        docker_runtime.doctor = wrap(originals[1], lambda args: "polaris.preflight")
        docker_runtime._stage_cli = wrap(originals[2], lambda args: "polaris.staging")
        docker_runtime._docker = wrap(originals[3], docker_phase)
        # Runner calls use a separate prefix, so they do not merge with Polaris docker.* events.
        runner._process = wrap(originals[4], lambda args: "runner.process")
        runner._docker = wrap(originals[5], lambda args: "runner." + docker_phase(args))
        runner._stage = wrap(originals[6], lambda args: "runner.stage")
        runner._stage_archive = wrap(originals[7], lambda args: "runner.stage_archive")
        yield
    finally:
        (project_formal._run, docker_runtime.doctor, docker_runtime._stage_cli,
         docker_runtime._docker, runner._process, runner._docker, runner._stage,
         runner._stage_archive) = originals


def main():
    args = sys.argv[1:]
    if args[:1] == ["--"]:
        args = args[1:]
    with profiling(Trace(os.environ.get("MENTAT_PROFILE_TRACE"))):
        app(args=args, prog_name="mentat")


if __name__ == "__main__":
    main()
