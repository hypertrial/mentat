# Contributing to Mentat

Use Python 3.12, uv, Git, and Node.js 26.10.0. Run the full gate from the checkout before every push; the required CI check does not run it.

```sh
uv sync --locked --extra polaris
scripts/verify
```

`scripts/verify` runs ruff, the claims check, then the test suite. Tests run in parallel by default (`addopts` in `pyproject.toml`); add `-n 0` to a `pytest` command for a serial run.

The gate also lints with ruff's bugbear (`B`) and pyupgrade (`UP`) groups and type-checks `src/mentat` with pyright in basic mode (`showcases/` is excluded for now). `tests/test_static_checks.py` runs pyright once; pyright and its Node engine come from the `dev` group (`pyright[nodejs]`), so the check runs offline after `uv sync`. Keep `# noqa` and `# pyright: ignore[rule]` comments narrow, naming the rule, with a short reason. Pyright reports stale ignores as errors.

Expect the Lean tests and the opt-in Nano ID and Iceberg Go upstream replays to skip when their prerequisites are absent, and the real proof-checker fixtures to skip outside Linux.

The required **Mentat checks** job parses tracked Python files and checks the
changed commit's whitespace. It does not install dependencies or run tests. The
local gate is the source of truth for verification; report its result in the PR.

Before release, run `scripts/verify-release` on both Linux and Apple Silicon
macOS. It checks the complete applicable suite without skips, builds both
artifacts, and verifies their contents and installed behavior. Install Python
3.12, uv, Node.js 26.10.0, Go 1.26.3, Lean 4.31.0, and the local replay
mirrors first, then set `MENTAT_NANOID_SOURCE` and
`MENTAT_ICEBERG_GO_SOURCE` to their absolute paths. For example:

```sh
git clone --mirror https://github.com/ai/nanoid.git /absolute/path/nanoid.git
git clone --mirror https://github.com/apache/iceberg-go.git /absolute/path/iceberg-go.git
export MENTAT_NANOID_SOURCE=/absolute/path/nanoid.git
export MENTAT_ICEBERG_GO_SOURCE=/absolute/path/iceberg-go.git
scripts/verify-release
```

The Lean 4.31.0 release archive SHA-256 pins are
`07a633cc8d9151cbc08825ea4cdda50d4b02a2c9cb852c0131b13046f49cad7f`
for Linux and `264105500c8abdf37b68ffe03390a783ed259807807222698da8dd92d6ce0a27`
for Apple Silicon macOS. Verify the archive before installing it. On Linux,
install the protected proof checker separately with `bash scripts/install-proof-checker`;
the release command requires the real checker and fails if it is unavailable. It
does not provision the checker or use sudo. On macOS the Linux-only checker
tests are excluded; all remaining tests must run without skips.

To print and verify the pinned checker tree digest, run the installer and then:

```sh
python3 -I -c 'import platform, sys; sys.path.insert(0, "src"); from mentat import proof_checker; print(platform.machine(), proof_checker._toolchain_digest()); proof_checker._pinned_toolchain_digest()'
```

Run it on each supported Linux architecture when updating the toolchain pin.

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
the same interpreter and fixtures. The test suite checks results and subprocess counts;
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

The profiler also times the runner's `_process`, `_stage`, and `_docker` helpers
as `runner.process`, `runner.stage` (host runner only; the Docker runner stages
with an archive), and `runner.docker.<operation>` (such as `create`, `cp`,
`start`, and `rm`). Polaris Docker calls keep the `docker.*` names. To profile the
Docker runner, prepare the quickstart repository, its image, and a Docker
context, then approve and profile it locally:

```sh
PROFILE_DIR=$(mktemp -d)
sh examples/quickstart/create.sh "$PROFILE_DIR/quickstart"
repo="$PROFILE_DIR/quickstart"
image=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["docker_image"])' "$repo/.mentat/contract.json")
docker context create mentat --docker 'host=unix:///var/run/docker.sock'
docker --context mentat pull "$image"
mkdir -m 700 "$PROFILE_DIR/state"
base=$(git -C "$repo" rev-parse mentat-demo-base)
fixed=$(git -C "$repo" rev-parse mentat-demo-fixed)
mentat() { uv run --locked --no-sync mentat --state "$PROFILE_DIR/state" --docker-context mentat "$@"; }
mentat doctor --repo "$repo" --base "$base" --runner docker
digest=$(mentat spec check --repo "$repo" --base "$base" --runner docker --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["contract_digest"])')
mentat spec approve "$digest" --repo "$repo" --base "$base" --runner docker --trusted-chat-approval
MENTAT_PROFILE_TRACE="$PROFILE_DIR/trace.jsonl" uv run --locked --no-sync python scripts/profile_runtime.py -- \
  --state "$PROFILE_DIR/state" --docker-context mentat verify --repo "$repo" --base "$base" --rev "$fixed" --runner docker --json
```

The trace remains in `PROFILE_DIR` for inspection. Summarize inclusive phase
timings (nested phases overlap):

```sh
python3 - "$PROFILE_DIR/trace.jsonl" <<'PYCODE'
import collections, json, statistics, sys
phases = collections.defaultdict(list)
with open(sys.argv[1], encoding="utf-8") as trace:
    for line in trace:
        event = json.loads(line)
        phases[event["phase"]].append(event["elapsed_seconds"])
if not phases:
    raise SystemExit("profile trace has no events")
for phase, values in sorted(phases.items()):
    print(f"{phase}: count={len(values)} total={sum(values):.3f}s median={statistics.median(values):.3f}s")
PYCODE
```

Each Docker call appears twice, as `runner.docker.<operation>` and as the
`runner.process` call that runs it, and the Docker runner's archive staging and
artifact extraction are not timed.

## Regenerate the saved-record corpus

The saved-record corpus lives in `tests/fixtures/records/`, with labels
`v0.1.2` and `main-pre-split`. On a prepared Linux host with uv, Docker and
the required runtime images, generate both pinned historical revisions locally:

```sh
scripts/record-fixtures /absolute/path/to/new-record-fixtures
```

The destination must not already exist. Generation requires every scenario;
missing prerequisites cause failure. Saved records include absolute paths,
interpreter paths and PATH, so review generated artifacts for local information
before committing. Replace the two corpus directories with their corresponding
generated directories, then regenerate and review the comparison goldens:

```sh
MENTAT_UPDATE_GOLDENS=1 uv run --locked --no-sync pytest -q -s tests/test_record_fixtures.py -n 0
uv run --locked --no-sync pytest -q tests/test_record_fixtures.py -n 0
```

For vulnerabilities, use the private channel in [SECURITY.md](SECURITY.md).
