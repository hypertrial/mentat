#!/usr/bin/env python3
"""Warm Go module and build caches for both replay revisions."""

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

BEFORE = "410b789ed781852bbb4376e4f5cb57e76da12c56"
FIXED = "3fb910983b9674efc6092418af90a1c2b821dd42"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def main():
    if len(sys.argv) != 3:
        raise ValueError("usage: prepare.py REPO BASE")
    repo = Path(sys.argv[1]).resolve()
    base = sys.argv[2]
    contract = json.loads(git(repo, "show", f"{base}:.mentat/contract.json"))
    probe = git(repo, "show", f"{base}:.mentat/probe_test.go")
    checks = [c for c in contract["behavior"] if c["id"] == "compaction-rows"]
    if len(checks) != 1:
        raise ValueError("missing compaction-rows check")
    argv = checks[0]["argv"]
    mod = argv[argv.index("--mod-cache") + 1]
    build = argv[argv.index("--build-cache") + 1]
    if not all(Path(p).is_absolute() and Path(p).is_dir() for p in (mod, build)):
        raise ValueError("invalid cache paths")
    env = os.environ.copy()
    env.update(
        GOENV="off",
        GOTOOLCHAIN="local",
        GOWORK="off",
        GOMODCACHE=mod,
        GOCACHE=build,
        GOMAXPROCS="2",
    )
    if subprocess.check_output(["go", "version"], env=env).decode().split()[2] != "go1.26.3":
        raise ValueError("Go 1.26.3 is required")
    for revision in (BEFORE, FIXED):
        with tempfile.TemporaryDirectory(prefix="mentat-iceberg-prepare-") as tmp:
            archive = Path(tmp) / "source.tar"
            archive.write_bytes(git(repo, "archive", revision))
            candidate = Path(tmp) / "candidate"
            candidate.mkdir()
            with tarfile.open(archive) as tar:
                tar.extractall(candidate, filter="data")
            target = candidate / "table/mentat_compaction_replay_test.go"
            if target.exists():
                raise ValueError("probe destination already exists")
            target.write_bytes(probe)
            subprocess.run(["go", "mod", "download"], cwd=candidate, env=env, check=True)
            subprocess.run(["go", "mod", "verify"], cwd=candidate, env=env, check=True)
            subprocess.run(
                [
                    "go",
                    "test",
                    "-mod=readonly",
                    "-p=2",
                    "-c",
                    "-o",
                    str(Path(tmp) / "probe.test"),
                    "./table",
                ],
                cwd=candidate,
                env=env,
                check=True,
            )
    print("Prepared both revisions; compiled binaries discarded.")


if __name__ == "__main__":
    main()
