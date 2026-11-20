# Changelog

## Unreleased

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
