"""Verify a deliberately small, pure Python function against a symbolic contract.

This is a source-level proof under the assumptions reported in every result; candidate
code is parsed as data and is never imported or executed.
"""

import ast
import ctypes
import hashlib
import importlib
import json
import operator
import platform
import resource
import sys
import tempfile
from pathlib import Path

from . import gitio, process

_MAX_SOURCE = 100_000
_MAX_PREDICATE = 4096
_MAX_NODES = 1200
_MAX_DEPTH = 80
_MAX_MODULE_NODES = 30_000
_MAX_MODULE_DEPTH = 100
_MAX_INT_BITS = 256
_ASSUMPTIONS = [
    "The selected function is called with exact Python int and bool values as declared.",
    "The committed function is the function bound and called; module execution, imports, decorators elsewhere, and callers are outside this proof.",
    "The source translator, Python runtime, Z3 solver, and Git object verification are trusted.",
    "String literals are limited to printable ASCII without backslashes.",
]


class Unsupported(ValueError):
    """Input is outside the source verifier's supported language."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse(text: str, mode: str) -> ast.AST:
    try:
        tree = ast.parse(text, mode=mode)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise Unsupported("invalid Python syntax") from exc
    _check_ast(tree, module=mode == "exec")
    return tree


def _check_ast(tree: ast.AST, *, module: bool = False) -> None:
    # Only the selected function and predicates are translated. The full module
    # needs its own bounded traversal, but its unrelated literals are not proof inputs.
    max_nodes = _MAX_MODULE_NODES if module else _MAX_NODES
    max_depth = _MAX_MODULE_DEPTH if module else _MAX_DEPTH
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > max_nodes or depth > max_depth:
            raise Unsupported("AST size or depth limit exceeded")
        if not module and isinstance(node, ast.Constant):
            if type(node.value) is int and node.value.bit_length() > _MAX_INT_BITS:
                raise Unsupported("integer literal limit exceeded")
            if isinstance(node.value, str) and len(node.value) > 4096:
                raise Unsupported("string literal limit exceeded")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))


def _worker(data: dict) -> dict:
    try:
        z3 = importlib.import_module("z3")
    except ImportError as exc:
        raise Unsupported("Z3 is unavailable") from exc

    source, inputs = data["source"], data["inputs"]
    tree = _parse(source, "exec")
    assert isinstance(tree, ast.Module)
    # Parsing alone accepts contextually invalid code such as module-level break.
    # Compilation validates those contexts without importing or executing candidate code.
    try:
        compile(tree, "<candidate>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError, TypeError, RecursionError) as exc:
        raise Unsupported("source cannot be compiled") from exc
    matches = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == data["function"]
    ]
    # Other assignments or imports can change the binding; that is an explicit assumption.
    if len(matches) != 1 or not isinstance(matches[0], ast.FunctionDef):
        raise Unsupported("expected exactly one top-level synchronous function definition")
    fn = matches[0]
    _check_ast(fn)
    allowed_expr = (
        ast.Constant,
        ast.Name,
        ast.UnaryOp,
        ast.BinOp,
        ast.BoolOp,
        ast.IfExp,
        ast.Compare,
    )
    for statement in fn.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.stmt) and not isinstance(
                node, (ast.Assign, ast.Return, ast.If)
            ):
                raise Unsupported(f"unsupported statement: {type(node).__name__}")
            if isinstance(node, ast.expr) and not isinstance(node, allowed_expr):
                raise Unsupported(f"unsupported expression: {type(node).__name__}")
    args = fn.args
    if (
        fn.decorator_list
        or args.posonlyargs
        or args.vararg
        or args.kwonlyargs
        or args.kwarg
        or args.defaults
        or args.kw_defaults
    ):
        raise Unsupported("decorators, defaults, and nonstandard parameters are unsupported")
    names = [a.arg for a in args.args]
    if len(set(names)) != len(names) or set(names) != set(inputs) or "result" in names:
        raise Unsupported("inputs must exactly match distinct parameter names; result is reserved")
    if any(t not in ("int", "bool") for t in inputs.values()):
        raise Unsupported("input types must be int or bool")
    env = {n: (z3.Int(n) if inputs[n] == "int" else z3.Bool(n), inputs[n]) for n in names}

    def expr(node, scope):
        if isinstance(node, ast.Constant):
            if type(node.value) is bool:
                return z3.BoolVal(node.value), "bool"
            if type(node.value) is int:
                return z3.IntVal(node.value), "int"
            if type(node.value) is str:
                # Z3 interprets backslash escape sequences in StringVal. Restrict the
                # alphabet to values for which its parser preserves Python's literal.
                if any(ord(c) < 32 or ord(c) > 126 or c == "\\" for c in node.value):
                    raise Unsupported("string literal must be printable ASCII without backslashes")
                return z3.StringVal(node.value), "str"
        if isinstance(node, ast.Name) and node.id in scope:
            return scope[node.id]
        if isinstance(node, ast.UnaryOp):
            a, typ = expr(node.operand, scope)
            if isinstance(node.op, ast.Not) and typ == "bool":
                return z3.Not(a), "bool"
            if isinstance(node.op, (ast.USub, ast.UAdd)) and typ == "int":
                return (-a if isinstance(node.op, ast.USub) else a), "int"
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            a, at = expr(node.left, scope)
            b, bt = expr(node.right, scope)
            if at == bt == "int":
                return (a + b if isinstance(node.op, ast.Add) else a - b), "int"
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [expr(n, scope) for n in node.values]
            if all(t == "bool" for _, t in values):
                return (z3.And if isinstance(node.op, ast.And) else z3.Or)(
                    *[v for v, _ in values]
                ), "bool"
        if isinstance(node, ast.IfExp):
            test, tt = expr(node.test, scope)
            yes, yt = expr(node.body, scope)
            no, nt = expr(node.orelse, scope)
            if tt == "bool" and yt == nt:
                return z3.If(test, yes, no), yt
        if isinstance(node, ast.Compare):
            values = [expr(n, scope) for n in [node.left, *node.comparators]]
            parts = []
            for (a, at), op, (b, bt) in zip(values[:-1], node.ops, values[1:], strict=True):
                if at != bt:
                    raise Unsupported("comparison type mismatch")
                if isinstance(op, ast.Eq):
                    parts.append(a == b)
                elif isinstance(op, ast.NotEq):
                    parts.append(a != b)
                elif at == "int" and isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                    parts.append(
                        {
                            ast.Lt: operator.lt,
                            ast.LtE: operator.le,
                            ast.Gt: operator.gt,
                            ast.GtE: operator.ge,
                        }[type(op)](a, b)
                    )
                else:
                    raise Unsupported("unsupported comparison")
            return z3.And(*parts), "bool"
        raise Unsupported(f"unsupported expression: {type(node).__name__}")

    assigned = set(names) | {"result"}
    returns = []

    def block(statements, states):
        for stmt in statements:
            next_states = []
            if isinstance(stmt, ast.Assign):
                if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                    raise Unsupported("only simple local assignment is supported")
                name = stmt.targets[0].id
                if name in assigned:
                    raise Unsupported("local rebinding is unsupported")
                assigned.add(name)
                for guard, scope in states:
                    value = expr(stmt.value, scope)
                    next_states.append((guard, {**scope, name: value}))
            elif isinstance(stmt, ast.Return) and stmt.value is not None:
                for guard, scope in states:
                    returns.append((guard, *expr(stmt.value, scope)))
            elif isinstance(stmt, ast.If):
                for guard, scope in states:
                    test, typ = expr(stmt.test, scope)
                    if typ != "bool":
                        raise Unsupported("if condition must be bool")
                    next_states.extend(block(stmt.body, [(z3.And(guard, test), scope.copy())]))
                    next_states.extend(
                        block(stmt.orelse, [(z3.And(guard, z3.Not(test)), scope.copy())])
                    )
            else:
                raise Unsupported(f"unsupported statement: {type(stmt).__name__}")
            states = next_states
        return states

    remaining = block(fn.body, [(z3.BoolVal(True), env)])
    if remaining or not returns:
        raise Unsupported("all paths must return")
    types = {t for _, _, t in returns}
    if len(types) != 1:
        raise Unsupported("return types differ")
    result = returns[-1][1]
    for guard, value, _ in reversed(returns[:-1]):
        result = z3.If(guard, value, result)
    pre_ast = _parse(data["requires"], "eval")
    post_ast = _parse(data["ensures"], "eval")
    assert isinstance(pre_ast, ast.Expression) and isinstance(post_ast, ast.Expression)
    pre, pt = expr(pre_ast.body, env)
    post, qt = expr(post_ast.body, {**env, "result": (result, types.pop())})
    if pt != "bool" or qt != "bool":
        raise Unsupported("predicates must be bool")
    solver = z3.Solver()
    solver.set(timeout=data["timeout_ms"])
    solver.add(pre)
    first = solver.check()
    out = {"precondition": str(first), **_solver_identity(z3)}
    if first != z3.sat:
        out.update(
            status="incomplete",
            reason="precondition is unsatisfiable"
            if first == z3.unsat
            else "precondition solver returned unknown",
        )
        return out
    solver.add(z3.Not(post))
    second = solver.check()
    out["violation"] = str(second)
    if second == z3.unsat:
        out["status"] = "proved"
    elif second == z3.sat:
        model = solver.model()
        out["status"] = "counterexample"
        out["counterexample"] = {
            n: (
                z3.is_true(model.eval(v, model_completion=True))
                if t == "bool"
                else model.eval(v, model_completion=True).as_long()
            )
            for n, (v, t) in env.items()
        }
    else:
        out.update(status="incomplete", reason="violation solver returned unknown")
    return out


def _binding_digest(package: Path) -> str:
    """Hash package code and relative paths, excluding derived bytecode caches."""
    files = sorted(
        p
        for p in package.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.relative_to(package).parts
        and p.suffix in {".py", ".pyc", ".so", ".pyd", ".dylib"}
    )
    if not files or not (package / "__init__.py").is_file():
        raise Unsupported("Z3 binding package identity unavailable")
    digest = hashlib.sha256()
    for path in files:
        name = path.relative_to(package).as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        with path.open("rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()


def _solver_identity(z3) -> dict:
    # ctypes retains the CDLL owner of the actual function pointer in _objects.
    function = z3.z3core.Z3_get_version.__defaults__[0].f
    owners = [
        value for value in (function._objects or {}).values() if isinstance(value, ctypes.CDLL)
    ]
    if len(owners) != 1 or not Path(owners[0]._name).is_absolute():
        raise Unsupported("native Z3 library identity unavailable")
    library = Path(owners[0]._name).resolve(strict=True)
    with library.open("rb") as stream:
        library_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "solver_library": str(library),
        "solver_library_sha256": library_hash,
        "solver_version": z3.get_version_string(),
        "solver_binding_sha256": _binding_digest(Path(z3.__file__).resolve(strict=True).parent),
    }


def _validate_worker_result(result: object, inputs: dict[str, str]) -> None:
    """Accept only the worker's result fields, never its source identity or contract."""
    if not isinstance(result, dict):
        raise Unsupported("invalid source proof worker response")
    allowed = {
        "status",
        "reason",
        "precondition",
        "violation",
        "solver_version",
        "solver_binding_sha256",
        "counterexample",
        "python_version",
        "python_implementation",
        "solver_library",
        "solver_library_sha256",
    }
    if set(result) - allowed or result.get("status") not in {
        "proved",
        "counterexample",
        "incomplete",
    }:
        raise Unsupported("invalid source proof worker response")
    status = result["status"]
    if status == "incomplete":
        if not isinstance(result.get("reason"), str):
            raise Unsupported("invalid source proof worker response")
    elif result.get("precondition") != "sat" or result.get("violation") != (
        "unsat" if status == "proved" else "sat"
    ):
        raise Unsupported("invalid source proof worker response")
    if status != "incomplete":
        if any(
            not isinstance(result.get(key), str) or not result[key]
            for key in (
                "solver_version",
                "solver_binding_sha256",
                "python_version",
                "python_implementation",
                "solver_library",
                "solver_library_sha256",
            )
        ):
            raise Unsupported("invalid source proof worker response")
    if status == "counterexample":
        witness = result.get("counterexample")
        if (
            not isinstance(witness, dict)
            or set(witness) != set(inputs)
            or any(type(witness[n]) is not (int if t == "int" else bool) for n, t in inputs.items())
        ):
            raise Unsupported("invalid source proof worker response")
    elif "counterexample" in result:
        raise Unsupported("invalid source proof worker response")


