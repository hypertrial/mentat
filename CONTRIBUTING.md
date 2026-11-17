# Contributing to Mentat

Use Python 3.12, uv, and Git. Set up the project from a clone.

```sh
uv sync --locked --extra polaris
uv run --locked ruff check .
uv run --locked --extra polaris pytest -q -rs
uv run --locked mentat claims check --repo . --base "$(git rev-parse HEAD)"
```

The full suite also needs Lean 4.31.0 and Node.js 26.10.0 on `PATH` and a local
[Nano ID mirror](docs/nanoid.md#run-the-opt-in-integration-test) set in
`MENTAT_NANOID_SOURCE`. CI provisions these dependencies.

Open a focused pull request with the behavior change, its reproduction, and the
checks you ran. When claim text or check associations change, regenerate and
commit the Claims Spec with the contract.

```sh
uv run --locked mentat claims render --repo .
```

For vulnerabilities, use the private channel in [SECURITY.md](SECURITY.md).
