"""Adversarial receive-pack tests against local disposable processes only."""

import os
import subprocess
import sys
import time

import pytest

from mentat import autonomy_git_transaction as transaction

BRANCH = "mentat/aaaaaaaaaaaa/bbbbbbbbbbbb"


def git(repo, *args, input=None):
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=input, capture_output=True, check=True,
    ).stdout.decode().strip()


# The wrapper relays a traditional interactive receive-pack, recording the exact
# commands before streaming the pack. The process under test sees no file URL.
RELAY = r'''
import os, subprocess, sys, threading
server = subprocess.Popen(["git", "receive-pack", sys.argv[1]], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
def read_exact(source, count):
    data = bytearray()
    while len(data) < count:
        chunk = os.read(source.fileno(), count - len(data))
        if not chunk: raise EOFError()
        data.extend(chunk)
    return bytes(data)
def packet(source):
    h = read_exact(source, 4)
    n = int(h, 16)
    return h if n == 0 else h + read_exact(source, n-4)
while True:
    p = packet(server.stdout); sys.stdout.buffer.write(p); sys.stdout.buffer.flush()
    if p == b"0000": break
commands = bytearray()
while True:
    p = packet(sys.stdin.buffer); commands.extend(p)
    server.stdin.write(p); server.stdin.flush()
    if p == b"0000": break
open(sys.argv[2], "wb").write(commands)
def copy(source, dest):
    try:
        while True:
            data = os.read(source.fileno(), 65536)
            if not data: break
            os.write(dest.fileno(), data)
    except (BrokenPipeError, OSError): pass
threading.Thread(target=copy, args=(sys.stdin.buffer, server.stdin), daemon=True).start()
threading.Thread(target=copy, args=(server.stderr, sys.stderr.buffer), daemon=True).start()
response = bytearray()
while True:
    data = os.read(server.stdout.fileno(), 65536)
    if not data: break
    response.extend(data)
    os.write(sys.stdout.fileno(), data)
code = server.wait()
if code == 0:
    open(sys.argv[3], "wb").write(response)
sys.exit(code)
'''


def packets(data):
    result = []
    while data:
        size = int(data[:4], 16)
        data = data[4:]
        if not size:
            assert not data
            return result
        result.append(data[:size - 4])
        data = data[size - 4:]
    raise AssertionError("missing packet flush")


@pytest.fixture
def repo(tmp_path):
    work, bare = tmp_path / "work", tmp_path / "remote.git"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.name", "Fixture")
    git(work, "config", "user.email", "fixture@example.test")
    (work / "file").write_text("base\n")
    git(work, "add", "file")
    git(work, "commit", "-qm", "base")
    base = git(work, "rev-parse", "HEAD")
    (work / "file").write_text("head\n")
    git(work, "commit", "-qam", "head")
    head = git(work, "rev-parse", "HEAD")
    tree = git(work, "rev-parse", "HEAD^{tree}")
    merge = git(work, "commit-tree", tree, "-p", base, "-p", head, input=b"merge\n")
    other = git(work, "commit-tree", tree, "-p", head, input=b"other\n")
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(work, "push", "-q", str(bare), base + ":refs/heads/main", head + ":refs/heads/mentat/aaaaaaaaaaaa/bbbbbbbbbbbb", other + ":refs/heads/seed")
    return work, bare, base, head, merge, other, tree


def assert_groups_gone(processes):
    for process in processes:
        assert process.poll() is not None
        deadline = time.monotonic() + 2
        while True:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError as exc:
                if time.monotonic() >= deadline:
                    raise AssertionError(f"cannot verify cleanup of process group {process.pid}") from exc
            assert time.monotonic() < deadline, f"process group {process.pid} survived cleanup"
            time.sleep(0.01)


def ref(bare, name):
    return git(bare, "rev-parse", "refs/heads/" + name)


