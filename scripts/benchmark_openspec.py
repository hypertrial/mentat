#!/usr/bin/env python3
"""Benchmark committed OpenSpec imports using the currently imported Mentat source."""

import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from mentat.openspec import import_spec


def benchmark(size):
    with tempfile.TemporaryDirectory(prefix="mentat-openspec-bench-") as directory:
        repo = Path(directory)
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_AUTHOR_NAME": "Benchmark", "GIT_AUTHOR_EMAIL": "bench@example.invalid",
               "GIT_COMMITTER_NAME": "Benchmark", "GIT_COMMITTER_EMAIL": "bench@example.invalid",
               "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
               "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000"}
        # Do not let the caller's Git repository settings or hooks affect the fixture.
        for name in list(env):
            if name.startswith("GIT_") and name not in {
                "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"
            }:
                del env[name]

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null",
                                   "-c", "commit.gpgsign=false", *args], env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()

        git("init", "-q")
        for index in range(size):
            path = repo / "openspec" / "specs" / f"capability-{index:03}" / "spec.md"
            path.parent.mkdir(parents=True)
            path.write_text("# Capability\n\n### Requirement: Works\nIt works.\n\n"
                            "#### Scenario: Success\nIt succeeds.\n")
        git("add", "-A")
        git("commit", "-qm", "fixture")
        revision = git("rev-parse", "HEAD")
        original_popen = subprocess.Popen
        counts = []
        samples = []
        expected = None
        for sample in range(6):
            count = 0

            def counted(args, *positional, **kwargs):
                nonlocal count
                if isinstance(args, (list, tuple)) and "cat-file" in args:
                    count += 1
                return original_popen(args, *positional, **kwargs)

            started = time.perf_counter()
            with patch("subprocess.Popen", counted):
                draft = import_spec(repo, revision)
            elapsed = time.perf_counter() - started
            assert draft["status"] == "pass" and not draft["diagnostics"]
            assert len(draft["claims"]) == size
            assert all(claim["requires_behavior_check"] for claim in draft["claims"])
            if expected is not None:
                assert draft == expected
            expected = draft
            if sample:
                samples.append(elapsed)
                counts.append(count)
        return {"capabilities": size, "median_seconds": statistics.median(samples),
                "min_seconds": min(samples), "max_seconds": max(samples),
                "cat_file_counts": counts}


def main():
    print(json.dumps([benchmark(size) for size in (1, 32, 128)], indent=2))


if __name__ == "__main__":
    main()
