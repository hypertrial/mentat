# Audit improvements

## Intended behavior

- Load the validated saved run, including its manifest digest, in one integrity pass per bridge read. Check every declared artifact and retain exact-scope and mutation-time validation.
- A cancellation request prevents the next behavior check or negative control from starting. An already running process retains the current termination and uncertainty semantics.
- The controller uses bounded, purpose-specific queries for session metadata and pending human feedback. Keep the durable acknowledgment and enqueue behavior across retries, resumes, and crashes.
- An atomic, explicitly versioned SQLite migration adds indexes for session event and feedback reads. Keep the integration protocol version independent of the datastore version. Preserve current sessions, jobs, leases, event order, control receipts, and one-time recovery of earlier supported layouts.
- Bound the cost of session monitoring while preserving independent observations of Git and trusted inputs. Reuse an immutable published snapshot only while its observation is within the five-second freshness window and its ledger state and controller still match. Preserve the original observation time and expiry. Continue to observe external identity even for quiet ledgers. Avoid repeated claim-scope scans within each projection and retain existing output privacy, deadlines, caps, and page semantics.

## Regression surfaces and verification

- Characterize single-read bridge validation, tampered artifacts, acceptance and comparison, and completed-operation recovery. Add cancellation coverage between configured checks and before negative controls.
- Exercise upgrade and repeat open on persisted state, version rejection, event query plan, idle feedback work, and replay and acknowledgment after restart.
- Exercise snapshot reuse, concurrent requests, expiry, unchanged-ledger repository drift, controller change, and immutable pagination. Compare projection before and after on large synthetic histories.
- Run the affected tests, `scripts/verify-fast`, `scripts/verify` on macOS, and `scripts/verify-worker` with the protected Linux fixtures. Record the outcomes against the inspected tree.

## Scope

This refactor preserves stdio bridge and integration v1 response semantics. Measure Docker staging, live workflow latency, and other conditional ideas before changing them. Preserve checker isolation and approval authority.

## Implementation evidence

- A 512 KiB format-2 bridge fixture read 531,582 artifact bytes in one integrity pass, down from 1,063,164 bytes across two passes.
- A scoped feedback read at 4,097 synthetic events took about 0.21 ms median. The previous idle path loaded the whole snapshot and took about 4.93 ms median in the same fixture.
- A disposable Titanic run at `92b038552bf6290ed0bcb869e1e2db9a5e4797c2` passed all 11 formal checks, five behavior checks, and ten source qualifications. It used the existing pinned image in `colima-mentat`, with separate local approval and evidence state. Across 15 staging operations, median archive creation took 4.2 ms and container start took 4.0 seconds. The timings supported retaining per-check archive creation and adding `runner.stage_archive` to the profiler.
- `scripts/verify-fast` passed. The source and test tree was based on `7621bd950fd4633f79e6a1cc073b25cdde937277`, with SHA-256 `86afc844b1cce26064689785550fa145770418cac435c14b66598b52dbccab5a` over the file-checksum manifest for `src`, `tests`, `scripts`, `pyproject.toml`, and `uv.lock`.
- `scripts/verify` passed on macOS with Python 3.12.14 and the locked environment. All 2,367 executed tests passed. The 53 skips required the dedicated Linux proof checker, Lean 4.31.0, or local Iceberg Go and Nano ID source mirrors. The dedicated proof-checker gate ran separately below.
- `scripts/verify-worker` passed all 45 tests with zero skips in the dedicated Linux VM. The source, verification scripts, lockfile, and proof-checker test manifest matched between the verified Linux copy and the final checkout at SHA-256 `fdec36d2ef019ad627c3f55be48088d50971f7f913a7935d22d8f5c031db8d0c`.
- Independent review found no blocking issues. Independent targeted verification passed 14 tests covering evidence integrity, migration and feedback replay, monitoring freshness, cancellation, and profiler behavior.
- Cockpit's installed Mentat 0.1.2 bridge read all four Titanic claims and the passing verification and qualification evidence from the disposable run.

Existing saved evidence remains readable. Contract approvals bind the verifier identity and need renewed review when that identity changes.
