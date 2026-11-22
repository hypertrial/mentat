"""Pinned Linux checker; only trusted data exports leave the source sandboxes."""

import base64
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from .canonical import decode_json

LEAN_VERSION = "4.35.0"
_ROOT = Path(f"/opt/lean-{LEAN_VERSION}")
# Tree digests of the official Lean archives installed by scripts/install-proof-checker,
# keyed by platform.machine(). Regenerate them with the "Checker toolchain digest"
# workflow when LEAN_VERSION changes.
TOOLCHAIN_SHA256 = {
    "x86_64": "9096fe09c207318a12327ee4e080abb96d29d3d2e08af58d0244c8b5ff7a8b50",
    "aarch64": "12748abcd7b210a881f257827b29665d82626dd07d9f817057edafff3eb8fd5e",
}
_SOURCE_LIMIT = 1_000_000
_OUTPUT_LIMIT = 1_000_000
_LIBRARY_CACHE = {}


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _privileged(args, **kwargs):
    prefix = [] if os.geteuid() == 0 else ["/usr/bin/sudo", "-n", "--"]
    return subprocess.run(
        prefix + args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        check=False,
        **kwargs,
    )


def _toolchain_digest():
    entries = []
    for path in sorted(_ROOT.rglob("*")):
        info = path.lstat()
        if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
            raise ValueError(
                f"Lean {LEAN_VERSION} toolchain must be root-owned and immutable to proof authors"
            )
        relative = str(path.relative_to(_ROOT))
        if stat.S_ISLNK(info.st_mode):
            if not path.resolve(strict=True).is_relative_to(_ROOT):
                raise ValueError("Lean toolchain link escapes its pinned directory")
            entries.append((relative, "link", os.readlink(path)))
        elif stat.S_ISREG(info.st_mode):
            signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            cached = _LIBRARY_CACHE.get(relative)
            if cached is None or cached[0] != signature:
                h = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(chunk)
                cached = signature, h.hexdigest()
                _LIBRARY_CACHE[relative] = cached
            entries.append((relative, "file", cached[1]))
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("Unsupported pinned toolchain file")
    if not entries:
        raise ValueError(f"Install the official Lean {LEAN_VERSION} distribution at {_ROOT}")
    return _hash(json.dumps(entries, separators=(",", ":")).encode())


def _pinned_toolchain_digest():
    digest = _toolchain_digest()
    if TOOLCHAIN_SHA256.get(platform.machine()) != digest:
        raise ValueError(
            f"The Lean {LEAN_VERSION} toolchain does not match the pinned official distribution"
        )
    return digest


