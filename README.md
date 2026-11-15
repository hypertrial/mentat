# Mentat

Mentat verifies that a committed Apache Polaris Python setup CLI preserves namespace paths and selected properties when exporting and restoring configuration. The local prototype runs on an Apple Silicon Mac and checks a single internal, file-backed catalog. A human approves the contract and accepts the result. A coding agent can inspect evidence, draft contract changes, and propose repairs through the local CLI.

## Set up the prototype

Install uv, the Docker CLI, Colima, and Lean 4.31.0 with Lake. Put `lean` and `lake` on `PATH`. With elan, install `leanprover/lean4:v4.31.0` and set `ELAN_TOOLCHAIN=leanprover/lean4:v4.31.0` for the Mentat session.

```sh
uv sync --locked
colima start mentat --runtime docker --vm-type vz --mount none --cpu 4 --memory 6 --disk 20
uv run --locked mentat doctor --prepare
uv run --locked mentat spec check
```

The dedicated `colima-mentat` context runs two disposable Polaris 1.8.0 ARM64 containers per case. Image preparation can download dependencies. Candidate execution uses private Docker networks, synthetic credentials, and a committed snapshot of `client/python/apache_polaris/cli`. The server, generated SDK, and runner stay pinned. Run only operator-reviewed local candidates in this prototype.

## Check and repair a Polaris change

Review the contract shown by `spec check` and confirm it from an interactive terminal. The default state directory is `~/.local/state/mentat`. Use `--state` for a separate operator-owned directory outside the candidate clone. Use `--spec` to check an alternate draft from the trusted workspace. The FAS and its fixture must agree on the selected properties. Each namespace can carry up to 32 selected properties. Mentat checks the generated `location` separately.

```sh
CONTRACT_DIGEST='paste the spec check digest here'
uv run --locked mentat spec approve "$CONTRACT_DIGEST"

POLARIS=/absolute/path/to/polaris
BEFORE=7bb319e281225b2b63c78f1cbc87726a1c9a7ece
FIXED=ba152c04f7f985bbaeb487a55ef442df52321afe
uv run --locked mentat verify --repo "$POLARIS" --rev "$BEFORE" --fresh --json
FAILED_RUN='paste the failed run ID here'
uv run --locked mentat evidence export "$FAILED_RUN" --out /absolute/path/to/new-bundle
uv run --locked mentat verify --repo "$POLARIS" --rev "$FIXED" --fresh --json
PASSING_RUN='paste the passing run ID here'
uv run --locked mentat accept "$PASSING_RUN"
```

The replay covers [Polaris PR #5292](https://github.com/apache/polaris/pull/5292). Seed data gives `["finance.us"]` and `["finance", "us"]` distinct values. The oracle reads Polaris independently over REST, and the destination receives the source CLI's exact export bytes. The evidence records per-claim path and property differences. A supported violation that the native regression reproduces also produces a pytest file. Copy it into `client/python/tests/` in the repair workspace and run its header command against the failing and repaired revisions.

For agents, `--json` emits a versioned result. Exit `0` means complete success, `1` means a confirmed violation, and `2` means verification is incomplete or the input is invalid. Z3 and Lean cover the model. The live oracle and native regression cover the reported fixtures and revisions. A contract or verifier change requires fresh approval. SQLite caches only Z3 success, and `--fresh` bypasses that cache.

## Run the founder evaluation

Copy the bundled public fixture shape from `src/mentat/pack/public.json` into an operator-owned private file. Use unseen nested and literal-dot paths with selected properties and explicitly include their parent paths. Give at least one namespace a selected property so the evaluator can test loss of a namespace and corruption of an existing value. Freeze the file digest before repair and keep the file away from the repair workspace.

```sh
HELD_OUT=/absolute/path/to/held-out.json
FROZEN_SHA=$(shasum -a 256 "$HELD_OUT" | cut -d ' ' -f 1)
uv run --locked mentat evaluate --repo "$POLARIS" --held-out "$HELD_OUT" --sha256 "$FROZEN_SHA"
```

The report compares frozen upstream pytest, a Hypothesis-generated test corpus, and live verification on the historical failure, repair, and three controls. Evaluation reports use `schema_version` 2. Their `baseline_rows` record one result per candidate variant for each test baseline. The live `rows` record one result per candidate variant and private fixture, so the totals use different denominators. Private inputs and detailed failures stay out of the evaluation bundle. Candidate code can observe inputs delivered during replay, so keep the final trial operator-run and rotate cases before further repair attempts. A baseline that also fails on the unmutated repair receives an ambiguous alert when it rejects a violation case.

## Develop

```sh
uv sync --locked
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv run --locked pytest
uv build --wheel
```

On interruption, the replay cleans up its disposable resources. After a hard kill, inspect `docker --context colima-mentat ps -a --filter label=org.hypertrial.mentat.run` and `docker --context colima-mentat network ls --filter label=org.hypertrial.mentat.run` and remove only the orphaned run's resources.
