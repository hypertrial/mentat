# Changelog

## Unreleased

- Count proof round and time budgets from earlier campaigns in the same state folder
  with the same approved contract digest and candidate snapshot digest. This stops a
  reset only by preparing again for the same candidate; a new candidate commit, base,
  runner, repository path or verifier version starts a new budget. Verification
  rechecks are charged only to their own campaign. An unreadable or malformed
  campaign entry in the state, or an invalid campaign with the same contract and
  candidate, blocks checks until it is removed. Malformed proof campaign
  manifests and ledger entries fail as user errors. The verifier modules changed, so
  approvals and proof campaigns must be renewed.
- Make Polaris native pytest results advisory. Native pytest runs candidate code
  inside its container, which prints the result marker, so its result may be
  influenced by the candidate. It no longer decides a Polaris verification or
  evaluation status, and no longer decides whether the regression file is kept.
  Every supported oracle violation produces a pytest file, labeled advisory.
  Verdicts come from the live oracle's comparison. Evaluation status still turns
  incomplete when a baseline is incomplete; baseline counts are comparison data
  labeled as candidate-influenced and never decide pass or violation. Records
  written by this version name advisory results in `advisory_results`. Older
  records render unchanged. Replay servers accept writes from candidate code
  through the replay credential, so the oracle's result still depends on operator
  review of the candidate. Mentat's own contract changed. `command.py` and
  `evidence.py` are verifier modules, and the Polaris pack digest changes, so all
  approvals, proof campaigns, and pending acceptances must be renewed.
  `evidence compare` across this change needs `--across-contracts`.
- Separate the product verdict from source-mutation qualification. The run
  `status` and the `verify` exit code (`0` pass, `1` violation, `2` incomplete)
  now come only from formal and behavior checks, so a surviving mutant exits `0`
  and a real violation is no longer reported as incomplete because of a
  qualification. An incomplete qualification, which previously exited `2`, also
  exits `0` when the product checks pass. Records of contracts with `source_mutations` carry
  `source_qualification_status` (`pass`, `violation`, `incomplete`, or `skipped`
  when no qualification ran). `accept` requires a passing product verdict and a
  passing qualification, and reports and terminal output state both verdicts.
  Scripts or CI that relied on exit `1` for a qualification failure must check
  `source_qualification_status` or use `accept`. Verifier modules and Mentat's
  own contract changed, so all approvals and pending acceptances must be renewed.
- Pin the proof checker's Lean toolchain. The checker refuses a toolchain whose
  files or links differ from the official 4.35.0 x86_64 or aarch64 archive, and the
  expected digests are part of the verifier source, so approvals and proof
  campaigns must be renewed.
- Narrow the verifier fingerprint to an explicit list of verifier modules plus
  the installed Z3 version. Edits to the HTML report, Notion publishing, and
  OpenSpec import no longer invalidate approvals; code under `showcases/` remains
  outside the verifier digest. This is a one-time invalidation of approvals, proof
  campaigns, and pending acceptances, and `evidence compare` across the upgrade
  needs `--across-contracts`. The Polaris pack digest also changes, because it
  covers the edited files, so Polaris approvals must be renewed. Saved proofs
  from Lean 4.35.0 keep loading; a checker bump must add its version to the
  saved-version set.
- Stop the Polaris native regression from reporting a cleanup failure when
  container creation failed and no container exists to remove.
- Isolate the Lean proof checker's submission sources and exporter: submissions
  pass a command allowlist and screen for code-running forms (`unsafe`, `run_tac`,
  `by_elab`, `include_str`, `bv_check`, `bv_decide`, `bv_decide?`, `native_decide`, non-allowlisted
  attributes and `set_option`), and the exporter reads only
  the compiled module from a fresh folder. The checker policy digest and verifier
  fingerprint change, so approvals and proof campaigns must be renewed; submissions
  using macros, custom syntax, evaluation commands or other attributes are now rejected.
- Let host behavior checks declare approved `host_environment` variable names.
  Each check receives only its own operator-supplied values; doctor, evidence,
  and local and Notion Claims Specs show names and availability, never values.
- Add `mentat evidence render` for a standalone offline HTML report of saved runs.
- Add approved Lean proof campaigns (`mentat proof prepare` and `proof check`)
  on a dedicated Linux checker, plus explicit claim assurance and
  source-mutation qualification, with bounded diagnostics for checker failures.
  Checking and verification take campaign targets and approved proof source
  from the approved contract, so edited campaign state cannot weaken a target.