def install_relay(monkeypatch, tmp_path, bare):
    script = tmp_path / "relay.py"
    capture = tmp_path / "commands"
    script.write_text(RELAY)
    processes = []
    def launch(*args, **kwargs):
        process = subprocess.Popen([sys.executable, "-u", str(script), str(bare), str(capture), str(tmp_path / "status")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        processes.append(process)
        (tmp_path / "processes.log").write_text(f"pid={process.pid} pgid={process.pid}\n")
        return process
    monkeypatch.setattr(transaction, "_start_receive_pack", launch)
    return capture, processes


def test_real_bidirectional_atomic_transaction(repo, tmp_path, monkeypatch):
    work, bare, base, head, merge, _, tree = repo
    capture, processes = install_relay(monkeypatch, tmp_path, bare)
    dispatched = []
    def before_send():
        assert not capture.exists()
        dispatched.append(True)
    transaction.transact(work, "org/private", base, head, merge, BRANCH, before_send, timeout=4)
    assert dispatched == [True]
    assert_groups_gone(processes)
    commands = packets(capture.read_bytes())
    assert len(commands) == 2
    first, capabilities = commands[0].rstrip(b"\n").split(b"\0")
    assert set(capabilities.split()) >= {b"atomic", b"report-status"}
    assert first.decode() == f"{base} {merge} refs/heads/main"
    assert commands[1].decode().strip() == f"{head} {merge} refs/heads/mentat/aaaaaaaaaaaa/bbbbbbbbbbbb"
    assert ref(bare, "main") == ref(bare, BRANCH) == merge
    assert git(bare, "show", "-s", "--format=%P", merge).split() == [base, head]
    assert git(bare, "rev-parse", merge + "^{tree}") == tree
    assert git(bare, "for-each-ref", "--format=%(refname)").splitlines() == [
        "refs/heads/main", "refs/heads/mentat/aaaaaaaaaaaa/bbbbbbbbbbbb", "refs/heads/seed"
    ]


@pytest.mark.parametrize("changed", ["main", BRANCH])
@pytest.mark.parametrize("destination", ["target", "other"])
def test_race_after_advertisement_rejects_both_commands(repo, tmp_path, monkeypatch, changed, destination):
    work, bare, base, head, merge, other, _ = repo
    capture, processes = install_relay(monkeypatch, tmp_path, bare)
    # Make the target object available independently of the transaction pack.
    git(work, "push", "-q", str(bare), merge + ":refs/heads/seed-merge")
    moved = merge if destination == "target" else other
    def before_send():
        assert not capture.exists()
        git(bare, "update-ref", "refs/heads/" + changed, moved)
    with pytest.raises(ValueError, match="receive-pack ref update failed"):
        transaction.transact(work, "org/private", base, head, merge, BRANCH, before_send, timeout=4)
    assert_groups_gone(processes)
    assert ref(bare, changed) == moved
    assert ref(bare, BRANCH if changed == "main" else "main") == (head if changed == "main" else base)
    commands = packets(capture.read_bytes())
    assert len(commands) == 2
    assert commands[0].startswith(f"{base} {merge} refs/heads/main\0".encode())
    assert commands[1] == f"{head} {merge} refs/heads/mentat/aaaaaaaaaaaa/bbbbbbbbbbbb\n".encode()

# A controlled peer tests protocol failures a real receive-pack cannot emit.
# It still uses the same interactive pipes as the real-server test above.
PEER = r'''
import os, sys, threading, time
advertisement = open(sys.argv[1], "rb").read()
status = open(sys.argv[2], "rb").read()
mode = sys.argv[3]
os.write(1, advertisement)
if mode == "stall-advert": time.sleep(30); sys.exit(0)
if mode == "flood-stderr":
    while True: os.write(2, b"x" * 8192)
while True:
    h = sys.stdin.buffer.read(4)
    if len(h) < 4: sys.exit(0)
    n = int(h, 16)
    if not n: break
    if len(sys.stdin.buffer.read(n - 4)) != n - 4: sys.exit(0)
open(sys.argv[4], "wb").write(b"commands")
def drain():
    while os.read(0, 65536): pass
threading.Thread(target=drain, daemon=True).start()
if mode == "stall-status": time.sleep(30)
else: os.write(1, status)
'''


def pkt(value):
    return f"{len(value) + 4:04x}".encode() + value


def install_peer(monkeypatch, tmp_path, advertisement, status=b"", mode="normal"):
    script, advert, response, capture = (tmp_path / name for name in ("peer.py", "advert", "status", "sent"))
    script.write_text(PEER)
    advert.write_bytes(advertisement)
    response.write_bytes(status)
    processes = []
    def launch(*args, **kwargs):
        process = subprocess.Popen([sys.executable, "-u", str(script), str(advert), str(response), mode, str(capture)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        processes.append(process)
        (tmp_path / "processes.log").write_text(f"pid={process.pid} pgid={process.pid}\n")
        return process
    monkeypatch.setattr(transaction, "_start_receive_pack", launch)
    return capture, processes


def advert(base, head, capabilities=b"report-status atomic object-format=sha1"):
    return (pkt(f"{base} refs/heads/main".encode() + b"\0" + capabilities + b"\n")
            + pkt(f"{head} refs/heads/mentat/aaaaaaaaaaaa/bbbbbbbbbbbb\n".encode()) + b"0000")


def status(*rows):
    return b"".join(pkt(row.encode() + b"\n") for row in rows) + b"0000"


@pytest.mark.parametrize("advertisement", [
    lambda b, h: advert(b, h, b"report-status object-format=sha1"),
    lambda b, h: advert(b, h, b"atomic object-format=sha1"),
    lambda b, h: advert(b, h, b"atomic report-status object-format=sha256"),
    lambda b, h: b"zzzz",
    lambda b, h: b"0010short",
    lambda b, h: b"ffff" + b"x" * 65531,
    lambda b, h: advert(b, h)[:-4],
])
def test_invalid_advertisement_never_dispatches(repo, tmp_path, monkeypatch, advertisement):
    work, _, base, head, merge, _, _ = repo
    capture, processes = install_peer(monkeypatch, tmp_path, advertisement(base, head))
    dispatched = []
    with pytest.raises(ValueError):
        transaction.transact(work, "org/private", base, head, merge, BRANCH, lambda: dispatched.append(True), timeout=1)
    assert not dispatched and not capture.exists()
    assert_groups_gone(processes)


@pytest.mark.parametrize("response", [
    status("unpack ok", "ng refs/heads/main stale", "ng refs/heads/mentat/aaaaaaaaaaaa/bbbbbbbbbbbb atomic failure"),
    status("unpack error broken", "ok refs/heads/main", "ok refs/heads/mentat/aaaaaaaaaaaa/bbbbbbbbbbbb"),
    status("unpack ok", "ok refs/heads/main"),
    status("unpack ok", "ok refs/heads/main", "ok refs/heads/main", "ok refs/heads/mentat/aaaaaaaaaaaa/bbbbbbbbbbbb"),
    status("unpack ok", "ok refs/heads/main", "ok refs/heads/mentat/aaaaaaaaaaaa/bbbbbbbbbbbb", "ok refs/heads/extra"),
    status("unpack ok", "ok refs/heads/main", "ok refs/heads/mentat/aaaaaaaaaaaa/bbbbbbbbbbbb")[:-4],
    b"zzzz", b"", b"0010short",
])
def test_invalid_or_incomplete_status_is_not_success(repo, tmp_path, monkeypatch, response):
    work, _, base, head, merge, _, _ = repo
    capture, processes = install_peer(monkeypatch, tmp_path, advert(base, head), response)
    dispatched = []
    with pytest.raises(ValueError):
        transaction.transact(work, "org/private", base, head, merge, BRANCH, lambda: dispatched.append(True), timeout=1)
    assert dispatched == [True] and capture.exists()
    assert_groups_gone(processes)


@pytest.mark.parametrize("mode,expect_dispatch", [("stall-advert", False), ("flood-stderr", False), ("stall-status", True)])
def test_timeout_cleans_up_process_and_preserves_dispatch_boundary(repo, tmp_path, monkeypatch, mode, expect_dispatch):
    work, _, base, head, merge, _, _ = repo
    initial = b"" if mode in {"stall-advert", "flood-stderr"} else advert(base, head)
    capture, processes = install_peer(monkeypatch, tmp_path, initial, mode=mode)
    dispatched = []
    with pytest.raises(ValueError):
        transaction.transact(work, "org/private", base, head, merge, BRANCH, lambda: dispatched.append(True), timeout=0.3)
    assert bool(dispatched) is expect_dispatch
    assert capture.exists() is expect_dispatch
    assert_groups_gone(processes)


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_cancellation_stops_peer_without_erasing_dispatch(repo, tmp_path, monkeypatch, after_dispatch):
    work, _, base, head, merge, _, _ = repo
    capture, processes = install_peer(monkeypatch, tmp_path, advert(base, head), mode="stall-status")
    active = [after_dispatch]
    dispatched = []
    def before_send():
        dispatched.append(True)
        active[0] = False
    with pytest.raises(ValueError):
        transaction.transact(work, "org/private", base, head, merge, BRANCH, before_send,
                             active=lambda: active[0], timeout=2)
    assert bool(dispatched) is after_dispatch
    if not after_dispatch:
        assert not capture.exists()
    assert_groups_gone(processes)


def test_cleanup_falls_back_when_group_signal_is_denied(repo, tmp_path, monkeypatch):
    work, _, base, head, merge, _, _ = repo
    _, processes = install_peer(monkeypatch, tmp_path, b"", mode="stall-advert")
    denied = []
    real_killpg = os.killpg
    def deny_group(pid, sig):
        if not processes or pid != processes[0].pid:
            return real_killpg(pid, sig)
        denied.append((pid, sig))
        raise PermissionError("fixture denies group signal")
    with monkeypatch.context() as patch:
        patch.setattr(transaction.os, "killpg", deny_group)
        started = time.monotonic()
        with pytest.raises(ValueError):
            transaction.transact(work, "org/private", base, head, merge, BRANCH,
                                 lambda: None, timeout=0.3)
        assert time.monotonic() - started < 3
    assert denied and len(processes) == 1
    assert_groups_gone(processes)


def test_execution_identity_rejects_changed_transport_source(tmp_path, monkeypatch):
    from pathlib import Path

    from mentat import autonomy_runtime

    changed = tmp_path / "autonomy_git_transaction.py"
    changed.write_bytes(Path(transaction.__file__).read_bytes() + b"\n# changed after import\n")
    monkeypatch.setattr(transaction, "__file__", str(changed))
    with pytest.raises(ValueError, match="changed after import"):
        autonomy_runtime.execution_identity()


def test_cleanup_failure_never_reports_success_after_applied_transaction(repo, tmp_path, monkeypatch):
    work, bare, base, head, merge, _, _ = repo
    _, processes = install_relay(monkeypatch, tmp_path, bare)
    dispatched = []
    real_killpg = os.killpg
    def deny_group(pid, sig):
        if not processes or pid != processes[0].pid:
            return real_killpg(pid, sig)
        # This marker is written by the relay only after the real receive-pack
        # exits successfully, after forwarding its complete status response.
        assert (tmp_path / "status").exists(), "receive-pack did not finish before cleanup"
        assert packets((tmp_path / "status").read_bytes()) == [
            b"unpack ok\n", b"ok refs/heads/main\n",
            f"ok refs/heads/{BRANCH}\n".encode(),
        ]
        assert ref(bare, "main") == ref(bare, BRANCH) == merge
        raise PermissionError("fixture denies group signal")
    with monkeypatch.context() as patch:
        patch.setattr(transaction.os, "killpg", deny_group)
        with pytest.raises(ValueError, match="receive-pack process group cleanup failed"):
            transaction.transact(work, "org/private", base, head, merge, BRANCH,
                                 lambda: dispatched.append(True), timeout=4)
    assert dispatched == [True]
    assert ref(bare, "main") == ref(bare, BRANCH) == merge
    assert_groups_gone(processes)


def test_cleanup_failure_keeps_preapply_cancellation_unknown(repo, tmp_path, monkeypatch):
    work, bare, base, head, merge, _, _ = repo
    capture, processes = install_peer(monkeypatch, tmp_path, advert(base, head), mode="stall-status")
    active = [True]
    dispatched = []
    real_killpg = os.killpg
    def before_send():
        dispatched.append(True)
        active[0] = False
    def deny_group(pid, sig):
        if not processes or pid != processes[0].pid:
            return real_killpg(pid, sig)
        raise PermissionError("fixture denies group signal")
    with monkeypatch.context() as patch:
        patch.setattr(transaction.os, "killpg", deny_group)
        with pytest.raises(ValueError, match="receive-pack process group cleanup failed") as exc:
            transaction.transact(work, "org/private", base, head, merge, BRANCH, before_send,
                                 active=lambda: active[0], timeout=4)
    assert isinstance(exc.value.__context__, ValueError)
    assert str(exc.value.__context__) == "receive-pack cancelled"
    assert dispatched == [True] and not capture.exists()
    assert ref(bare, "main") == base and ref(bare, BRANCH) == head
    assert_groups_gone(processes)
