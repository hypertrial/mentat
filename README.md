# Mentat

Mentat verifies a Git commit against a contract you review and approve. Define what should hold, check the model with Lean or Z3, and run behavior checks against the committed implementation. Use the evidence to review a change or hand a reproducible failure to your coding agent.

## Install

Install Hypertrial Mentat v0.1.2 from GitHub with Python 3.12 and uv. The
distribution is `hypertrial-mentat` and provides the `mentat` command.
[Install uv](https://docs.astral.sh/uv/getting-started/installation/)

```sh
uv tool install --python 3.12 \
  'hypertrial-mentat @ git+https://github.com/hypertrial/mentat.git@v0.1.2'
export PATH="$(uv tool dir --bin):$PATH"
mentat --version
```

For the walkthroughs, clone the same tag to get the example scripts.

```sh
git clone --branch v0.1.2 https://github.com/hypertrial/mentat.git
cd mentat
```

If your internal Hypertrial Mentat install used the old `mentat` distribution
name, remove that tool with `uv tool uninstall mentat` first. Verify the new
contract digest and reapprove it when the verifier fingerprint changes.

The v0.1.2 release checks target Linux x86-64 and Apple Silicon macOS. Docker is
the default runner. The clamp quickstart uses the opt-in host runner with Git
and a POSIX shell. Host behavior checks and Lean run with your local
permissions. Use trusted code and see the [runner setup](docs/contracts.md#runners).

## Try it on a Git repo

From the tagged clone, create a separate example repository with a buggy shell
command and a fixed revision. The Z3 check covers the clamp model. The executable
check verifies the candidate command on the declared inputs.

```sh
DEMO="$(mktemp -d)/clamp"
./examples/quickstart/create.sh "$DEMO"
STATE=$(dirname "$DEMO")/state
BASE=$(git -C "$DEMO" rev-parse mentat-demo-base)
FIXED=$(git -C "$DEMO" rev-parse mentat-demo-fixed)

mentat --state "$STATE" doctor --repo "$DEMO" --base "$BASE" --runner host
mentat --state "$STATE" spec check --repo "$DEMO" --base "$BASE" --runner host
```

Review the files in `"$DEMO/.mentat"`. Copy the displayed contract digest and approve it at the terminal.

```sh
DIGEST='paste the contract digest'
mentat --state "$STATE" spec approve "$DIGEST" --repo "$DEMO" --base "$BASE" --runner host
mentat --state "$STATE" verify --repo "$DEMO" --base "$BASE" --rev "$BASE" --runner host --json
```

The Z3 model passes. The behavior check returns exit `1` because the buggy CLI prints `-1` for a negative input. Copy its run ID to export the counterexample and reproduction command.

```sh
FAILED_RUN='paste the failing run ID'
mentat --state "$STATE" evidence export "$FAILED_RUN" --out "$DEMO/../evidence"
mentat --state "$STATE" verify --repo "$DEMO" --base "$BASE" --rev "$FIXED" --runner host --json

PASSING_RUN='paste the passing run ID'
mentat --state "$STATE" accept "$PASSING_RUN" --repo "$DEMO" --base "$BASE" --runner host
```

Development source checkouts also provide an [offline HTML report](docs/contracts.md#render-an-offline-report) for saved runs. This command is not included in the v0.1.2 tag used above.

Development checkouts support [approved Lean proof campaigns](docs/proofs.md) on a
dedicated Linux checker, plus explicit claim assurance and source-mutation
qualification. Proofs establish model properties; implementation checks remain
separate evidence.

## Try approved pytest checks on a src-layout project

This example requires pytest in the `python3` environment on `PATH`. It creates a fresh Git repository with a working base and a bad candidate that also weakens its own test. The contract runs the base's approved tests and fixture against the candidate's `src` directory.

```sh
DEMO="$(mktemp -d)/python-src"
./examples/python-src/create.sh "$DEMO"
BASE=$(git -C "$DEMO" rev-parse mentat-demo-base)
BAD=$(git -C "$DEMO" rev-parse mentat-demo-bad)
mentat doctor --repo "$DEMO" --base "$BASE" --runner host
mentat spec check --repo "$DEMO" --base "$BASE" --runner host
```

Review the contract and selected base files, then approve the displayed digest. The bad revision returns exit `1`; the base returns exit `0`.

```sh
DIGEST='paste the contract digest'
mentat spec approve "$DIGEST" --repo "$DEMO" --base "$BASE" --runner host
mentat verify --repo "$DEMO" --base "$BASE" --rev "$BAD" --runner host --json
mentat verify --repo "$DEMO" --base "$BASE" --rev "$BASE" --runner host --json
```

See [approved base files and selectors](docs/contracts.md#approved-base-files-and-selectors) for the launcher, host prerequisites, and evidence reproduction details.

## Replay a real bug

Follow the [Iceberg-Go compaction walkthrough](docs/iceberg-go.md) to check whether
a historical compaction preserves all twelve rows after each committed batch.
Run the same approved native probe against the original revision and upstream
fix, inspect the separate Lean and Z3 model results, and export the recorded
failure and passing comparison.

The [Nano ID walkthrough](docs/nanoid.md) remains available for a smaller CLI
validation example with approved native tests and a negative control.

## Use Mentat on your project

Every claim needs a behavior check, and at least one claim also needs a formal
check. A passing model establishes a result under its stated assumptions.
Behavior evidence covers the approved commands and recorded environment.

- [Project contracts and CLI](docs/contracts.md) covers writing claims, selecting runners, and interpreting results.
- [Mentat Claims Spec](.mentat/claims.md) links Mentat's own numbered requirements to executable checks.
- [Nemo example](docs/nemo.md) catches a silent arithmetic error and exports a standalone replay.
- [Apache Polaris showcase](docs/polaris.md) replays a real namespace round-trip bug and exports a native regression test.

See the [release notes](CHANGELOG.md), [contribution guide](CONTRIBUTING.md), and
[security policy](SECURITY.md). Mentat is available under the [MIT license](LICENSE).
