# Changelog

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
