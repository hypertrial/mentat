# Verified Mentat audit fixes

## Intended behavior

- A protected proof campaign uses the same host lock across temporary directories and state roots for the same operator. Contention refuses a second check before recording an attempt.
- The execution fingerprint covers the source-correction and review-input validators. An update invalidates the prior execution identity, and an in-process source change requires a restart.
- The integration adapter retains its published rendezvous file descriptor until shutdown. It leaves replacement files in place and closes descriptors after failed publication.

## Invariants and regression coverage

- Preserve kernel-managed lock ownership, nonblocking contention and the checker’s current permission boundary. Exercise default and overridden temporary directories.
- Use the same source reader for loaded and current execution code. Resolve imported module paths on each check so source redirection remains detectable. Test both omitted helpers with isolated source fixtures, including a different identity for a fresh runtime and refusal after an in-process source change.
- Carry forward the focused rendezvous regression from the existing fix. Test normal shutdown, a replacement path and publication failure with disposable loopback state.

## Verification

Run the focused proof, autonomy identity and integration tests, then the Mentat completion gate on macOS. Run `scripts/verify-worker` on the dedicated Linux checker worker. Record revision, outcomes, and any unexercised prerequisites.

## Verification evidence

- Local branch `fix/verified-audit-bugs` starts at `50ce798c6a539e7004dd7a12dc35dd3928997003`, the existing rendezvous fix from PR #87. The audited main revision was `bc931550cd87353a21a1e4ca0d06b3d54abc0d90`.
- `python -m pytest -n 0 --tb=no -q tests/test_audit_regressions.py` failed all three new cases with the baseline package on `PYTHONPATH`. An independent verifier passed eight focused macOS cases with `uv run --locked --no-sync pytest -q -n 0 tests/test_audit_regressions.py tests/test_autonomy_api.py tests/test_autonomy_git_transaction.py::test_execution_identity_rejects_changed_transport_source tests/test_autonomy_spec_scope.py::test_execution_identity_rejects_changed_scope_helper -k 'execution_identity or host_lock or published_rendezvous or cleanup_does_not_unlink_replacement or failed_rendezvous_publication'` on the corrected source.
- `PYTEST_XDIST_AUTO_NUM_WORKERS=8 PYTEST_ADDOPTS=--dist=worksteal scripts/verify` on macOS passed Ruff, the claims check, and 2,354 tests. The 53 skips required the Linux checker, the Lean 4.31.0 toolchain, or local Iceberg Go and Nano ID source mirrors. Both module-path drift failures found in the initial gate passed in this run.
- `scripts/verify-worker` on the dedicated Linux worker passed all 45 protected checker fixtures with zero skips. The repeat run after the source-path correction also passed the same eight focused cases listed above.
- Cockpit passed `pnpm exec vitest run tests/unit/mentat-integration.test.ts` with `MENTAT_BIN` set to the executable from a wheel built and installed from this worktree.
