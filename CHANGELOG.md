# Changelog

## Unreleased

- Add typed validation for proof campaign manifests and ledger entries. `mentat.records` now
  models the manifest fields and target entries, the ledger entries and the checker results
  that `proof` reads, and the saved lean-comparator checks that `evidence.proof_evidence`
  reads. The hash chain, receipts, budgets and every comparison still run on the raw dicts,
  and the existing error messages are kept. There is no stored-format change: campaign and
  ledger files written before this change still parse. The verifier digest changes (the
  verifier modules `evidence`, `proof` and `records` changed), and the Polaris pack digest
  changes too (it covers `evidence.py` and `records.py`). So approvals, pending acceptances
  and proof campaigns must be renewed: a campaign prepared before this change is refused by
  `check` and `recheck` ("Proof campaign inputs changed") and must be prepared again.
- Add typed validation for check results and the run record. `mentat.records` now models
  formal and behavior check results, negative controls, case inventories and the saved
  `project-verification` run, and `evidence.validate_run_record` replaces the hand validator
  that lived in `evidence_html`. The saved dicts, hashes and comparisons are unchanged, and the
  existing error messages are kept. There is no stored-format change. Two intended changes for
  saved run manifests: a manifest that repeats a JSON key is refused with `duplicate JSON key`,
  and a manifest with a `NaN` or `Infinity` constant is now refused (it loaded before), while
  nesting too deep for the parser is reported as an error instead of crashing. Large valid manifests still load. `SourceQualification`
  bindings no longer constrain their key types, so keys that JSON can carry, such as lone
  surrogates, are accepted as they were before the models. The verifier modules `canonical`,
  `cli`, `evidence` and `records` changed, and the Polaris pack digest changes, so approvals,
  proof campaigns and pending acceptances must be renewed.
- Add typed validation for approval, acceptance and source-qualification records. A new
  verifier module, `mentat.records`, holds pydantic models that check these records at each
  boundary; the raw saved dicts stay what is stored, hashed and compared, and the existing
  error messages are unchanged. There is no stored-format change. Because `records` is a new
  verifier module, the verifier digest changes, and the Polaris pack digest changes too (it
  now covers `records.py`). Approvals, proof campaigns and pending acceptances must be renewed.
- Make the names that Mentat modules share public, with no runtime behavior change. `gitio`
  exports `MAX_FILE`, `MAX_TOTAL`, `MAX_FILES`, `MAX_PATH`, `ls_tree`, `read_blobs`,
  `verify_revision` and `repo_root`; `project` exports `NAME_PATTERN`,
  `safe_relative_path`, `safe_git_path`, `tree_digest` and `validate_staged`;
  `project_formal` exports `LEAN_NAME_PATTERN`; `claims` exports `code_span`,
  `inline_prose` and `escape_prose`; `runner` exports `MAX_RUN_OUTPUT`; `evidence_html`
  exports `acceptance_status`; and the Polaris `replay` module
  exports `MAX_CANDIDATE_FILE`. The old underscore names are removed without aliases. A boundary
  test now fails when code under `src/mentat` imports another module's private name or
  reads one through an import alias.
  `.mentat/selfcheck.py` now patches `project.tree_digest`, which changes Mentat's own
  contract inputs. The verifier modules changed (`claims`, `cli`, `evidence`,
  `evidence_report`, `gitio`, `project`, `project_formal`, `qualification` and `runner`),
  and the Polaris pack digest changes, so approvals, proof campaigns, and pending
  acceptances must be renewed.
- Move canonical JSON, digests and verdict status into a stdlib-only `mentat.canonical`
  module, and move the verifier's Git reads into `mentat.gitio`. Core Git reads (snapshots,
  OpenSpec import, claim checks) now run through the bounded process runner, with no
  inherited stdin, in their own session, and with output caps sized from what each read
  expects.
  The streamed tree listing still starts `git` directly, with the same environment and
  now also `--no-replace-objects`. Polaris Git calls now set both no-replace protections
  (`GIT_NO_REPLACE_OBJECTS=1` and `--no-replace-objects`), and the Polaris upstream test
  read is capped at 2 MiB and passes `--no-textconv`. Some function-local imports are now
  module-level, and the others stay where they are. `canonical` and `gitio` are new
  verifier modules. The Polaris pack digest now also covers `claims.py` and `project.py`,
  so approvals, proof campaigns, and pending acceptances must be renewed. The
  `mentat.evidence` helpers keep their names.
- Run the formal runner, behavior runner, Polaris Docker and Colima helpers' child
  processes through one module, `mentat.process`. Each child runs in its own session
  with no controlling terminal, and any process left in its group is killed when the
  call returns. A timeout kills the whole group, and Polaris Docker calls now do too.
  Colima validation no longer inherits the operator's stdin. Its output is capped at
  1 MiB per stream, and overflow reports "Colima validation output exceeded limit".
  Every clean formal run uses a fresh private 0700 directory as `HOME`, and as its
  working directory where the run does not set its own; the Lean dependency, compile
  and audit steps keep their staged directory as working directory, with a private
  `home` inside it. A Lean toolchain launcher still gets the operator's `HOME` to find
  its toolchain. The verifier modules changed (`process` is new; `colima`,
  `project_formal` and `runner` changed), and the Polaris pack digest changes, so
  approvals, proof campaigns, and pending acceptances must be renewed.
- Extend the developer runtime profiler to time the runner's process, staging,
  and Docker CLI helpers (`runner.process`, `runner.stage`, and
  `runner.docker.<operation>`), and add a **Profile Docker runner**
  workflow. Developer tooling only: verifier code is unchanged, so approvals and
  pending acceptances need no renewal.
- Allow the HTML run report's script only by its SHA-256 hash in the page policy,
  instead of allowing any inline script.
- Retry a failed `mentat spec import-openspec` blob batch one file at a time. A file
  that still fails gets a diagnostic naming that file, its neighbors still import,
  and later batches keep running. A batch in which no file can be read still stops
  the import with one diagnostic on its first path. Batch reads remain the fast path.
  The OpenSpec import is presentation code, so approvals do not need renewal.
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
- Split the behavior runner's per-check closure into host and Docker phase functions, and
  move the verify orchestration out of the CLI into a new `mentat.verification` verifier
  module. Apart from the contract and verifier digests, which change with the verifier sources, verification records, outputs and exit codes are unchanged. Because `verification`
  is a new verifier module and the verifier sources changed, the verifier digest changes.
  Approvals, proof campaigns and pending acceptances must be renewed.

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
