# Contributing to Mentat

Use Python 3.12, uv, Git, and Node.js 26.10.0. To reproduce the required Linux check, run these commands from the checkout.

```sh
uv sync --locked --extra polaris
scripts/verify
```

`scripts/verify` runs ruff, the claims check, then the test suite. Tests run in parallel by default (`addopts` in `pyproject.toml`); add `-n 0` to a `pytest` command for a serial run.

Expect the Lean tests and the opt-in Nano ID and Iceberg Go upstream replays to skip when their prerequisites are absent. The required check runs on pull requests and pushes to `main`.

Before publishing a release, run the **Release verification** workflow manually on the release candidate. It runs the full suite and checks the built wheel and source distribution on Linux and Apple Silicon macOS. It provisions Lean 4.31.0, Go, and both upstream mirrors, and fails if any tests skip.

Open a focused pull request with the behavior change, its reproduction, and the
checks you ran. When claim text or check associations change, regenerate and
commit the Claims Spec with the contract.

```sh
uv run --locked mentat claims render --repo .
```

Classify each new top-level `src/mentat` module as a verifier or presentation module in `src/mentat/project.py`; Commands that load a project refuse to run, and the tests fail, until you do. Packages other than `showcases/` are refused.

## Measure import and runtime costs

Run the OpenSpec benchmark with the installed development environment:

```sh
uv run --locked --no-sync python scripts/benchmark_openspec.py
```

It creates temporary committed fixtures with 1, 32, and 128 capabilities,
checks the generated drafts, and reports Git blob-reading subprocess counts
and timings after one warm-up and five measured runs. Compare revisions with
the same interpreter and fixtures. CI checks results and subprocess counts;
elapsed times are diagnostic, without a timing threshold.

For runtime phase timings, run an existing Mentat command through the developer
profiler. The command still needs its usual approved contract, candidate,
toolchain, and prepared runner. The profiler does not provision them.

```sh
PROFILE_DIR=$(mktemp -d)
MENTAT_PROFILE_TRACE="$PROFILE_DIR/trace.jsonl" \
  uv run --locked --no-sync python scripts/profile_runtime.py -- \
  showcase polaris verify --repo "$POLARIS" --rev "$REVISION" --json
```

Only this script reads `MENTAT_PROFILE_TRACE`; normal Mentat commands do not
enable tracing. Use an absolute path whose parent already exists. The script
creates a private file, or appends to an operator-owned regular file without
group or other permissions. Unsafe or unwritable destinations are ignored.
Trace writing is best effort and does not change the command's result.
An event is skipped if another writer holds the trace lock; trace collection
never waits for that lock. User cancellation still stops the command.

JSONL events contain generated invocation IDs, sequence numbers, fixed phase
names, durations, and outcomes. They exclude command arguments, paths, input,
output, exception text, and environment values. Nested timings are inclusive;
do not add parent and child durations together. Project formal subprocess
durations include process startup, I/O, scheduling, and teardown. A project Z3
worker triggers separate fresh Python and Z3-import calibration samples to
estimate that overhead; they do not isolate solver time. Timings stay outside
verification evidence bundles.

## Regenerate the saved-record corpus

The saved-record corpus lives in `tests/fixtures/records/`, with one directory per
label (`v0.1.2` and `main-pre-split`). Saved records embed absolute paths, the
interpreter path, and `PATH`, so the corpus must come only from the **Record
fixtures** workflow, never from a workstation.

1. Run the **Record fixtures** workflow on the branch, from the Actions tab or
   with `gh workflow run record-fixtures.yml --ref BRANCH`.
2. Download the `record-fixtures` artifact and extract it.
3. Replace `tests/fixtures/records/v0.1.2/` and
   `tests/fixtures/records/main-pre-split/` with the matching directories from
   the artifact.
4. Write the goldens once. `MENTAT_UPDATE_GOLDENS=1` rewrites each
   `compare-<n>.json` golden and fails if `CI` is set, so run it locally. Use
   `-s` to print each rewritten golden path. Then review the diff and run the
   tests without the variable.

```sh
MENTAT_UPDATE_GOLDENS=1 uv run --locked --no-sync pytest -q -s tests/test_record_fixtures.py -n 0
uv run --locked --no-sync pytest -q tests/test_record_fixtures.py -n 0
```

For vulnerabilities, use the private channel in [SECURITY.md](SECURITY.md).
