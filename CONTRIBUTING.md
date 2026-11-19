# Contributing to Mentat

Use Python 3.12, uv, Git, and Node.js 26.10.0. To reproduce the required Linux check, run these commands from the checkout.

```sh
uv sync --locked --extra polaris
uv run --locked --no-sync ruff check .
uv run --locked --no-sync mentat claims check --repo . --base "$(git rev-parse HEAD)"
uv run --locked --no-sync pytest -q -rs
```

Expect the Lean tests and the opt-in Nano ID and Iceberg Go upstream replays to skip when their prerequisites are absent. The required check runs on pull requests and pushes to `main`.

Before publishing a release, run the **Release verification** workflow manually on the release candidate. It runs the full suite and checks the built wheel and source distribution on Linux and Apple Silicon macOS. It provisions Lean 4.31.0, Go, and both upstream mirrors, and fails if any tests skip.

Open a focused pull request with the behavior change, its reproduction, and the
checks you ran. When claim text or check associations change, regenerate and
commit the Claims Spec with the contract.

```sh
uv run --locked mentat claims render --repo .
```

For vulnerabilities, use the private channel in [SECURITY.md](SECURITY.md).
