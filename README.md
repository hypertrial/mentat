# Mentat

Mentat verifies a Git commit against a contract you review and approve. Define what should hold, check the model with Lean or Z3, and run behavior checks against the committed implementation. Use the evidence to review a change or hand a reproducible failure to your coding agent.

## Install

From a Mentat clone, install the CLI with Python 3.12 and uv. [uv installation](https://docs.astral.sh/uv/getting-started/installation/)

```sh
uv tool install --python 3.12 .
```

Docker is the default runner. Use a local Docker context on Linux or an unshared Colima profile on Apple Silicon macOS. The example below uses the opt-in host runner so you can try it with Git and a POSIX shell. See the [runner setup](docs/contracts.md#runners).

## Try it on a Git repo

From the Mentat clone, create a separate example repository with a buggy shell command and a fixed revision.

```sh
DEMO="$(mktemp -d)/clamp"
./examples/quickstart/create.sh "$DEMO"
BASE=$(git -C "$DEMO" rev-parse mentat-demo-base)
FIXED=$(git -C "$DEMO" rev-parse mentat-demo-fixed)

mentat doctor --repo "$DEMO" --base "$BASE" --runner host
mentat spec check --repo "$DEMO" --base "$BASE" --runner host
```

Review the files in `"$DEMO/.mentat"`. Copy the displayed contract digest and approve it at the terminal.

```sh
DIGEST='paste the contract digest'
mentat spec approve "$DIGEST" --repo "$DEMO" --base "$BASE" --runner host
mentat verify --repo "$DEMO" --base "$BASE" --rev "$BASE" --runner host --json
```

The Z3 model passes. The behavior check returns exit `1` because the buggy CLI prints `-1` for a negative input. Copy its run ID to export the counterexample and reproduction command.

```sh
FAILED_RUN='paste the failing run ID'
mentat evidence export "$FAILED_RUN" --out "$DEMO/../evidence"
mentat verify --repo "$DEMO" --base "$BASE" --rev "$FIXED" --runner host --json

PASSING_RUN='paste the passing run ID'
mentat accept "$PASSING_RUN" --repo "$DEMO" --base "$BASE" --runner host
```

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

## Use Mentat on your project

- [Project contracts and CLI](docs/contracts.md) covers writing claims and checks, running candidates, and interpreting results.
- [Mentat Claims Spec](.mentat/claims.md) explains what Mentat covers and links its numbered requirements to executable checks.
- [Apache Polaris showcase](docs/polaris.md) replays a real namespace round-trip bug and exports a native regression test. Mentat 0.1 Polaris commands now live under `mentat showcase polaris`.
- [Nemo example](docs/nemo.md) catches a silent arithmetic error, verifies the historical fix, and exports a standalone replay.
- [Nano ID pilot](docs/nanoid.md) compares a historical CLI input validation bug with its fix and exports a replay.

Run the full suite from the Mentat clone with `uv run --locked --extra polaris pytest`. Put Lean 4.31.0 on `PATH` to include the Lean checks. Run the [runtime smoke check](docs/polaris.md#run-the-runtime-smoke-check) on a prepared Colima host to exercise live Polaris replay and native pytest.
