"""One-shot, two-ref receive-pack transaction for controller-private merges."""

import os
import re
import selectors
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from . import gitio, process

_PACK_LIMIT = 64_000_000
_OUTPUT_LIMIT = 2_000_000
_TIMEOUT = 120.0


def _pkt(data: bytes) -> bytes:
    if len(data) + 4 > 65520:
        raise ValueError("receive-pack packet too large")
    return f"{len(data) + 4:04x}".encode() + data


class _Wire:
    def __init__(self, child: subprocess.Popen, deadline: float, active: Callable[[], bool]):
        self.child = child
        self.active = active
        self.deadline = deadline
        self.buffer = bytearray()
        self.stderr = 0
        self.total = 0
        assert child.stdin and child.stdout and child.stderr
        self.stdin = child.stdin
        self.stdout = child.stdout
        self.err = child.stderr
        for stream in (self.stdin, self.stdout, self.err):
            os.set_blocking(stream.fileno(), False)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.stdout, selectors.EVENT_READ, "out")
        self.selector.register(self.err, selectors.EVENT_READ, "err")

    def _step(self, writing: bool = False) -> bool:
        if not self.active():
            raise ValueError("receive-pack cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("receive-pack timed out")
        if writing:
            self.selector.register(self.stdin, selectors.EVENT_WRITE, "in")
        try:
            events = self.selector.select(min(remaining, 0.1))
        finally:
            if writing:
                self.selector.unregister(self.stdin)
        if not events:
            return False
        writable = False
        for key, _ in events:
            if key.data == "in":
                writable = True
                continue
            chunk = os.read(key.fd, 65536)
            if not chunk:
                self.selector.unregister(key.fileobj)
                continue
            self.total += len(chunk)
            if self.total > _OUTPUT_LIMIT:
                raise ValueError("receive-pack output exceeds limit")
            if key.data == "out":
                self.buffer.extend(chunk)
            else:
                self.stderr += len(chunk)
                if self.stderr > 65536:
                    raise ValueError("receive-pack stderr exceeds limit")
        return writable

    def read(self, count: int) -> bytes:
        while len(self.buffer) < count:
            if self.stdout.fileno() not in self.selector.get_map():
                raise ValueError("truncated receive-pack output")
            self._step()
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def packet(self) -> bytes | None:
        header = self.read(4)
        if not re.fullmatch(rb"[0-9a-f]{4}", header):
            raise ValueError("invalid receive-pack packet header")
        length = int(header, 16)
        if length == 0:
            return None
        if length < 4 or length > 65520:
            raise ValueError("invalid receive-pack packet length")
        return self.read(length - 4)

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            if self._step(writing=True):
                try:
                    count = os.write(self.stdin.fileno(), view[:65536])
                except BlockingIOError:
                    continue
                if not count:
                    raise ValueError("receive-pack input closed")
                view = view[count:]

    def finish(self) -> None:
        self.stdin.close()
        while self.stdout.fileno() in self.selector.get_map() or self.err.fileno() in self.selector.get_map():
            self._step()
        if self.buffer:
            raise ValueError("unexpected receive-pack output")
        while self.child.poll() is None:
            if not self.active() or time.monotonic() >= self.deadline:
                raise ValueError("receive-pack cancelled or timed out")
            time.sleep(0.05)
        if self.child.returncode:
            raise ValueError("receive-pack process failed")


def _start_receive_pack(repository: str, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", "git@github.com", f"git-receive-pack '{repository}.git'"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, start_new_session=True,
    )


def transact(
    repo: Path,
    repository: str,
    base: str,
    head: str,
    oid: str,
    branch: str,
    before_send: Callable[[], None],
    *,
    active: Callable[[], bool] = lambda: True,
    timeout: float = _TIMEOUT,
) -> None:
    """Send both exact expected-old ref updates in one atomic server transaction."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository) or ".." in repository:
        raise ValueError("invalid receive-pack repository")
    if not re.fullmatch(r"mentat/[0-9a-f]{12}/[0-9a-f]{12}", branch):
        raise ValueError("invalid receive-pack branch")
    if len(base) not in (40, 64) or any(not re.fullmatch(r"[0-9a-f]+", x) or len(x) != len(base) for x in (base, head, oid)):
        raise ValueError("invalid receive-pack object identity")
    refs = {"refs/heads/main": base, "refs/heads/" + branch: head}
    try:
        env = gitio.git_env()
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
        with tempfile.TemporaryFile() as source:
            source.write(f"{oid}\n^{base}\n^{head}\n".encode())
            source.seek(0)
            code, pack, _ = process.run(
                gitio.argv(repo, "-c", "core.hooksPath=/dev/null", "pack-objects", "--stdout", "--revs"),
                output_limit=_PACK_LIMIT, timeout=timeout, env=env, stdin=source, stderr=False,
            )
        if code or not pack.startswith(b"PACK"):
            raise ValueError("invalid Git-generated pack")
        if not active():
            raise ValueError("receive-pack cancelled")
        deadline = time.monotonic() + timeout
        child = _start_receive_pack(repository, env)
        try:
            wire = _Wire(child, deadline, active)
            first = wire.packet()
            if first is None or first.count(b"\0") != 1:
                raise ValueError("invalid receive-pack advertisement")
            first_ref, capabilities = first.split(b"\0")
            caps = capabilities.rstrip(b"\n").split(b" ")
            fmt = b"sha1" if len(base) == 40 else b"sha256"
            formats = [x.split(b"=", 1)[1] for x in caps if x.startswith(b"object-format=")]
            if b"atomic" not in caps or b"report-status" not in caps or (formats or [b"sha1"]) != [fmt]:
                raise ValueError("receive-pack lacks required capabilities or object format")
            advertised: dict[str, str] = {}
            row: bytes | None = first_ref
            while row is not None:
                match = re.fullmatch(rb"([0-9a-f]+) (refs/[^\s\x00]+)\n?", row)
                if not match or len(match[1]) != len(base):
                    raise ValueError("invalid receive-pack ref advertisement")
                ref = match[2].decode("ascii")
                if ref in advertised:
                    raise ValueError("duplicate receive-pack ref advertisement")
                advertised[ref] = match[1].decode("ascii")
                row = wire.packet()
            if any(advertised.get(ref) != old for ref, old in refs.items()):
                raise ValueError("receive-pack advertised changed refs")
            commands = list(refs.items())
            payload = _pkt(f"{commands[0][1]} {oid} {commands[0][0]}".encode() + b"\0report-status atomic" + (b" object-format=" + fmt if formats else b"") + b"\n")
            payload += _pkt(f"{commands[1][1]} {oid} {commands[1][0]}\n".encode()) + b"0000"
            if not active() or time.monotonic() >= deadline:
                raise ValueError("receive-pack cancelled or timed out")
            before_send()
            wire.write(payload)
            wire.write(pack)
            if wire.packet() != b"unpack ok\n":
                raise ValueError("receive-pack unpack failed or status missing")
            seen = set()
            for _ in range(2):
                status = wire.packet()
                if status is None or not status.startswith(b"ok ") or not status.endswith(b"\n"):
                    raise ValueError("receive-pack ref update failed or status missing")
                ref = status[3:-1].decode("ascii")
                if ref not in refs or ref in seen:
                    raise ValueError("unexpected receive-pack ref status")
                seen.add(ref)
            if wire.packet() is not None or seen != set(refs):
                raise ValueError("invalid receive-pack status terminator")
            wire.finish()
        finally:
            try:
                cleanup_failed = False
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    cleanup_failed = True
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
                child.wait(timeout=5)
                if cleanup_failed:
                    raise ValueError("receive-pack process group cleanup failed")
            finally:
                for stream in (child.stdin, child.stdout, child.stderr):
                    if stream:
                        stream.close()
                if "wire" in locals():
                    wire.selector.close()
    except (OSError, subprocess.SubprocessError, UnicodeError, TimeoutError, RuntimeError) as exc:
        raise ValueError("receive-pack transaction failed") from exc
