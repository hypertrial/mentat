"""Bounded model checks and an independently kernel-checked Lean theorem."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .spec import Spec

Z3_DOMAIN = ("finance", "us", "finance.us")


def run_z3(spec: Spec) -> list[dict]:
    try:
        import z3
    except ImportError:
        return [
            {
                "id": "namespace.identity",
                "kind": "z3",
                "status": "incomplete",
                "detail": "z3-solver is unavailable",
            }
        ]

    # All paths have one or two components chosen from this finite domain.
    a, b, c, d = z3.Strings("a b c d")
    n, m = z3.Ints("n m")
    constraints = [z3.Or(n == 1, n == 2), z3.Or(m == 1, m == 2)]
    constraints += [z3.Or(*[v == item for item in Z3_DOMAIN]) for v in (a, b, c, d)]
    different = z3.Or(n != m, a != c, z3.And(n == 2, m == 2, b != d))
    left = z3.If(n == 1, a, z3.Concat(a, ".", b))
    right = z3.If(m == 1, c, z3.Concat(c, ".", d))
    solver = z3.Solver()
    solver.set(timeout=5000)
    solver.add(*constraints, different, left == right)
    legacy_query = solver.sexpr()
    collision = solver.check()
    witness = None
    if collision == z3.sat:
        model = solver.model()

        def path(length, first, second):
            return [model.eval(first).as_string()] + (
                [model.eval(second).as_string()] if model.eval(length).as_long() == 2 else []
            )

        witness = [path(n, a, b), path(m, c, d)]
    legacy = {
        "id": "namespace.identity",
        "check": "legacy-collision",
        "kind": "z3",
        "status": "pass"
        if collision == z3.sat
        else "incomplete"
        if collision == z3.unknown
        else "violation",
        "detail": "A dot-joined key collides for distinct paths"
        if witness
        else f"collision search: {collision}",
        "result": str(collision),
        "witness": witness,
        "query": legacy_query,
        "solver_version": z3.get_full_version(),
        "assumptions": ["one or two components", "finite component domain"],
    }

    solver = z3.Solver()
    solver.set(timeout=5000)
    solver.add(*constraints, n == m, a == c, z3.Or(n == 1, b == d))
    nonvacuous = solver.check()
    solver.add(different)
    list_query = z3.Solver()
    list_query.add(*solver.assertions())
    collision = solver.check()
    status = (
        "pass"
        if nonvacuous == z3.sat and collision == z3.unsat
        else "incomplete"
        if z3.unknown in (nonvacuous, collision)
        else "violation"
    )
    encoded = {
        "id": "namespace.identity",
        "check": "list-identity",
        "kind": "z3",
        "status": status,
        "detail": "List encoding is injective within the bounded domain"
        if status == "pass"
        else "List encoding check did not establish injectivity and nonvacuity",
        "result": str(collision),
        "nonvacuity": str(nonvacuous),
        "query": list_query.sexpr(),
        "solver_version": z3.get_full_version(),
        "assumptions": ["one or two components", "finite component domain"],
    }
    return [legacy, encoded]


def run_lean(lean_dir: Path) -> list[dict]:
    result = {
        "id": "namespace.properties",
        "check": "restore-lookup",
        "kind": "lean",
        "status": "incomplete",
        "detail": "Lean check did not run",
        "assumptions": ["distinct paths", "complete snapshot", "successful restore"],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="mentat-lean-") as tmp:
            project = Path(tmp) / "lean"
            shutil.copytree(
                lean_dir, project, ignore=shutil.ignore_patterns("*.olean", "*.ilean", ".lake")
            )
            if (project / "lean-toolchain").read_text().strip() != "leanprover/lean4:v4.31.0":
                result["detail"] = "Lean toolchain is not pinned to 4.31.0"
                return [result]
            # Let the pinned Lake toolchain select its own compiler and libraries.
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in ("LEAN_PATH", "LEAN_SRC_PATH", "LAKE_OVERRIDE_LEAN")
            }
            version = subprocess.run(
                ["lake", "env", "lean", "--version"],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if version.returncode != 0 or not version.stdout.startswith("Lean (version 4.31.0,"):
                result["detail"] = "Lake must select Lean 4.31.0"
                return [result]
            build = subprocess.run(
                ["lake", "build"],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if build.returncode:
                result["detail"] = "Lean project did not build"
                result["result"] = (build.stdout + build.stderr)[-8000:]
                return [result]
            run = subprocess.run(
                ["lake", "env", "lean", "Check.lean"],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            output = run.stdout + run.stderr
            result["result"] = {"exit_code": run.returncode, "output": output[-8000:]}
            result["lean_version"] = version.stdout.strip()
            axiom_line = next(
                (
                    line.strip()
                    for line in output.splitlines()
                    if line.startswith("'restore_lookup' ")
                ),
                "",
            )
            allowed_audits = {
                "'restore_lookup' does not depend on any axioms",
                "'restore_lookup' depends on axioms: [propext, Quot.sound]",
            }
            result["axiom_audit"] = axiom_line
            if run.returncode == 0 and axiom_line in allowed_audits:
                result.update(
                    status="pass",
                    detail="Lean checked restore_lookup; only audited standard axioms are used",
                )
            else:
                result["detail"] = "Lean failed or the theorem's axiom audit was missing"
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["detail"] = f"Lean unavailable or timed out: {exc}"
    return [result]


def run_formal(spec: Spec, lean_dir: Path) -> list[dict]:
    return run_z3(spec) + run_lean(lean_dir)
