"""Validate an explicitly selected, unshared Colima Docker profile."""

import json
import re
import subprocess


def validate_colima(context: str) -> None:
    if not re.fullmatch(r"colima-[A-Za-z0-9_-]+", context):
        raise ValueError("Mentat requires a named, dedicated Colima profile")
    profile = context.removeprefix("colima-")
    inspected = subprocess.run(
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
        capture_output=True,
        timeout=10,
        check=False,
    )
    endpoint = inspected.stdout.strip()
    if inspected.returncode or not endpoint.startswith(b"unix:///"):
        raise ValueError("Mentat requires a dedicated local Unix-socket Docker context")
    status = subprocess.run(
        ["colima", "status", profile, "--json"],
        capture_output=True,
        timeout=10,
        check=True,
    )
    colima = json.loads(status.stdout)
    if colima.get("docker_socket", "").encode() != endpoint.strip():
        raise ValueError("Docker context does not match the selected Colima profile")
    mounts = subprocess.run(
        [
            "colima",
            "--profile",
            profile,
            "ssh",
            "--",
            "sh",
            "-c",
            'findmnt -rn -t virtiofs,9p,fuse.sshfs >/dev/null; test "$?" -eq 1 && printf unshared',
        ],
        capture_output=True,
        timeout=15,
        check=True,
    )
    if mounts.stdout != b"unshared":
        raise ValueError("The dedicated Colima VM must have host mounts disabled")
