# Project agent notes

Repository: `hypertrial/mentat`

Visibility: public

## Verification

- Fast: `uv run --locked --no-sync ruff check . && sh -c 'uv run --locked --no-sync mentat claims check --repo . --base "$(git rev-parse HEAD)"'`
- Completion: `uv run --locked --no-sync ruff check . && sh -c 'uv run --locked --no-sync mentat claims check --repo . --base "$(git rev-parse HEAD)"' && uv run --locked --no-sync pytest -q -rs`

These commands are wrapped by `scripts/verify-fast` and `scripts/verify`.
The required hosted check only parses tracked Python and checks commit whitespace. Run local verification as the source of truth. Before release, run `scripts/verify-release` on Linux and Apple Silicon macOS with the prerequisites in CONTRIBUTING; it rejects skipped tests. Read README and CONTRIBUTING before changing behavior.
