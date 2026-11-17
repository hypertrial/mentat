"""Execute approved project proof inputs in fresh, bounded processes."""

import hashlib
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

_TIMEOUT = 15
_OUTPUT_LIMIT = 16_384
_LEAN_VERSION = "4.31.0"
# Match Lean 4.31's isIdFirst/isIdRest in Init.Meta.Defs before interpolating a name.
_LEAN_FIRST = (
    r"A-Za-z_\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u017f"
    r"\u0391-\u039f\u03a1-\u03a2\u03a4-\u03a9\u03b1-\u03ba\u03bc-\u03fb"
    r"\u1f00-\u1ffe\u2100-\u214f\U0001d49c-\U0001d59f"
)
_LEAN_PART = rf"[{_LEAN_FIRST}][{_LEAN_FIRST}0-9'!?\u2080-\u2089\u2090-\u209c\u1d62-\u1d6a\u2c7c]*"
_NAME = re.compile(rf"{_LEAN_PART}(?:\.{_LEAN_PART})*")
_COMMAND = re.compile(r'\(\s*(?:;[^\n]*\n\s*)*([a-zA-Z][a-zA-Z0-9_-]*)(?=[ \t\r\n();"|])')


def _env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tempfile.gettempdir(),
        "LANG": "C.UTF-8",
    }


def _run(args, *, input=None, cwd=None, timeout=_TIMEOUT, env=None):
    def limit_output():
        resource.setrlimit(resource.RLIMIT_FSIZE, (1_000_000, 1_000_000))

    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        with subprocess.Popen(
            args,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            cwd=cwd,
            env=env or _env(),
            stdout=out,
            stderr=err,
            text=True,
            preexec_fn=limit_output,
            start_new_session=True,
        ) as p:
            try:
                p.communicate(input, timeout=timeout)
            finally:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(
            args,
            p.returncode,
            out.read(_OUTPUT_LIMIT).decode("utf-8", "replace"),
            err.read(_OUTPUT_LIMIT).decode("utf-8", "replace"),
        )


def tool_info(project) -> dict:
    """Find selected tools without evaluating any project-controlled input."""
    result = {}
    engines = {check.engine for check in project.contract.formal}
    if "z3" in engines:
        try:
            import z3

            result["z3"] = {"version": z3.get_version_string(), "executable": sys.executable}
        except ImportError:
            result["z3"] = {"error": "Install the project's z3-solver dependency."}
    if "lean" in engines:
        executable = shutil.which("lean")
        if not executable:
            result["lean"] = {"error": "Install Lean 4.31.0 and put its bin directory on PATH."}
        else:
            executable = os.path.abspath(executable)
            try:
                p = _run([executable, "--version"], timeout=5)
                match = re.search(r"\bversion (\d+\.\d+\.\d+)\b", p.stdout)
                if p.returncode or not match or match.group(1) != _LEAN_VERSION:
                    result["lean"] = {
                        "error": "Lean 4.31.0 is required; check the executable on PATH."
                    }
                else:
                    lib = _run([executable, "--print-libdir"], timeout=5)
                    if lib.returncode or not Path(lib.stdout.strip()).is_dir():
                        result["lean"] = {"error": "Could not locate Lean's standard library."}
                    else:
                        result["lean"] = {
                            "version": match.group(1),
                            "executable": executable,
                            "libdir": lib.stdout.strip(),
                        }
            except (OSError, subprocess.TimeoutExpired) as exc:
                result["lean"] = {"error": f"Could not run Lean: {exc}"}
    errors = [info["error"] for info in result.values() if "error" in info]
    if errors:
        raise ValueError(" ".join(errors))
    return result


_Z3_WORKER = r"""
import json, sys
import z3
try:
    data = json.load(sys.stdin)
    base = z3.Solver()
    base.set(timeout=5000)
    base.from_string(data["source"])
    first = base.check()
    output = {"base": str(first), "version": z3.get_version_string()}
    if data["expected"] == "unsat" and first == z3.sat:
        target = z3.Solver()
        target.set(timeout=5000)
        target.from_string(data["source"] + "\n" + data["counterexample"])
        output["result"] = str(target.check())
    else:
        output["result"] = str(first)
    print(json.dumps(output))
except Exception as exc:
    print(json.dumps({"error": str(exc)[:4000]}))
"""


