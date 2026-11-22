"""Validate an explicitly selected, unshared Colima Docker profile."""

import json
import re
import subprocess

from . import process

_OUTPUT_LIMIT = 1024 * 1024


def _run(argv: list[str], timeout: int, *, check: bool = False) -> tuple[int, bytes]:
    """Run a Colima or Docker probe with subprocess.run's error types on the shared runner."""
    try:
        code, stdout, stderr = process.run(argv, timeout=timeout, output_limit=_OUTPUT_LIMIT)
    except TimeoutError as exc:
        raise subprocess.TimeoutExpired(argv, timeout) from exc
    except RuntimeError as exc:
        raise ValueError("Colima validation output exceeded limit") from exc
    if check and code:
        raise subprocess.CalledProcessError(code, argv, stdout, stderr)
    return code, stdout


def validate_colima(context: str) -> None:
    if not re.fullmatch(r"colima-[A-Za-z0-9_-]+", context):
        raise ValueError("Mentat requires a named, dedicated Colima profile")
    profile = context.removeprefix("colima-")
    code, endpoint = _run(
        [
            "docker",
            "--context",
            context,
            "context",
            "inspect",
            context,
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ],
        timeout=10,
    )
    endpoint = endpoint.strip()
    if code or not endpoint.startswith(b"unix:///"):
        raise ValueError("Mentat requires a dedicated local Unix-socket Docker context")
    _, status = _run(["colima", "status", profile, "--json"], timeout=10, check=True)
    colima = json.loads(status)
    if colima.get("docker_socket", "").encode() != endpoint.strip():
        raise ValueError("Docker context does not match the selected Colima profile")
    _, mounts = _run(
        [
            "colima",
            "--profile",
            profile,
            "ssh",
            "--",
            "sh",
            "-c",
            "findmnt -rn -t virtiofs,9p,fuse.sshfs >/dev/null; rc=$?; "
            'if [ "$rc" -eq 1 ]; then printf unshared; else printf "mounted:%s" "$rc"; fi',
        ],
        timeout=15,
        check=True,
    )
    if mounts != b"unshared":
        raise ValueError("The dedicated Colima VM must have host mounts disabled")
