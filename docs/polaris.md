# Apache Polaris showcase

Apache Polaris PR #5292 fixed a configuration round-trip bug. The Python setup CLI flattened the literal namespace `["finance.us"]` and the nested namespace `["finance", "us"]` to the same export key. Mentat replays both commits and uses an independent REST oracle to check the restored namespace paths and properties. [Polaris PR #5292](https://github.com/apache/polaris/pull/5292)

## Prepare the showcase

Use an Apple Silicon Mac with uv, the Docker CLI, Colima, and Lean 4.31.0 with Lake. Run these commands from the Mentat clone. Put `lean` and `lake` on `PATH`. Mentat checks the compiler selected by Lake before building the proof. With elan, install `leanprover/lean4:v4.31.0` and set `ELAN_TOOLCHAIN=leanprover/lean4:v4.31.0` for the Mentat session.

```sh
uv sync --locked --extra polaris
colima start mentat --runtime docker --vm-type vz --mount none --cpu 4 --memory 6 --disk 20
uv run --locked --extra polaris mentat showcase polaris doctor --prepare
uv run --locked --extra polaris mentat showcase polaris spec check
```

The dedicated `colima-mentat` context runs two disposable Polaris 1.8.0 ARM64 containers per case. Image preparation can download dependencies. Candidate execution uses private Docker networks, synthetic credentials, and a committed snapshot of `client/python/apache_polaris/cli`. Snapshot limits are 4,096 files, 512 characters per relative path, 2 MiB per file, and 16 MiB total. The server, generated SDK, and runner stay pinned. Run only operator-reviewed local candidates in this prototype.

## Check and repair a Polaris change

Review the contract shown by `spec check` and confirm it from an interactive terminal. The default state directory is `~/.local/state/mentat`. Use `--state` for a separate operator-owned directory outside the candidate clone. Use `--spec` to check an alternate draft from the trusted workspace. The FAS and its fixture must agree on the selected properties. Each namespace can carry up to 32 selected properties. Mentat checks the generated `location` separately.

Mentat resolves `--repo` and reads committed inputs from that checkout, ignoring inherited Git overrides such as `GIT_DIR` and `GIT_WORK_TREE`.

```sh
CONTRACT_DIGEST='paste the spec check digest here'
uv run --locked --extra polaris mentat showcase polaris spec approve "$CONTRACT_DIGEST"

POLARIS=/absolute/path/to/polaris
BEFORE=7bb319e281225b2b63c78f1cbc87726a1c9a7ece
FIXED=ba152c04f7f985bbaeb487a55ef442df52321afe
uv run --locked --extra polaris mentat showcase polaris verify --repo "$POLARIS" --rev "$BEFORE" --fresh --json
FAILED_RUN='paste the failed run ID here'
uv run --locked --extra polaris mentat evidence export "$FAILED_RUN" --out /absolute/path/to/new-bundle
uv run --locked --extra polaris mentat showcase polaris verify --repo "$POLARIS" --rev "$FIXED" --fresh --json
PASSING_RUN='paste the passing run ID here'
uv run --locked --extra polaris mentat showcase polaris accept "$PASSING_RUN"
```

The fixture gives `["finance.us"]` and `["finance", "us"]` distinct values. The oracle reads Polaris over REST, and the destination receives the source CLI's exact export bytes. The evidence records per-claim differences and command failures. Mentat attempts native reproduction for supported claim violations, including failures during export, apply, and re-export. Every supported violation produces a pytest file, whether or not the native run reproduces it. Verdicts come from the live oracle's comparison, not from native pytest. Native pytest is a convenience reproduction only: candidate code runs inside the pytest container that prints the result marker, so it may influence that result. The result is labeled advisory and never changes the verification or evaluation status. During replay the candidate CLI runs against the replay servers with the replay root credential, so candidate code can write state that the oracle later reads. The verdict therefore still depends on operator review of the candidate. Copy the file into `client/python/tests/` in the repair workspace and run its header command against the failing and repaired revisions.

An out-of-memory kill, a signal exit, or a stopped server leaves the affected replay stage incomplete. Interrupted native test execution, missing or inconsistent native results, and unconfirmed native container removal are reported in the advisory native result only. Replay cleanup failures still make the affected replay incomplete.