def checker_identity():
    if sys.platform != "linux":
        raise ValueError("lean-comparator requires native Linux; use the dedicated Linux VM")
    for path in (
        _ROOT,
        Path("/usr/bin/bwrap"),
        Path("/usr/bin/systemd-run"),
        Path("/usr/bin/systemctl"),
        Path("/usr/bin/python3"),
    ):
        if not path.exists():
            raise ValueError(
                f"Install pinned Lean {LEAN_VERSION}, bubblewrap and systemd in the Linux checker"
            )
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(
                "Checker executables must be root-owned and not writable by proof authors"
            )
    if not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        raise ValueError("The Linux proof checker requires cgroup v2 resource controls")
    # Checked before any toolchain binary runs, so an unpinned lean is never executed.
    toolchain = _pinned_toolchain_digest()
    version = subprocess.run(
        [str(_ROOT / "bin/lean"), "--version"], capture_output=True, timeout=5, check=False
    )
    if version.returncode or not version.stdout.startswith(
        f"Lean (version {LEAN_VERSION},".encode()
    ):
        raise ValueError(f"The pinned comparator requires Lean {LEAN_VERSION} exactly")
    probe = _privileged(["/usr/bin/systemctl", "show", "--property=Version", "--value"])
    if probe.returncode:
        raise ValueError(
            "Allow noninteractive systemd service creation in the dedicated checker VM"
        )
    policy = Path("/etc/apparmor.d/bwrap-userns-restrict")
    restriction = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
    from . import proof_lean

    return {
        "engine": "lean-comparator",
        "version": LEAN_VERSION,
        "toolchain_sha256": toolchain,
        "bwrap_sha256": _hash(Path("/usr/bin/bwrap").read_bytes()),
        "python_sha256": _hash(Path("/usr/bin/python3").read_bytes()),
        "systemd_run_sha256": _hash(Path("/usr/bin/systemd-run").read_bytes()),
        "systemctl_sha256": _hash(Path("/usr/bin/systemctl").read_bytes()),
        "systemd_version": probe.stdout.decode().strip(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "apparmor_profile_sha256": _hash(policy.read_bytes()) if policy.exists() else None,
        "userns_restriction": restriction.read_text().strip() if restriction.exists() else None,
        "policy_sha256": _hash(
            (
                _DRIVER
                + proof_lean.PARSER_SOURCE
                + proof_lean.EXPORTER_SOURCE
                + proof_lean.AUDIT_SOURCE
            ).encode()
        ),
        "limits": {
            "seconds": 120,
            "memory_bytes": 3 * 1024**3,
            "tasks": 128,
            "workspace_bytes": 512 * 1024**2,
            "diagnostic_bytes": _OUTPUT_LIMIT,
        },
        "network": "private namespace",
        "cache": "fresh, separate approved and submission",
    }


def _unit_properties(unit):
    result = _privileged(
        [
            "/usr/bin/systemctl",
            "show",
            unit,
            "--property=Result",
            "--property=ExecMainCode",
            "--property=ExecMainStatus",
            "--property=ActiveState",
            "--property=SubState",
            "--property=ControlGroup",
        ]
    )
    if result.returncode:
        return {}
    return dict(line.split("=", 1) for line in result.stdout.decode().splitlines() if "=" in line)


def _read_result(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _OUTPUT_LIMIT:
            raise ValueError("Invalid proof checker result file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(_OUTPUT_LIMIT + 1)
        if len(raw) > _OUTPUT_LIMIT:
            raise ValueError("Proof checker result exceeded the output limit")
        return decode_json(raw)
    finally:
        os.close(fd)


def _service(files, timeout_seconds):
    unit = "mentat-proof-" + uuid4().hex + ".service"
    root = Path(tempfile.mkdtemp(prefix="mentat-check-"))
    root.chmod(0o755)
    inputs, outputs = root / "input", root / "result"
    inputs.mkdir(mode=0o755)
    outputs.mkdir(mode=0o755)
    started = False
    try:
        for name, data in files.items():
            path = inputs / name
            path.write_bytes(data)
            path.chmod(0o444)
        own = _privileged(["/usr/bin/chown", "nobody:nogroup", str(outputs)])
        if own.returncode:
            raise ValueError("Cannot create isolated checker result directory")
        properties = [
            "User=nobody",
            "Group=nogroup",
            "RemainAfterExit=yes",
            "PrivateNetwork=yes",
            "PrivateDevices=yes",
            "ProtectHome=tmpfs",
            "ProtectSystem=strict",
            "NoNewPrivileges=yes",
            "UMask=0077",
            "KillMode=control-group",
            "TimeoutStopSec=3",
            "SendSIGKILL=yes",
            f"RuntimeMaxSec={timeout_seconds}",
            "MemoryMax=3G",
            "MemorySwapMax=0",
            "TasksMax=128",
            "LimitFSIZE=128M",
            "TemporaryFileSystem=/work:rw,size=384M,mode=1777",
            "TemporaryFileSystem=/tmp:rw,size=128M,mode=1777",
            f"BindReadOnlyPaths={inputs}:/input",
            f"BindPaths={outputs}:/result",
            "InaccessiblePaths=-/run/docker.sock -/run/containerd/containerd.sock",
            "StandardOutput=null",
            "StandardError=null",
            "WorkingDirectory=/work",
        ]
        args = ["/usr/bin/systemd-run", "--quiet", "--no-block", "--unit=" + unit]
        for prop in properties:
            args += ["--property=" + prop]
        args += [
            "/usr/bin/env",
            "-i",
            f"PATH={_ROOT}/bin:/usr/bin:/bin",
            "HOME=/tmp",
            "LANG=C.UTF-8",
            "/usr/bin/python3",
            "/input/driver.py",
        ]
        launch = _privileged(args)
        if launch.returncode:
            raise ValueError("Cannot start the protected Linux proof service")
        started = True
        deadline = time.monotonic() + timeout_seconds + 5
        properties = {}
        while time.monotonic() < deadline:
            properties = _unit_properties(unit)
            if properties.get("SubState") in {"exited", "failed", "dead"}:
                break
            time.sleep(0.1)
        if (
            properties.get("Result") != "success"
            or properties.get("ExecMainCode") != "1"
            or properties.get("ExecMainStatus") != "0"
            or properties.get("SubState") != "exited"
        ):
            return {
                "status": "incomplete",
                "detail": "Protected checker timed out or could not complete",
                "logs": {},
            }
        # Acceptance is withheld until every process in the service has stopped.
        stopped = _privileged(["/usr/bin/systemctl", "stop", unit])
        if stopped.returncode:
            return {
                "status": "incomplete",
                "detail": "Protected checker cleanup did not complete",
                "logs": {},
            }
        group = properties.get("ControlGroup", "")
        if group:
            cgroup = Path("/sys/fs/cgroup") / group.lstrip("/")
            if cgroup.exists() and any(
                path.read_text().strip() for path in cgroup.rglob("cgroup.procs")
            ):
                return {
                    "status": "incomplete",
                    "detail": "Protected checker descendants did not stop",
                    "logs": {},
                }
        # No process can still write the result once the stop and empty control group are confirmed.
        result = _read_result(outputs / "result.json")
        if not isinstance(result, dict) or result.get("status") not in {
            "accepted",
            "rejected",
            "incomplete",
        }:
            raise ValueError("Invalid protected checker response")
        if not isinstance(result.get("detail"), str) or not isinstance(result.get("logs"), dict):
            raise ValueError("Invalid protected checker response")
        # Only validated fields leave the service; extra keys must not override the caller's evidence.
        return {
            "status": result["status"],
            "detail": result["detail"],
            "logs": {
                name: base64.b64decode(data, validate=True)
                for name, data in result["logs"].items()
            },
        }
    finally:
        if started:
            _privileged(["/usr/bin/systemctl", "stop", unit])
            _privileged(["/usr/bin/systemctl", "reset-failed", unit])
        # Only the unique directory created above is passed to privileged cleanup.
        if root.parent == Path(tempfile.gettempdir()) and root.name.startswith("mentat-check-"):
            _privileged(["/usr/bin/rm", "-rf", "--", str(root)])


def _check(approved, solution, policy, timeout_seconds):
    start = time.monotonic()
    result = {
        "status": "incomplete",
        "detail": "Protected proof checker could not complete",
        "logs": {},
        "identity": {},
        "approved_sha256": _hash(approved) if isinstance(approved, bytes) else None,
        "source_sha256": _hash(solution) if isinstance(solution, bytes) else None,
        "theorem": policy.get("theorem"),
    }
    try:
        for source in (approved, solution):
            if not isinstance(source, bytes) or len(source) > _SOURCE_LIMIT:
                raise ValueError("Proof source exceeds the source-only size limit")
            source.decode("utf-8")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise ValueError("Proof checker timeout must be between 1 and 120 seconds")
        identity = checker_identity()
        result["identity"] = identity
        from . import proof_lean

        files = {
            "Challenge.lean": approved,
            "Solution.lean": solution,
            "Parser.lean": proof_lean.PARSER_SOURCE.encode(),
            "Exporter.lean": proof_lean.EXPORTER_SOURCE.encode(),
            "Audit.lean": proof_lean.AUDIT_SOURCE.encode(),
            "policy.json": json.dumps(policy).encode(),
            "driver.py": _DRIVER.encode(),
        }
        # The caller holds the host-wide lock (proof._host_lock); a second flock here would self-block.
        checked = _service(files, timeout_seconds)
        if checker_identity() != identity:
            raise ValueError("Proof checker environment changed during checking")
        result.update(checked)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
        # Source and tool output never become diagnostics on this path.
        result.update(status="incomplete", detail=str(exc), logs={})
    result["duration_seconds"] = time.monotonic() - start
    return result


def audit_approved(approved, *, theorems, allowed_imports, permitted_axioms):
    return _check(
        approved,
        approved,
        {
            "mode": "approved",
            "theorem": theorems[0] if theorems else "",
            "theorems": theorems,
            "allowed_imports": allowed_imports,
            "permitted_axioms": permitted_axioms,
        },
        120,
    )


def check_submission(
    approved, solution, *, theorem, allowed_imports, permitted_axioms, timeout_seconds=120
):
    return _check(
        approved,
        solution,
        {
            "mode": "submission",
            "theorem": theorem,
            "theorems": [],
            "allowed_imports": allowed_imports,
            "permitted_axioms": permitted_axioms,
        },
        timeout_seconds,
    )


# Executed by the fixed system Python inside the resource-bounded service.
_DRIVER = r"""
import base64, json, os, selectors, signal, stat, subprocess, time
from pathlib import Path

LEAN = '/opt/lean-4.35.0/bin/lean'
LAKE = '/opt/lean-4.35.0/bin/lake'
ENV = {'PATH': '/opt/lean-4.35.0/bin:/usr/bin:/bin', 'HOME': '/tmp', 'LANG': 'C.UTF-8'}
WORK, INPUT = '/work', '/input'
policy = json.loads(Path('/input/policy.json').read_bytes())
logs = bytearray()
diagnostic_bytes = 0

class Incomplete(Exception): pass
class Rejected(Exception): pass

def run(args, *, cwd='/work', env=None, export=None):
    global diagnostic_bytes
    proc = subprocess.Popen(args, cwd=cwd, env=env or ENV, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    output = bytearray()
    streams = selectors.DefaultSelector()
    streams.register(proc.stdout, selectors.EVENT_READ, 'stdout')
    streams.register(proc.stderr, selectors.EVENT_READ, 'stderr')
    count = 0
    target = open(export, 'wb') if export else None
    try:
        while streams.get_map():
            for key, _ in streams.select(0.2):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    streams.unregister(key.fileobj)
                    continue
                if export and key.data == 'stdout':
                    count += len(data)
                    if count > 64 * 1024 * 1024: raise Incomplete('Proof export exceeded its bounded size')
                    target.write(data)
                else:
                    diagnostic_bytes += len(data)
                    if diagnostic_bytes > 1_000_000: raise Incomplete('Proof diagnostics exceeded the output limit')
                    if key.data == 'stdout': output.extend(data)
                    logs.extend(data[:max(0, 128000-len(logs))])
        code = proc.wait()
        return code, bytes(output)
    finally:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        proc.wait()
        streams.close()
        if target: target.close()

def sandbox(args, project, *, writable=False, env=None, export=None):
    environment = env or ENV
    cmd = ['/usr/bin/bwrap', '--ro-bind', '/', '/', '--tmpfs', '/home', '--tmpfs', '/root',
           '--tmpfs', '/run', '--tmpfs', '/var', '--tmpfs', '/tmp', '--dev', '/dev',
           '--proc', '/proc', '--clearenv', '--unshare-all', '--die-with-parent', '--new-session',
           '--ro-bind', project, project]
    if writable: cmd += ['--bind', project+'/.lake', project+'/.lake']
    for name, value in environment.items(): cmd += ['--setenv', name, value]
    cmd += ['--chdir', project, '--'] + args
    return run(cmd, env=ENV, export=export)

def parse(source, mode):
    code, output = run([LEAN, '--run', '/input/Parser.lean', source, '/input/policy.json', mode])
    try: result = json.loads(output)
    except (ValueError, UnicodeError): raise Incomplete('Lean source policy parser returned invalid data')
    if code == 1 and result.get('status') == 'rejected': raise Rejected('Lean source does not satisfy the approved source policy')
    if code != 0 or result.get('status') != 'accepted': raise Incomplete('Lean source policy could not be established')

def stage_olean(source, folder, limit=128 * 1024 * 1024):
    # Only the one compiled module crosses from the writable build into a fresh folder.
    message = 'Lean source compilation did not complete'
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise Incomplete(message)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        os.close(fd)
        raise Incomplete(message)
    with os.fdopen(fd, 'rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit: raise Incomplete(message)
    os.mkdir(folder, 0o755)
    fd = os.open(os.path.join(folder, os.path.basename(source)), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(fd, 'wb') as out: out.write(data)
    os.chmod(folder, 0o555)

def build(module):
    project = WORK + '/' + module
    Path(project+'/.lake').mkdir(parents=True)
    Path(project+'/'+module+'.lean').write_bytes(Path(INPUT+'/'+module+'.lean').read_bytes())
    code, _ = sandbox([LEAN, '-o', project+'/.lake/'+module+'.olean', project+'/'+module+'.lean'], project, writable=True)
    if code: raise Incomplete('Lean source compilation did not complete')
    export = WORK+'/'+module+'.ndjson'
    # The writable .lake is never a search path; the exporter sees only the staged module.
    folder = WORK+'/'+module+'-export'
    stage_olean(project+'/.lake/'+module+'.olean', folder)
    env = {**ENV, 'LEAN_PATH': folder}
    code, _ = sandbox([LEAN, '--run', INPUT+'/Exporter.lean', module], project, env=env, export=export)
    if code: raise Incomplete('Lean source export did not complete')
    # A valid exported environment is required before a semantic rejection is reported.
    code, _ = sandbox(['/opt/lean-4.35.0/bin/leanchecker', '--silent', '--from-export', export], project)
    if code: raise Incomplete('Lean kernel export validation did not complete')
    return export

status, detail = 'incomplete', 'Protected proof checker could not complete'
try:
    parse('/input/Challenge.lean', 'approved')
    parse('/input/Solution.lean', 'approved' if policy['mode']=='approved' else 'submission')
    challenge = build('Challenge')
    solution = challenge if policy['mode']=='approved' else build('Solution')
    args = [LEAN, '--run', '/input/Audit.lean', challenge, solution, '/input/policy.json']
    if policy['mode'] == 'approved': args.append('approved')
    code, output = run(args)
    try: audit = json.loads(output)
    except (ValueError, UnicodeError): raise Incomplete('Lean environment audit returned invalid data')
    if code == 1 and audit.get('status') == 'rejected': raise Rejected('Lean target, definition or axiom policy did not match')
    if code != 0 or audit.get('status') != 'accepted': raise Incomplete('Lean environment audit could not establish the policy')
    if policy['mode'] != 'approved':
        project = Path('/work/verdict'); project.mkdir(); (project/'.lake').mkdir()
        (project/'lakefile.toml').write_text('name = "mentat_proof"\nversion = "0.0.0"\n')
        (project/'lake-manifest.json').write_text('{"version":"1.1.0","packagesDir":".lake/packages","packages":[],"name":"mentat_proof","lakeDir":".lake"}')
        (project/'comparator.json').write_text(json.dumps({'challenge_module':'Challenge','solution_module':'Solution','theorem_names':[policy['theorem']],'definition_names':[],'permitted_axioms':policy['permitted_axioms']}))
        code, _ = run([LAKE, 'comparator', '--challenge-from-export', challenge, '--solution-from-export', solution], cwd=str(project))
        if code: raise Incomplete('Pinned comparator or kernel check did not complete')
        detail = 'Pinned comparator and kernel accepted the frozen target proof'
    else: detail = 'Approved Lean declarations and theorem holes passed the environment audit'
    status = 'accepted'
except Rejected as error: status, detail = 'rejected', str(error)
except Incomplete as error: status, detail = 'incomplete', str(error)
except Exception: status, detail = 'incomplete', 'Protected proof checker could not complete'
record = {'status':status,'detail':detail,'logs':{'checker.txt':base64.b64encode(logs).decode()}}
path = Path('/result/result.tmp'); path.write_text(json.dumps(record)); path.chmod(0o644)
path.rename('/result/result.json')
"""
