# Nano ID replay pilot

This example verifies a [historical Nano ID CLI fix](https://github.com/ai/nanoid/pull/618) with the same approved check on both revisions. The older CLI accepted `0.5`, `1.5`, and `Infinity` as sizes. The fixed CLI rejects them. A valid size of seven is the positive control.

## Set up a clean checkout

From the Mentat clone, use Git, the installed Mentat CLI, and Node.js on `PATH`. The reference run uses Node.js 26.10.0. Nano ID’s CLI and its upstream CLI test file use Node’s standard library.

```sh
uv tool install --python 3.12 .
node --version

PILOT="$PWD/.cache/nanoid-pilot"
REPO="$PILOT/nanoid"
STATE="$PILOT/state"
mkdir -p "$PILOT"
./examples/nanoid/setup.sh "$REPO"

BASE=$(git -C "$REPO" rev-parse mentat-nanoid-base)
BEFORE=$(git -C "$REPO" rev-parse mentat-nanoid-before)
FIXED=$(git -C "$REPO" rev-parse mentat-nanoid-fixed)
```

Choose a new destination for each setup. As a second argument, the script can use an existing local Nano ID clone that contains the two upstream commits. The contract base adds the grouped Claims Spec, Z3 model, and behavior check to the fixed revision. The candidates are the original upstream commits.

## Approve, verify, and replay

Read the generated `.mentat/claims.md` and check the review output. The Z3 model checks the size rule after numeric parsing. The Node subprocess check observes the CLI’s actual exit status and output. The host runner uses your local permissions, so review the check and candidate source before approving.

```sh
mentat --state "$STATE" doctor --repo "$REPO" --base "$BASE" --runner host
mentat claims check --repo "$REPO" --base "$BASE"
mentat --state "$STATE" spec check --repo "$REPO" --base "$BASE" --runner host

DIGEST='paste the contract digest'
mentat --state "$STATE" spec approve "$DIGEST" --repo "$REPO" --base "$BASE" --runner host
mentat --state "$STATE" verify --repo "$REPO" --base "$BASE" --rev "$BEFORE" --runner host --json
mentat --state "$STATE" verify --repo "$REPO" --base "$BASE" --rev "$FIXED" --runner host --json
```

The formal check should pass on both runs. The parent should report a behavior violation with exit `1`. The fix should pass with exit `0`. Treat exit `2` as an unresolved run. Copy the run IDs from the JSON output.

```sh
FAILED_RUN='paste the failing run ID'
PASSING_RUN='paste the passing run ID'
mentat --state "$STATE" evidence export "$FAILED_RUN" --out "$PILOT/before"
mentat --state "$STATE" evidence export "$PASSING_RUN" --out "$PILOT/fixed"
```

Run the same-host replay command in `before/report.md`. It should exit `1` and return a new run ID. Export that run and compare the witnesses.

```sh
REPLAY_RUN='paste the replay run ID'
mentat --state "$STATE" evidence export "$REPLAY_RUN" --out "$PILOT/replay"
cmp "$PILOT/before/check-001.artifact-001.witness.json" \
    "$PILOT/replay/check-001.artifact-001.witness.json"
```

The witness records the input, exit status, stderr, and stdout length. It excludes the random ID characters. For a fresh checkout, repeat setup with a different destination and state directory and obtain approval for the digest displayed there.

## Compare with upstream tests

Run the upstream CLI tests in scratch snapshots. Passing `frozen` applies the fixed commit’s exact test file to the selected candidate.

```sh
run_cli_tests() (
  work=$(mktemp -d)
  trap 'rm -rf "$work"' 0
  git -C "$REPO" archive "$1" > "$work/source.tar" || exit 2
  tar -xf "$work/source.tar" -C "$work" || exit 2
  rm "$work/source.tar" || exit 2
  if [ "$2" = frozen ]; then
    git -C "$REPO" show "${FIXED}:test/bin.test.js" > "$work/test/bin.test.js" || exit 2
  fi
  cd "$work" || exit 2
  node --test --test-reporter=tap test/bin.test.js
)

run_cli_tests "$BEFORE" own
run_cli_tests "$FIXED" own
run_cli_tests "$BEFORE" frozen
run_cli_tests "$FIXED" frozen
```

| Candidate | CLI tests | Passed on Node 26.10.0 | Failed on Node 26.10.0 |
| --- | --- | ---: | ---: |
| Parent `3167e10` | Its own tests | 8 | 0 |
| Fix `58193a1` | Its own tests | 11 | 0 |
| Parent `3167e10` | Fixed tests | 8 | 3 |
| Fix `58193a1` | Fixed tests | 11 | 0 |

The fixed upstream tests already detect all three faulty results. Mentat exercises the reviewed rule with the same contract on both candidates and records the inputs, results, and replay instructions together.

## Verified pilot result

On Node.js 26.10.0, the Z3 check passed on both candidates. The parent returned a confirmed behavior violation for the three invalid sizes, and the fix passed all four cases. The exported failure replay reproduced the witness byte for byte, including from a separate fresh Nano ID checkout with its own approved contract. The failure witness SHA-256 was `a7eb9410234add47aaa2233719e1a38ea6e5e027aeca962231c04dd38bbdf059`.

The independent walkthrough exposed two documentation issues. The guide now uses Git revision syntax that works in zsh and names the witness file exactly as Mentat exports it.