def source_tool_identity() -> dict:
    """Identify the current trusted verifier in an isolated, bounded process."""
    bootstrap = (
        "import sys, types; p=types.ModuleType('mentat'); "
        "p.__path__=[sys.argv[1]]; sys.modules['mentat']=p; "
        "from mentat.source_verify import _identity_main; _identity_main()"
    )
    try:
        with tempfile.TemporaryDirectory() as workdir:
            code, output, _ = process.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-X",
                    f"pycache_prefix={workdir}/pycache",
                    "-c",
                    bootstrap,
                    str(Path(__file__).resolve().parent),
                ],
                cwd=workdir,
                env={},
                output_limit=16384,
                timeout=8,
                stderr=False,
            )
        if code:
            raise ValueError("source tool identity worker failed")
        identity = json.loads(output)
        fields = {
            "python_version",
            "python_implementation",
            "solver_version",
            "solver_binding_sha256",
            "solver_library_sha256",
        }
        if not isinstance(identity, dict) or set(identity) != fields:
            raise ValueError("invalid source tool identity")
        if any(not isinstance(identity[k], str) or not identity[k] for k in fields):
            raise ValueError("invalid source tool identity")
        for key in ("solver_binding_sha256", "solver_library_sha256"):
            if len(identity[key]) != 64 or any(c not in "0123456789abcdef" for c in identity[key]):
                raise ValueError("invalid source tool identity")
        identity["frontend_sha256"] = _digest(Path(__file__).read_bytes())
        return identity
    except (OSError, TimeoutError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("source tool identity unavailable") from exc


def _identity_main() -> None:
    # A worker crash or missing dependency is an unknown identity, never a match.
    z3 = importlib.import_module("z3")
    identity = _solver_identity(z3)
    del identity["solver_library"]
    print(json.dumps(identity))


def verify_source(
    repo: Path,
    rev: str,
    path: str,
    function: str,
    inputs: dict[str, str],
    requires: str,
    ensures: str,
    *,
    timeout_ms: int = 5000,
) -> dict:
    """Return proof evidence or an explicit incomplete result for the committed function."""
    evidence = {
        "schema_version": 1,
        "method": "source-proof",
        "status": "incomplete",
        "assumptions": _ASSUMPTIONS.copy(),
        "source_identity": {"revision": rev, "path": path, "function": function},
        "contract": {"inputs": inputs, "requires": requires, "ensures": ensures},
    }
    try:
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or any(p in ("", ".", "..") for p in path.split("/"))
            or "\x00" in path
        ):
            raise Unsupported("invalid repository path")
        if (
            not isinstance(function, str)
            or not function.isidentifier()
            or not isinstance(inputs, dict)
            or any(
                not isinstance(k, str) or not k.isidentifier() or not isinstance(v, str)
                for k, v in inputs.items()
            )
        ):
            raise Unsupported("invalid function or inputs")
        if (
            not isinstance(requires, str)
            or not isinstance(ensures, str)
            or max(len(requires), len(ensures)) > _MAX_PREDICATE
        ):
            raise Unsupported("predicate size limit exceeded")
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30000:
            raise Unsupported("invalid timeout")
        root = gitio.repo_root(repo)
        revision = gitio.verify_revision(root, rev)
        raw = gitio.read_file(root, revision, path)
        if len(raw) > _MAX_SOURCE:
            raise Unsupported("source size limit exceeded")
        source = raw.decode("utf-8")
        evidence["source_identity"].update(
            source_sha256=_digest(raw),
            blob_oid=_digest(b"blob " + str(len(raw)).encode() + b"\0" + raw)
            if len(revision) == 64
            else hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
        )
        evidence["contract_sha256"] = _digest(
            json.dumps(evidence["contract"], sort_keys=True, separators=(",", ":")).encode()
        )
        evidence["frontend_sha256"] = _digest(Path(__file__).read_bytes())
        data = {
            "source": source,
            "function": function,
            "inputs": inputs,
            "requires": requires,
            "ensures": ensures,
            "timeout_ms": timeout_ms,
        }
        # Isolated mode ignores PYTHONPATH and cwd. Register only this trusted
        # package directory without adding it to sys.path, where it could shadow z3.
        bootstrap = (
            "import sys, types; p=types.ModuleType('mentat'); "
            "p.__path__=[sys.argv[1]]; sys.modules['mentat']=p; "
            "from mentat.source_verify import _main; _main()"
        )
        with tempfile.TemporaryFile() as stream, tempfile.TemporaryDirectory() as workdir:
            stream.write(json.dumps(data).encode())
            stream.seek(0)
            code, output, _ = process.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-X",
                    f"pycache_prefix={workdir}/pycache",
                    "-c",
                    bootstrap,
                    str(Path(__file__).resolve().parent),
                ],
                stdin=stream,
                cwd=workdir,
                env={},
                output_limit=16384,
                timeout=timeout_ms / 1000 + 3,
                stderr=False,
            )
        if code:
            raise Unsupported("source proof worker failed")
        result = json.loads(output)
        _validate_worker_result(result, inputs)
        evidence.update(result)
    except (ValueError, OSError, UnicodeError, TimeoutError, RuntimeError, RecursionError) as exc:
        evidence["reason"] = str(exc)
    return evidence


def _main() -> None:
    try:
        # Bound the solver process, independent of the parent deadline.
        if sys.platform != "darwin":
            resource.setrlimit(resource.RLIMIT_AS, (1_500_000_000, 1_500_000_000))
        data = json.load(sys.stdin)
        result = _worker(data)
    except (Unsupported, ValueError, TypeError, RecursionError) as exc:
        result = {"status": "incomplete", "reason": str(exc)}
    print(json.dumps(result))


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    _main()
