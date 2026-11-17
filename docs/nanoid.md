# Nano ID replay pilot

This example compares a [historical Nano ID CLI fix](https://github.com/ai/nanoid/pull/618) with its parent under one approved contract. The older CLI accepted `0.5`, `1.5`, and `Infinity` as sizes. The fixed CLI rejects them.

## Set up and approve

From a Mentat checkout, use Git, Python 3 and Node.js on `PATH`. The reference run uses Node.js 26.10.0. The example and its approved upstream test run without an npm install.

```sh
uv tool install --python 3.12 .
node --version

PILOT=$(mktemp -d "${TMPDIR:-/tmp}/mentat-nanoid.XXXXXX")
REPO="$PILOT/nanoid"
STATE="$PILOT/state"
./examples/nanoid/setup.sh "$REPO"

BASE=$(git -C "$REPO" rev-parse mentat-nanoid-base)
BEFORE=$(git -C "$REPO" rev-parse mentat-nanoid-before)
FIXED=$(git -C "$REPO" rev-parse mentat-nanoid-fixed)

mentat --state "$STATE" doctor --repo "$REPO" --base "$BASE" --runner host
mentat claims check --repo "$REPO" --base "$BASE"
mentat --state "$STATE" spec check --repo "$REPO" --base "$BASE" --runner host
```

The setup script accepts a local Nano ID mirror containing the pinned commits as an optional second argument. Review the displayed contract, `"$REPO/.mentat/claims.md"`, the approved test file and the candidate source. Host behavior checks use your local permissions.

```sh
DIGEST='paste the contract digest'
mentat --state "$STATE" spec approve "$DIGEST" --repo "$REPO" --base "$BASE" --runner host
```

## Verify and compare

```sh
verify_into() (
    expected=$1
    destination=$2
    candidate=$3
    result_code=0
    mentat --state "$STATE" verify --repo "$REPO" --base "$BASE" \
        --rev "$candidate" --runner host --json > "$destination" || result_code=$?
    if [ "$result_code" -ne "$expected" ]; then
        cat "$destination" >&2
        return 1
    fi
)

run_id() {
    python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["run_id"])' "$1"
}

verify_into 1 "$PILOT/before.json" "$BEFORE" &&
verify_into 0 "$PILOT/fixed.json" "$FIXED" &&
FAILED_RUN=$(run_id "$PILOT/before.json") &&
PASSING_RUN=$(run_id "$PILOT/fixed.json") &&
mentat --state "$STATE" evidence compare "$FAILED_RUN" "$PASSING_RUN"
```

The Z3 model should pass on both revisions. Both CLI checks should move from violation to pass. Exit `2` from verification means the run is incomplete and should be investigated before continuing. Use `--json` on the comparison for machine-readable results.

The native test check uses the fixed revision’s exact `test/bin.test.js` through [approved base files](contracts.md#approved-base-files-and-selectors). Its launcher stages the approved test where it can invoke the candidate’s CLI. The Node version, totals, and failed test names are recorded in the check log. The approved case report records all 11 exact case IDs and their outcomes. Read the exported report under claim `cli.size` to see each case, the custom observation, and the Z3 model result.

| Candidate | Own upstream CLI tests | Approved fixed CLI tests |
| --- | ---: | ---: |
| Parent `3167e10` | 8 passed | 8 passed, 3 failed |
| Fix `58193a1` | 11 passed | 11 passed |

The custom check also observes the exact CLI exit status, stderr, and output shape. Its witness records the input, exit status, stderr, and stdout length without storing the random ID characters. A request for seven characters serves as a positive control. When the primary check passes, the negative control feeds a known-bad fractional-size observation through the same matcher. The separate control evidence records whether the matcher caught it.

```sh
mentat --state "$STATE" evidence export "$FAILED_RUN" --out "$PILOT/before"
mentat --state "$STATE" evidence export "$PASSING_RUN" --out "$PILOT/fixed"
```

## Replay the failure

To replay on the same host, review and run the command in `"$PILOT/before/report.md"` and redirect its JSON output to `"$PILOT/replay.json"`. It should exit `1` with a new run ID.

```sh
REPLAY_RUN=$(run_id "$PILOT/replay.json")
mentat --state "$STATE" evidence export "$REPLAY_RUN" --out "$PILOT/replay"
```

For a fresh checkout, derive the base and candidate from that checkout’s refs. The following uses the first Nano ID checkout as a local Git source. Any of its pilot branches can be checked out. On a second host, run the setup script from an equivalent Mentat checkout with the public default source or an available local mirror.

```sh
SOURCE_REPO="$REPO"
REPO="$PILOT/fresh nanoid"
STATE="$PILOT/fresh state"
./examples/nanoid/setup.sh "$REPO" "$SOURCE_REPO"

BASE=$(git -C "$REPO" rev-parse mentat-nanoid-base)
BEFORE=$(git -C "$REPO" rev-parse mentat-nanoid-before)
mentat --state "$STATE" doctor --repo "$REPO" --base "$BASE" --runner host
mentat claims check --repo "$REPO" --base "$BASE"
mentat --state "$STATE" spec check --repo "$REPO" --base "$BASE" --runner host

DIGEST='paste the new contract digest after reviewing the inputs'
mentat --state "$STATE" spec approve "$DIGEST" --repo "$REPO" --base "$BASE" --runner host
verify_into 1 "$PILOT/fresh.json" "$BEFORE" &&
FRESH_RUN=$(run_id "$PILOT/fresh.json") &&
mentat --state "$STATE" evidence export "$FRESH_RUN" --out "$PILOT/fresh"
```

The fresh checkout has its own approval, even when it is on the same machine. Use the original failure export and the replay exports to compare witnesses. If replaying elsewhere, transfer the original export you have reviewed for sharing.

```sh
compare_witnesses() {
    python3 - "$@" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

reference = None
for argument in sys.argv[1:]:
    bundle = Path(argument)
    manifest = json.loads((bundle / "manifest.json").read_text())
    check = next(
        check for case in manifest["cases"] for check in case["checks"]
        if check["id"] == "cli-size-replay"
    )
    name = check["artifacts"]["witness.json"]
    data = (bundle / name).read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != manifest["artifacts"][name] or (reference is not None and data != reference):
        sys.exit(f"Witness mismatch in {bundle}")
    reference = data
    print(sha256, bundle)
PY
}

compare_witnesses "$PILOT/before" "$PILOT/replay" "$PILOT/fresh"
```

On a second host with the original export copied into `"$PILOT/before"`, run `compare_witnesses "$PILOT/before" "$PILOT/fresh"`.

The historical reference witness SHA-256 was `a7eb9410234add47aaa2233719e1a38ea6e5e027aeca962231c04dd38bbdf059`. The walkthrough has been verified across fresh checkouts on one machine.

## Run the opt-in integration test

Use a prepared local Nano ID mirror and put the reference Node version on `PATH`. The test runs the setup script twice and uses pytest’s approval seam. The regular test suite requires no Nano ID clone.

```sh
MENTAT_NANOID_SOURCE=/path/to/local/nanoid.git \
    uv run --locked --extra polaris pytest -q tests/test_nanoid_replay.py
```