def _smt_commands(source: str) -> list[str]:
    # Ignore comments and quoted symbols/strings when locating top-level commands.
    commands = []
    depth = 0
    i = 0
    while i < len(source):
        char = source[i]
        if char == ";":
            end = source.find("\n", i)
            i = len(source) if end < 0 else end + 1
            continue
        if char in ('"', "|"):
            quote = char
            i += 1
            while i < len(source):
                if source[i] == quote:
                    if quote == '"' and i + 1 < len(source) and source[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                if source[i] == "\\" and quote == "|":
                    i += 1
                i += 1
            else:
                raise ValueError("unterminated SMT quoted value")
            continue
        if char == "(":
            if depth == 0:
                match = _COMMAND.match(source, i)
                if not match:
                    raise ValueError("invalid SMT command")
                commands.append(match.group(1))
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced SMT input")
        elif depth == 0 and not char.isspace():
            raise ValueError("invalid top-level SMT input")
        i += 1
    if depth:
        raise ValueError("unbalanced SMT input")
    return commands


def _run_z3(check, files, tools):
    tool = tools.get("z3", {})
    result = {
        "id": check.id,
        "claim": check.claim,
        "kind": "z3",
        "status": "incomplete",
        "detail": "Z3 did not run",
        "version": tool.get("version"),
        "evidence": {},
    }
    if "error" in tool or not tool.get("version"):
        result["detail"] = tool.get("error", "Z3 tool information is missing")
        return result
    try:
        source = files[check.source]
        counterexample = files[check.counterexample] if check.expected == "unsat" else b""
        result["evidence"] = {
            "source": check.source,
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "expected": check.expected,
        }
        if check.expected == "unsat":
            result["evidence"].update(
                counterexample=check.counterexample,
                counterexample_sha256=hashlib.sha256(counterexample).hexdigest(),
            )
        source_text, counter_text = source.decode("utf-8"), counterexample.decode("utf-8")
        allowed = {
            "set-logic",
            "declare-const",
            "declare-datatype",
            "declare-datatypes",
            "declare-fun",
            "declare-sort",
            "define-fun",
            "define-fun-rec",
            "define-funs-rec",
            "define-sort",
            "assert",
        }
        commands = set(_smt_commands(source_text))
        if not commands or commands - allowed:
            raise ValueError("base model must contain supported SMT commands")
        if check.expected == "unsat" and (
            not _smt_commands(counter_text) or set(_smt_commands(counter_text)) != {"assert"}
        ):
            raise ValueError("counterexample must contain only assertions")
        payload = json.dumps(
            {"source": source_text, "counterexample": counter_text, "expected": check.expected}
        )
        p = _run([tool.get("executable", sys.executable), "-I", "-c", _Z3_WORKER], input=payload)
        if p.returncode:
            result["detail"] = f"Z3 worker failed: {p.stderr[-2000:]}"
            return result
        output = json.loads(p.stdout)
        result["evidence"].update(output)
        if "error" in output:
            result["detail"] = f"Z3 could not parse or check the model: {output['error']}"
        elif output.get("version") != tool["version"]:
            result["detail"] = "Z3 worker version differs from detected version"
        elif output["base"] == "unknown" or output["result"] == "unknown":
            result["detail"] = "Z3 returned unknown"
        elif check.expected == "unsat" and output["base"] != "sat":
            result["detail"] = "The base model must be satisfiable before checking a counterexample"
        elif output["result"] == check.expected:
            result.update(status="pass", detail=f"Z3 returned {check.expected}")
        else:
            result.update(
                status="violation",
                detail=f"Z3 returned {output['result']}, expected {check.expected}",
            )
    except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
        result["detail"] = f"Z3 check incomplete: {exc}"
    return result


def _run_lean(check, files, tools):
    tool = tools.get("lean", {})
    result = {
        "id": check.id,
        "claim": check.claim,
        "kind": "lean",
        "status": "incomplete",
        "detail": "Lean did not run",
        "version": tool.get("version"),
        "evidence": {},
    }
    if "error" in tool or tool.get("version") != _LEAN_VERSION or not tool.get("executable"):
        result["detail"] = tool.get("error", "Lean 4.31.0 tool information is missing")
        return result
    if not _NAME.fullmatch(check.theorem):
        result["detail"] = "The theorem must be a plain qualified Lean identifier"
        return result
    try:
        source = files[check.source]
        result["evidence"] = {
            "source": check.source,
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "theorem": check.theorem,
        }
        with tempfile.TemporaryDirectory(prefix="mentat-project-lean-") as tmp:
            root = Path(tmp)
            (root / "Model.lean").write_bytes(source)
            env = _env()
            env["LEAN_PATH"] = str(root)
            deps = _run([tool["executable"], "--deps", str(root / "Model.lean")], cwd=root, env=env)
            libdir = Path(tool.get("libdir", "")).resolve()
            dependencies = deps.stdout.splitlines()

            def standard(dep):
                try:
                    relative = Path(dep).resolve().relative_to(libdir)
                except ValueError:
                    return False
                return relative.suffix == ".olean" and relative.parts[0].split(".")[0] in {
                    "Init",
                    "Lean",
                    "Std",
                }

            if deps.returncode or not all(standard(dep) for dep in dependencies):
                result["detail"] = "Lean source must import only Lean standard library modules"
                result["evidence"]["dependency_output"] = (deps.stdout + deps.stderr)[
                    -_OUTPUT_LIMIT:
                ]
                return result
            result["evidence"]["dependencies"] = [
                str(Path(dep).resolve().relative_to(libdir)) for dep in dependencies
            ]
            compile_result = _run(
                [tool["executable"], "-o", str(root / "Model.olean"), str(root / "Model.lean")],
                cwd=root,
                env=env,
            )
            result["evidence"]["compile_output"] = (compile_result.stdout + compile_result.stderr)[
                -_OUTPUT_LIMIT:
            ]
            if compile_result.returncode:
                result["detail"] = "Lean source did not compile"
                return result
            # Parse trusted audit code before loading any model-defined syntax or extensions.
            audit = f"""import Lean.CoreM
import Lean.Util.CollectAxioms
open Lean

def main : IO Unit := do
  let env ← importModules #[{{module := `Model}}] {{}} (loadExts := false) (level := .private)
  let decl := `{check.theorem}
  let some idx := env.getModuleIdx? `Model | throw <| IO.userError "Missing Model"
  unless env.getModuleIdxFor? decl == some idx do
    throw <| IO.userError "Expected a theorem declared in Model"
  match env.checked.get.find? decl with
  | some (.thmInfo _) => pure ()
  | _ => throw <| IO.userError "Expected a checked theorem"
  let axioms ← (collectAxioms decl : CoreM (Array Name)).toIO'
    {{fileName := "Audit.lean", fileMap := default}} {{env := env}}
  if axioms.isEmpty then
    IO.println s!"'{{decl}}' does not depend on any axioms"
  else
    IO.println s!"'{{decl}}' depends on axioms: [{{String.intercalate ", " (axioms.toList.map toString)}}]"
"""
            (root / "Audit.lean").write_text(audit)
            p = _run([tool["executable"], "--run", str(root / "Audit.lean")], cwd=root, env=env)
            output = p.stdout + p.stderr
            result["evidence"]["audit_output"] = output[-_OUTPUT_LIMIT:]
            if p.returncode:
                result["detail"] = "Lean theorem audit failed"
                return result
            prefix = f"'{check.theorem}' "
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            allowed = {"propext", "Classical.choice", "Quot.sound"}
            if len(lines) != 1:
                result["detail"] = "Lean did not produce an unambiguous theorem axiom audit"
                return result
            line = lines[0]
            if line == prefix + "does not depend on any axioms":
                axioms = []
            else:
                match = re.fullmatch(re.escape(prefix) + r"depends on axioms: \[([^\]]*)\]", line)
                if not match:
                    result["detail"] = "Lean produced an unrecognized theorem axiom audit"
                    return result
                axioms = [name.strip() for name in match.group(1).split(",")]
                if not all(axioms):
                    result["detail"] = "Lean produced an unrecognized theorem axiom audit"
                    return result
            result["evidence"]["axioms"] = axioms
            if set(axioms) - allowed:
                result["detail"] = "The theorem uses disallowed axioms"
                return result
            result.update(status="pass", detail="Lean checked the theorem and its axioms")
    except (KeyError, OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        result["detail"] = f"Lean check incomplete: {exc}"
    return result


def run_formal(project, tools: dict) -> list[dict]:
    """Run each declared check using only the previously approved in-memory files."""
    return [
        (_run_z3 if check.engine == "z3" else _run_lean)(check, project.files, tools)
        for check in project.contract.formal
    ]
