# Verify a mixed numeric sum in Nemo

Nemo’s CLI can silently drop the float subtotal when `#sum` combines integers and floats. For integer `1` and float `2.5`, the buggy CLI exits successfully and emits integer `1`. The correct result is double `3.5`. This example exercises the same arithmetic through two original Nemo commits. [Nemo fix #801](https://github.com/knowsys/nemo/pull/801)

## Prepare the checkout and offline build image

Use Apple Silicon macOS with Git, Python 3, Colima, Docker, and an installed Mentat CLI. Run setup from the Mentat checkout. The target directory must be new. The optional second argument is a local Nemo clone containing the pinned commits, including a checkout created by an earlier run of setup.

```sh
colima start mentat-nemo --runtime docker --vm-type vz --mount none --cpu 4 --memory 6 --disk 30 --activate=false
export MENTAT_DOCKER_CONTEXT=colima-mentat-nemo
NEMO_DEMO="$(mktemp -d)/nemo-example"
./examples/nemo/setup.sh "$NEMO_DEMO"

NEMO_REPO="$NEMO_DEMO/nemo"
NEMO_STATE="$NEMO_DEMO/state"
NEMO_BASE=$(git -C "$NEMO_REPO" rev-parse mentat-nemo-public)
NEMO_BEFORE=c8396e9ad20ced0252816772223a2b23c227368d
NEMO_FIXED=93f219c31f227f04fbde3a1ec272343a69c4c967

nemo_mentat() {
    mentat --state "$NEMO_STATE" --docker-context "$MENTAT_DOCKER_CONTEXT" "$@"
}
```

Setup checks that the fixed revision has the expected first parent and that both use the same `Cargo.lock`. The Docker recipe pins the Debian base digest and the default Rust nightly. Setup vendors the locked dependencies, registers the image through a temporary loopback registry on an available port, and records the local image reference in the public contract. Save the setup output with the run. A new setup yields its own contract digest.

Each run compiles the staged candidate once using Mentat’s offline, non-root Docker runner and `cargo build --frozen --jobs 1 -p nemo-cli --bin nmo` with debug info and incremental compilation disabled. The build uses a fresh target directory and has a 500-second deadline within the 600-second behavior limit. Build failures, resource limits, and unexpected CLI exits produce incomplete results.

## Review, verify, and export

```sh
nemo_mentat doctor --repo "$NEMO_REPO" --base "$NEMO_BASE" --json
nemo_mentat spec check --repo "$NEMO_REPO" --base "$NEMO_BASE" --json
```

Review the committed `.mentat/` files. The SAT model demonstrates that dropping a float subtotal can change a sum. The second model rules out that difference for the corrected subtotal combination with up to three small, exactly representable, distinct typed inputs in one nonempty group. The compiled CLI checks exercise the mixed input and integer-only, float-only, and three-type controls.

```sh
NEMO_DIGEST='paste the contract_digest from spec check'
nemo_mentat spec approve "$NEMO_DIGEST" --repo "$NEMO_REPO" --base "$NEMO_BASE"
nemo_mentat verify --repo "$NEMO_REPO" --base "$NEMO_BASE" --rev "$NEMO_BEFORE" --json
nemo_mentat verify --repo "$NEMO_REPO" --base "$NEMO_BASE" --rev "$NEMO_FIXED" --json

NEMO_FAILED_RUN='paste the buggy run_id'
NEMO_PASSING_RUN='paste the fixed run_id'
nemo_mentat evidence export "$NEMO_FAILED_RUN" --out "$NEMO_DEMO/failed-evidence"
nemo_mentat evidence export "$NEMO_PASSING_RUN" --out "$NEMO_DEMO/passing-evidence"
```

Expect Mentat exit `1` and the typed `1` versus `3.5` witness on the buggy revision. All three controls should pass. Expect exit `0` on the fix. Exit `2` identifies an incomplete run. Both model results should pass on both revisions.

## Replay and accept

`evidence export` checks the saved manifest and artifact hashes. Use the exported replay and cases from `failed-evidence` with the prepared image to build and replay either exact revision without an existing `nmo` binary.

```sh
NEMO_IMAGE=$(git -C "$NEMO_REPO" show "$NEMO_BASE:.mentat/contract.json" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["docker_image"])')

nemo_replay() (
    stage=$(mktemp -d) || exit 2
    trap 'rm -rf -- "$stage"' EXIT
    git -C "$NEMO_REPO" archive --prefix=nemo/ --output="$stage/source.tar" "$1" || exit 2
    tar -xf "$stage/source.tar" -C "$stage" || exit 2
    python3 - "$NEMO_DEMO/failed-evidence" "$stage" <<'PY' || exit 2
import json
from pathlib import Path
import sys

bundle, stage = map(Path, sys.argv[1:])
manifest = json.loads((bundle / "manifest.json").read_text())
artifacts = manifest["cases"][0]["checks"][0]["artifacts"]
for name in ("replay.py", "cases.json"):
    (stage / name).write_bytes((bundle / artifacts[name]).read_bytes())
PY
    COPYFILE_DISABLE=1 tar --no-xattrs -cf - -C "$stage" nemo replay.py cases.json |
        docker --context "$MENTAT_DOCKER_CONTEXT" run --rm -i --pull never \
            --network none --user 65534:65534 --cpus 1 --memory 1g --pids-limit 128 \
            -w /tmp "$NEMO_IMAGE" /bin/sh -ec \
            'tar -xf - && cd nemo && exec /usr/bin/python3 -I ../replay.py --cases ../cases.json'
)

NEMO_BUG_STATUS=0
nemo_replay "$NEMO_BEFORE" || NEMO_BUG_STATUS=$?
test "$NEMO_BUG_STATUS" -eq 1 && nemo_replay "$NEMO_FIXED"
```

If you already have a compatible `nmo` binary, copy the two files using their original names and run them directly with Python 3.

```sh
python3 replay.py --nmo /path/to/nmo --cases cases.json
```

The replay checks exact numeric value and type independent of output order. The buggy replay exits `1` even though `nmo` itself exits `0`.

```sh
nemo_mentat accept "$NEMO_FAILED_RUN" --repo "$NEMO_REPO" --base "$NEMO_BASE"
nemo_mentat accept "$NEMO_PASSING_RUN" --repo "$NEMO_REPO" --base "$NEMO_BASE" </dev/null
nemo_mentat accept "$NEMO_PASSING_RUN" --repo "$NEMO_REPO" --base "$NEMO_BASE"
```

The first two commands should exit `2`. Review the passing evidence and make the decision on the third command in a terminal. Record the passing run ID and acceptance receipt.

## Recorded Apple Silicon run

The historical pair cold-built under the default non-root runner limits in 107 seconds per candidate. The runner was Docker 29.5.2 on Linux ARM64, with Rust `nightly-2026-08-12` (`rustc 1.99.0-nightly`, `3d6c19bb9`) and Z3 4.16.0 on the Mentat host. The two commits had the same `Cargo.lock` SHA-256 `5f2cc6ad7e20fb9d3a857fc6954329d6a909a08732f7728e4eb5179d3159a2e2`. The local runner image was `localhost:5500/mentat-nemo@sha256:6a1d7468bc2b931615257a675509319aaa300be0f55d34e70aef669b0ca4e879`.

| Candidate | Mentat exit | Public cases passed |
| --- | ---: | ---: |
| Historical bug | `1` | 3/4 |
| Historical fix | `0` | 4/4 |

Both public bundles passed artifact hash validation. The exported standalone
replay reproduced the witnesses against independently built binaries at the
pinned revisions. Mentat rejected acceptance of the failing run. The operator
accepted the fixed run at the terminal.

## Repair pilot outcome

In a historical six-attempt pilot on this one defect, all three baseline
attempts and all three evidence-assisted attempts passed the held-out
scoring cases. The two arms tied on repair success.
