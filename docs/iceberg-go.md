# Iceberg-Go compaction replay

This retrospective example checks the [historical Apache Iceberg-Go compaction
fix #2288](https://github.com/apache/iceberg-go/pull/2288), authored by Matt Faltyn.
The [original reproduction](https://github.com/apache/iceberg-go/issues/2287)
starts with twelve rows in one Parquet file. With split tasks and partial
progress, the older implementation commits one row, then errors. Only that row
remains visible in the active snapshot. The original file and older snapshot
can remain available for recovery; this is not a claim of permanent physical
destruction.

## Contract and recorded behavior

| Check | Before `410b789` | Fix `3fb9109` |
| --- | --- | --- |
| Primary fixture input | 12 rows | 12 rows |
| Primary fixture final active table | 1 row | 12 rows |
| Approved row-preservation cases | 4 pass, 4 fail | 8 pass |
| Lean specification | Pass | Pass |
| Z3 specification | Pass | Pass |

The matrix varies split/unsplit scan tasks, atomic/partial-progress commits,
and planner residual filters. The `partial` case label means `PartialProgress`;
it is not a separate selection of a partial byte range. The probe uses twelve
one-row Parquet groups and the historical APIs at these exact revisions:

- Before: `410b789ed781852bbb4376e4f5cb57e76da12c56`
- Fix: `3fb910983b9674efc6092418af90a1c2b821dd42`

The approved Go probe records the initial rows, each committed snapshot, the
final active table, and the operation error independently. The Python checker
compares complete row tuples as multisets. A failed operation whose completed
observations preserve all rows can pass this preservation property; that does
not establish operation success. Setup, compilation, scan, protocol, or missing
case failures make verification incomplete.

Lean and Z3 check a conditional multiplicity specification: whole-file
ownership, exact copying of selected rows, and atomic publication preserve
each row's multiplicity. Lean also checks preservation through committed
prefixes. Both models pass on both revisions. They do not verify Go source;
the native observations distinguish the implementations. The fixture excludes
logical deletes and concurrent writers, and does not establish arbitrary
catalog, storage, or crash-durability behavior.

## Install and review

Use the [versioned installation instructions](../README.md#install), then run
this walkthrough from the same tagged Mentat clone. The reference tools are
Python 3.12, Go 1.26.3, and Lean 4.31.0. Linux x86-64 and Apple Silicon macOS
are the platforms the release checks target. Hosted CI runs only a Python syntax and
changed-whitespace check; full verification records are in
[implementation evidence](implementation-evidence.md). No Docker daemon or cloud credentials are required. The pinned source contains
a relative `CLAUDE.md` link to `AGENTS.md`; Mentat preserves that link and its
Git mode in the candidate snapshot without loading host files.

```sh
mentat --version
python3 --version
go version
lean --version

PILOT=$(mktemp -d "${TMPDIR:-/tmp}/mentat-iceberg.XXXXXX")
REPO="$PILOT/iceberg-go"
STATE="$PILOT/state"
./examples/iceberg-go/setup.sh "$REPO"

BASE=$(git -C "$REPO" rev-parse mentat-iceberg-base)
BEFORE=$(git -C "$REPO" rev-parse mentat-iceberg-before)
FIXED=$(git -C "$REPO" rev-parse mentat-iceberg-fixed)

mentat --state "$STATE" doctor --repo "$REPO" --base "$BASE" --runner host
mentat claims check --repo "$REPO" --base "$BASE"
mentat --state "$STATE" spec check --repo "$REPO" --base "$BASE" --runner host
```

The optional second setup argument is a local Iceberg-Go Git source containing
both pinned commits. Setup refuses existing repository or cache destinations.
It binds sibling module/build cache paths into the reviewed contract before
committing the base. Review the contract, the Go probe, the Python launcher,
both formal sources, and the pinned candidate source before approval. These
are trusted host checks and use your local permissions. Lean elaboration also
runs on the host. Use v0.1.2 for the hardened compiled-model audit, and approve
the newly displayed verifier-bound digest; older approvals are not reused.

```sh
DIGEST='paste the reviewed contract digest'
mentat --state "$STATE" spec approve "$DIGEST" \
  --repo "$REPO" --base "$BASE" --runner host

python3 examples/iceberg-go/prepare.py "$REPO" "$BASE"
```

Preparation downloads and verifies the pinned module graph, then compiles the
approved probe against both temporary source snapshots to warm Go's cache.
It discards the binaries. Verification still compiles its own staged candidate
and approved probe, disables automatic toolchain/module downloads, and disables
test-result caching. A missing tool or cache entry is incomplete, not a row
violation. Cold preparation is separate from the 600-second behavior timeout.

## Verify and compare

```sh
verify_into() (
    expected=$1
    destination=$2
    candidate=$3
    code=0
    mentat --state "$STATE" verify --repo "$REPO" --base "$BASE" \
        --rev "$candidate" --runner host --json > "$destination" || code=$?
    if [ "$code" -ne "$expected" ]; then
        cat "$destination" >&2
        return 1
    fi
)

run_id() {
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_id"])' "$1"
}

verify_into 1 "$PILOT/before.json" "$BEFORE" &&
verify_into 0 "$PILOT/fixed.json" "$FIXED" &&
FAILED_RUN=$(run_id "$PILOT/before.json") &&
PASSING_RUN=$(run_id "$PILOT/fixed.json") &&
mentat --state "$STATE" evidence compare "$FAILED_RUN" "$PASSING_RUN"
```

Mentat exits `1` for the completed historical violation and `0` for the fix.
Exit `2` means incomplete: investigate it before interpreting or publishing
the result. The native launcher's internal violation exit is `10` so a failed
Go build cannot be mistaken for a confirmed row mismatch.

On a passing primary run, Mentat runs the same launcher again with the approved
negative-control argument. It changes only the primary case's completed
observation to a known-bad one-row result. Exactly that case must fail. An
unqualified control makes the run incomplete.

## Export and replay

```sh
mentat --state "$STATE" evidence export "$FAILED_RUN" --out "$PILOT/before"
mentat --state "$STATE" evidence export "$PASSING_RUN" --out "$PILOT/fixed"
```

Review the `host_reproduce` (or `reproduce`) command in
`"$PILOT/before/manifest.json"` and run it on the same host. It should return a new run ID and exit `1`. Export that run to a new
directory and compare its canonical `witness.json` bytes and manifest hashes
with the original. Raw logs retain the original errors and host details; the
canonical witness excludes random paths, timestamps, and snapshot identifiers.

For a fresh checkout, use the original pilot as a Git source, obtain the new
base, approve its newly displayed digest, and prepare its caches before
verification:

```sh
SOURCE_REPO="$REPO"
REPO="$PILOT/fresh iceberg-go"
STATE="$PILOT/fresh state"
./examples/iceberg-go/setup.sh "$REPO" "$SOURCE_REPO"
BASE=$(git -C "$REPO" rev-parse mentat-iceberg-base)
BEFORE=$(git -C "$REPO" rev-parse mentat-iceberg-before)
mentat --state "$STATE" spec check --repo "$REPO" --base "$BASE" --runner host

DIGEST='paste the newly reviewed digest'
mentat --state "$STATE" spec approve "$DIGEST" \
  --repo "$REPO" --base "$BASE" --runner host
python3 examples/iceberg-go/prepare.py "$REPO" "$BASE"
verify_into 1 "$PILOT/fresh.json" "$BEFORE"
```

The fresh checkout has its own approval and receipts. Compare its canonical
witness to the original; do not expect absolute paths or complete manifest
bytes to match across checkouts.

## Generate the public summary

Using the original paired runs and their original state directory:

```sh
uv run --locked python examples/iceberg-go/summarize.py \
  --state "$PILOT/state" --before "$FAILED_RUN" --fixed "$PASSING_RUN" \
  --out "$PILOT/public"
cd "$PILOT/public"
shasum -a 256 -c SHA256SUMS
```

The helper verifies local receipts, matching approval scope, both formal
results, every native case, raw observations, row tuples, and the fixed-run
negative control. It refuses existing output directories. It writes the
original exports, an attributed and licensed evidence archive, a small JSON
summary, and checksums. The summary includes all nonempty case operation
errors, including cases whose rows were preserved.

Generate public evidence in a dedicated temporary checkout with a minimal
public tool PATH. Inspect **every manifest, raw artifact, log, and archive
member** before uploading: reproduction commands record absolute paths and
invocation details. If anything private appears, regenerate in a clean public
environment; do not edit receipt-validated originals or silently redact hashed
exports. Checksums establish integrity of the published bytes, not an
independent signature or proof of authorship.

## Run the integration test

The ordinary unit tests need no Go source clone. To exercise real historical
revisions, provide a prepared local Git source and the reference tools on PATH:

```sh
MENTAT_ICEBERG_GO_SOURCE=/path/to/iceberg-go.git \
    uv run --locked --extra polaris pytest -q tests/test_iceberg_compaction_replay.py
```

The opt-in integration covers approval, before/fixed verification, the negative
control, comparison, exports, saved-command reproduction, fresh-checkout
witness equality, and resistance to candidate-owned test substitutions.