- Add `--trusted-chat-approval` to `spec approve` and `accept` for an agent
  relaying the user's explicit decision. Mentat cannot verify the relay and
  records its source as `unverified_agent_relay`.
- Show a relayed approval as an unverified agent relay in `report.md`, the HTML
  overview and comparison, `spec check` (terminal and `--json`), and `verify`
  output. The label is display only: no verdict depends on it, and stored approval
  and acceptance records are unchanged. Newly saved runs under a relayed approval
  carry one extra line in their saved `report.md`. The HTML acceptance row now
  shows any recorded decision source, not only the relay. Verifier modules changed,
  so approvals, proof campaigns, and pending acceptances must be renewed, and
  `evidence compare` across the change needs `--across-contracts`.
- Install the proof checker with a restrictive bubblewrap policy on hosted CI
  runners (`scripts/install-proof-checker --ci-profile`).
- Batch OpenSpec blob reads while preserving valid neighboring specs, ordered
  diagnostics, provenance, and input limits.
- Explain incomplete Polaris native regressions by stage in JSON, terminal
  output, and exported reports, including failures during cleanup.
- Escape control characters in terminal evidence comparisons while preserving
  the original JSON claim text.
- Add developer-only runtime profiling and a repeatable OpenSpec import
  benchmark, with no timing fields added to verification evidence.
- Repair Mentat's own `check.formal.lean-audit` self-check, which asserted
  audit source text that the trusted Lean driver no longer emits, and run every
  self-check case in the test suite. Approvals of Mentat's own contract must be
  renewed because an approved check file changed.
- Accept Lean installed through a toolchain launcher such as elan, found
  through an absolute `PATH` entry. When the launcher exits with an error in
  the clean environment, Mentat asks it once for the selected toolchain,
  passing only `PATH`, `HOME`, `ELAN_HOME`, `ELAN_TOOLCHAIN` and a fixed
  locale, then runs that toolchain's own `lean` under the clean environment.
  Evidence records that toolchain binary, so comparing runs across a switch
  between a direct install and elan shows a tools change. Direct installs are
  unaffected.
- Run the test suite in parallel by default, and have CI call `scripts/verify`
  so the local gate and the required check cannot drift apart.
- Add a corpus of saved records from v0.1.2 and the pre-split main revision.
  Tests check that these records are read, round-tripped through the current
  writer, compared, and rendered, and that their receipts and integrity checks
  hold. The corpus is generated by the Record fixtures workflow.

## v0.1.2

- Inspect compiled Lean theorem metadata from a trusted runtime driver without
  loading model-defined command extensions. Keep the theorem-origin, checked
  declaration, axiom allowlist, and process limits in force.
- Reject extra audit output and add real Lean regressions for command shadowing,
  private and opaque proof dependencies, Unicode names, and failed drivers.
- Record opt-in formal process diagnostics in the Iceberg CI integration.

## v0.1.1

- Preserve confined candidate symlinks to tracked regular files in snapshots
  and both runners. Keep approved inputs regular-only and reject unsafe targets.

- Add the Iceberg-Go compaction replay pack, with pinned historical revisions,
  eight native row-preservation cases, committed-snapshot observations, and
  separate Lean and Z3 specification checks.
- Prepare verified Go module and build caches explicitly before offline
  verification. Each run compiles its own staged candidate and approved probe.
- Generate a public comparison summary and checksummed paired evidence bundle
  from intact Mentat records. Keep runtime errors distinct from row violations.

## v0.1.0

First public prerelease of Hypertrial Mentat. Install the `hypertrial-mentat`
Python distribution from the GitHub tag and run the `mentat` command.

- Approve a contract pinned to a Git base commit, then verify an exact candidate
  commit with Z3 or Lean and behavior checks. Read grouped claim results,
  compare saved runs, and export a report with replay instructions.
- Render a Claims Spec locally, publish its managed section to one Notion page,
  or draft cited claims from committed OpenSpec scenarios.
- Try the clamp quickstart or replay the historical Nano ID CLI bug. The Nemo
  and Apache Polaris showcases document their pinned workflows and historical
  results.

See the [README](README.md) for install instructions and the
[contract guide](docs/contracts.md) for scope and runner requirements.
