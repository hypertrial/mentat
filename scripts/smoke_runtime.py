#!/usr/bin/env python3
"""Run the prepared Polaris runtime smoke test without modifying operator state."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from mentat.evidence import status
from mentat.project_formal import LEAN_VERSION
from mentat.showcases.polaris import docker_runtime
from mentat.showcases.polaris.evaluation import BEFORE, FIXED
from mentat.showcases.polaris.formal import run_formal
from mentat.showcases.polaris.regression import render_regression
from mentat.showcases.polaris.replay import snapshot
from mentat.showcases.polaris.spec import PACK, load_cases, load_spec


def check_cleanup(context: str, run_id: str) -> None:
    failures = []
    for command in (("ps", "-a"), ("network", "ls")):
        try:
            code, output = docker_runtime._docker(
                context,
                *command,
                "--filter",
                f"label=org.hypertrial.mentat.run={run_id}",
                "--quiet",
                timeout=20,
            )
            if code or output.strip():
                failures.append(" ".join(command))
        except (RuntimeError, OSError):
            failures.append(" ".join(command))
    if failures:
        raise RuntimeError(f"cleanup could not be verified for {run_id}: {failures}")


def run_checked(context: str, run_id: str, expected: str, operation) -> None:
    failure = None
    try:
        result = operation()
        if result.get("status") != expected:
            raise RuntimeError(f"{run_id}: expected {expected}, got {result}")
    except Exception as exc:
        failure = exc
        raise
    finally:
        try:
            check_cleanup(context, run_id)
        except RuntimeError as exc:
            if failure:
                raise RuntimeError(f"{failure}. {exc}") from failure
            raise
    print(f"{run_id}: {expected}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="local Polaris checkout")
    parser.add_argument("--docker-context", default="colima-mentat")
    args = parser.parse_args()
    try:
        elan = shutil.which("elan")
        toolchain = {}
        for tool in ("git", "docker", "colima", "lean", "lake"):
            executable = shutil.which(tool)
            if not executable:
                raise RuntimeError(f"missing required tool: {tool}")
            # Elan proxies may install a missing toolchain; require already installed binaries.
            if tool in ("lean", "lake"):
                path = Path(executable).resolve()
                if path.name == "elan" or (elan and path.samefile(elan)):
                    raise RuntimeError(
                        f"{tool} must point directly to the installed {LEAN_VERSION} toolchain"
                    )
                toolchain[tool] = path.parent
        if toolchain["lean"] != toolchain["lake"]:
            raise RuntimeError("lean and lake must come from the same installed toolchain")
        if not args.repo.is_dir():
            raise ValueError("--repo must be an existing local checkout")
        candidates = [snapshot(args.repo, revision) for revision in (BEFORE, FIXED)]
        runtime = docker_runtime.doctor(args.docker_context)
        if not runtime["ok"]:
            raise RuntimeError(f"prepared dedicated Colima runtime required: {runtime}")
        spec = load_spec(PACK / "contract.json")
        cases = load_cases(PACK / "public.json", spec)
        checks = run_formal(spec, PACK / "lean")
        if status(checks) != "pass":
            raise RuntimeError(f"formal checks failed: {checks}")
        print("formal checks: pass", flush=True)
        run_prefix = "smoke-" + uuid4().hex
        for index, (candidate, expected) in enumerate(zip(candidates, ("violation", "pass"))):
            for case_index, case in enumerate(cases):
                run_id = f"{run_prefix}-replay-{index}-{case_index}"
                run_checked(
                    args.docker_context,
                    run_id,
                    expected,
                    lambda: docker_runtime.run_case(
                        candidate, args.docker_context, case, spec.selected_properties, run_id
                    ),
                )
            run_id = f"{run_prefix}-pytest-{index}"
            source = render_regression(cases[0], spec.selected_properties)
            run_checked(
                args.docker_context,
                run_id,
                expected,
                lambda: docker_runtime.run_pytest(candidate, args.docker_context, source, run_id),
            )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"smoke failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
