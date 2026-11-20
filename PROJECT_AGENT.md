# Project agent notes

Repository: `hypertrial/mentat`

Visibility: public

## Verification

- Fast: `uv run --locked --no-sync ruff check . && sh -c 'uv run --locked --no-sync mentat claims check --repo . --base "$(git rev-parse HEAD)"'`
- Completion: `uv run --locked --no-sync ruff check . && sh -c 'uv run --locked --no-sync mentat claims check --repo . --base "$(git rev-parse HEAD)"' && uv run --locked --no-sync pytest -q -rs`

These commands are wrapped by `scripts/verify-fast` and `scripts/verify`.
Read README, CONTRIBUTING, and CI workflows before changing behavior.