Native regression failures include the failed stage and a bounded explanation
in JSON, terminal output, and exported reports. These distinguish preflight,
staging, container creation, copying, execution, completion, and cleanup
problems without copying raw Docker errors or local paths into the diagnostic.
A cleanup failure retains the original failure and marks the native result
incomplete. Older evidence without these details still displays its recorded
status. Held-out evaluation continues to exclude private detailed failures.

For agents, `--json` emits a versioned result. Exit `0` means complete success, `1` means a confirmed violation, and `2` means verification is incomplete or the input is invalid. Polaris verification gives a confirmed violation precedence when another check is incomplete. The manifest retains each result. Its `advisory_results` field names the results that are candidate-influenced and advisory; for verification this is `regressions`. Mentat reruns Z3 and Lean for every verification. Existing commands may still pass `--fresh`. The live oracle covers the reported fixtures and revisions. A contract or verifier change requires fresh approval.

## Run a held-out evaluation

Copy the bundled public fixture shape from `src/mentat/showcases/polaris/pack/public.json` into an operator-owned private file. Use unseen nested and literal-dot paths with selected properties and explicitly include their parent paths. Give at least one namespace a selected property so the evaluator can test loss of a namespace and corruption of an existing value. Freeze the file digest before repair and keep the file away from the repair workspace.

```sh
HELD_OUT=/absolute/path/to/held-out.json
FROZEN_SHA=$(shasum -a 256 "$HELD_OUT" | cut -d ' ' -f 1)
REPAIR='paste the full commit ID of the repair'
uv run --locked --extra polaris mentat showcase polaris evaluate \
  --repo "$POLARIS" --rev "$REPAIR" --held-out "$HELD_OUT" --sha256 "$FROZEN_SHA"
```

The report compares frozen upstream pytest, a Hypothesis-generated test corpus, and live verification on the historical failure, repair, and three controls. Evaluation reports use `schema_version` 2. Their `baseline_rows` record one result per candidate variant for each test baseline. The live `rows` record one result per candidate variant and private fixture, so the totals use different denominators. Private inputs and detailed failures stay out of the evaluation bundle. Candidate code can observe inputs delivered during replay, so keep the final trial operator-run and rotate cases before further repair attempts. A baseline that also fails on the unmutated repair receives an ambiguous alert when it rejects a violation case.

Native pytest runs candidate code inside its container, so its result is advisory and may be influenced by the candidate. `advisory_results` names it as `native_regression`. The two test baselines, `upstream-pytest` and `hypothesis`, run on the same candidate snapshots, so their counts may be influenced by the candidate too. Baseline counts are comparison data. They never decide pass or violation, but an incomplete baseline still makes the evaluation incomplete. Native results are not in the scored counts. Pass and violation come from the live oracle's comparison (`counts.mentat`).

An incomplete oracle attempt takes precedence in the evaluation exit status. If Docker preflight fails, the report records the common failure on the planned rows, with zero execution time.

## Run the runtime smoke check

Use the prepared Colima profile and images from the setup above. Put the installed Lean 4.31.0 toolchain's own `bin` directory on `PATH`. The smoke check requires a local Polaris checkout containing both pinned commits. It runs the formal checks and exercises both revisions through live replay and native pytest, then checks for leftover containers and networks labeled with its run IDs.

```sh
uv run --locked --extra polaris python scripts/smoke_runtime.py \
  --repo "$POLARIS" --docker-context colima-mentat
```

The smoke command exits nonzero if prerequisites, expected results, or resource cleanup checks fail. Its native pytest expectation is sound because it runs the pinned upstream revisions, not a candidate. It uses temporary runtime resources and leaves operator approvals and evidence alone.

## Cleanup and upgrade

The public release exposes the Polaris workflow under `mentat showcase polaris`. Run `spec check` and approve the new fingerprint before replaying. Export existing run IDs with the shared `mentat evidence export` command.

On interruption, the replay cleans up its disposable resources. After a hard kill, inspect `docker --context colima-mentat ps -a --filter label=org.hypertrial.mentat.run` and `docker --context colima-mentat network ls --filter label=org.hypertrial.mentat.run` and remove only the orphaned run's resources.

For an agent relaying your explicit decision, `spec approve` and `accept` also accept `--trusted-chat-approval`. This bypasses the terminal prompt; Mentat cannot verify the chat or the decision, and any process running as your account can impersonate it. Use it only when you trust that relay. Records identify this source as `unverified_agent_relay`; contract and evidence checks still apply.
