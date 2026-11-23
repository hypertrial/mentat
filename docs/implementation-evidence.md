# Autonomous assurance implementation evidence

The approved scope and delivery gate are in [implementation-plan.md](implementation-plan.md).
This record distinguishes implemented behavior, completed checks, and pending readiness work.

## Local workflow foundation

PR [47](https://github.com/hypertrial/mentat/pull/47) merged as
`ae4d167faef4e07d9e5b85817e4dd47ae1ceceeb`. Both independent reviewers accepted the
final workflow change. At its exact head `8ff7485`, the isolated macOS gate passed
1,480 tests with 53 documented platform/prerequisite skips; the normal Linux gate
passed the same counts. The protected Linux checker ran 45 tests with zero skips
against `1ea474f`; its protected source and fixture bytes are identical at `8ff7485`.
GitHub's required syntax/whitespace job passed in eight seconds.

## Feature checks

- The complete local feature suite passed: 1,635 tests, 53 documented legacy
  platform/prerequisite skips, 84.62 seconds. Later changes require renewed checks.
- The wheel and source distribution built and passed the existing isolated artifact
  installation, metadata, CLI, packaged-resource and evidence-rendering checks.
- Independent reviews covered source soundness, tool identities, isolation,
  durable state, specification lineage, cancellation, guarded publication and
  recovery. Findings were returned to their implementation owners and re-reviewed.
- The source backend's 31 targeted tests include vacuity, unsupported semantics,
  source identity, large unrelated modules, Z3 binding changes, and stale bytecode.
- Real Codex probes confirmed GPT-6.1 Terra with high effort and data-only proposed
  changes. Execution tools, apps, MCP and browser access are disabled for loop agents.
- The dedicated Ubuntu ARM64 worker provisioned successfully. Actual execution
  confirmed no host mounts, enforced bubblewrap confinement, and no candidate network.
- Browser inspection confirmed all six CAS areas, explicit unattempted claims,
  filtering, optional feedback and stop requests. Authenticated requests reached
  the local controller. The fixture asserted no successful proof. The browser's
  narrow viewport measured 600 pixels with no horizontal overflow; a requested
  390-pixel override was clamped by the browser surface and is not a 390-pixel check.
- Seven saved-view regressions, plus a missing-authority-pin case, exercise clean,
  changed, dirty and missing sources, candidate revisions and policy drift without
  mutating historical records.

The first live disposable fixture found its seeded off-by-one counterexample.
Independent agents conservatively deferred its atomic obligation and identified
a separate CLI integer-formatting boundary. This exposed an adjudication-prompt
gap: atomic consequences must not be mistaken for complete coverage of a broader
requirement. Independent reproduction and binding/domain review remain required. A second real run accepted the function-level violation, reproduced it in Linux, independently established binding/domain and generated a repair. Reviewers correctly deferred without candidate check results; the controller now runs protected checks before acceptance review and binds every review to the exact evidence cohort. Graceful interruption checkpointed the run as paused.

The isolated source-verifier PR at `a845547` passed 1,513 tests with 53 documented skips on both macOS (85.28 seconds) and Linux (35.17 seconds). Its mandatory protected checker gate passed 45 fixtures with zero skips in 267.97 seconds after worker provisioning. The actual Python 3.14 offline Hatchling bootstrap passed inside bubblewrap. Titanic's unchanged native baseline passed all 43 tests locally: Python statements 386/386, branches 112/112; JavaScript statements 56/56, branches 44/44. Its complete serial Linux browser gate also passed all 43 tests and all four coverage counts. The initial interrupted parallel diagnostic is excluded from acceptance evidence.

## Readiness still to complete

Finish the corrected live fixture, real Mentat self-run, final exact-revision local
and protected-worker checks, independent acceptance, and focused PR delivery.
Titanic onboarding remains gated on that readiness result. Its existing runtime
and data have not been changed. Titanic's current private-repository plan does not
expose enforceable branch protection through GitHub; its automatic merge path must
remain disabled unless that capability becomes available.

## Merged workflow and self-run context follow-up

The ordered workflow, source verifier, durable state, isolated runtime, localhost UI and coordinator changes merged through PRs 47–52. Exact coordinator PR head `1663c83` passed independent macOS and Linux completion checks: 1,684 passed and 53 documented legacy/platform skips on each. Packaging, installed worker resources, baseline profiles and Pyright passed, and all 226 file hashes matched before and after. The unchanged protected checker scope passed all 45 fixtures with zero skips at `211a57d` in 319.61 seconds. Real normal-completion and candidate/checker SIGTERM probes confirmed that interrupted services remain incomplete.

The worker is installed in the standard persistent Lima directory and ordinary CLI diagnostics report it ready. Existing GitHub authentication is available. No new signing or remote test attestation system was introduced.

The first actual self-run at `d95b45b` proved the committed clamp obligation, then correctly deferred specification generation because its 70,483-byte FAS exceeded the generic 64 KB context limit. This was a context-delivery defect, not missing repository data. The follow-up reserves complete FAS, configuration, OpenSpec and full review diffs; preserves all prior IDs; supports bounded targeted files up to 240 KB; and requires progress or an explicit capacity failure. Nine focused regressions and two independent reviews passed. Direct inspection confirmed all 20 existing FAS lineage entries and the complete coordinator source in a targeted chunk. Full follow-up completion checks and a renewed self-run remain required before Titanic onboarding.

## Live fixture and deferred source batches

The independently inspected [clean identity fixture](evidence/identity-fixture.json) reproduced value 0 returning 1 at baseline `ad4b817`, then verified candidate `984efb54` returning 0. Its native gate, one unique source obligation and protected repair replay passed. Fix, specification, verification and final review each had two independent accepting reviewers. Three engine proof checks covered the same single obligation; they are not three distinct properties. Publication paused solely because this deliberately disposable repository had no remote. The original checkout remained seeded, while the verified candidate was retained in Git.

The renewed self-run at `da4f6e3` received the complete FAS, then conservatively deferred over omitted source material. The controller previously advanced bounded source batches only after an accepted draft. The follow-up makes incremental drafting explicit and advances deferred input batches automatically with strict frontier progress and a fixed inventory-based bound. It retains mandatory context, prior IDs, independent reviews, and explicit terminal capability gaps. Completion of a self-run remains required before Titanic onboarding.
