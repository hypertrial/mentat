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

The batch follow-up at `90d034b` passed independent macOS and Linux checks with
1,705 tests passed and 53 documented skips on each platform. Packaging and Pyright
passed, with the 226-file manifest unchanged after verification. It merged through
PR 54 as `c6b115a`. A fresh real self-run advanced input batches and proved the
scoped clamp obligation, but stopped without publication after independent review
requested a CAS correction and the revised draft failed inventory validation.
The initial draft introduced a CAS file without inventorying it; revision then
used the larger draft inventory. The correction inventories every staged candidate,
adds explicit unexamined rows only for new paths, and keeps the original cumulative
diff through review retries, failed prechecks and deferred source batches. Two
independent reviews identified the additional continuation paths and sent them
back to the owner for fixes and prompt-level regressions. Existing omissions remain errors. Full checks,
independent acceptance and another real self-run remain required.

## Large-specification edit interface

The next real self-run at `37cda52` advanced source batches and independently
reviewed both unchanged drafts and a CAS addition. Several generator responses
explicitly identified supported observations but deferred because safely replacing
the complete 70 KB FAS was impractical without editing tools. Independent review
confirmed this as a specification-editing bottleneck. The optional `patch_fas`
protocol addresses individual records with exact file/record digests and preserves
the existing complete-candidate validation and independent review gates. It grants
no execution or filesystem tools to agents. Protocol checks and renewed real-run
evidence remain required before that capability counts as ready.

That run later stopped after repeated provenance revision requests. A generator
cited its actual input revision; the resulting candidate had a different hash,
and reviewers asked the versioned document to cite that resulting hash instead.
The next edit necessarily produced another hash. Its generic prompt also called
the static session head the repository head while supplying job-specific source.
The correction distinguishes immutable observed source provenance from candidate
identity; it does not relax exact-revision execution evidence or review freshness.

## Required source in specification review

The real self-run at `83ccc7a` successfully used compact FAS patches. Its first
two candidates received two independent accepting reviews each, with explicit
partial scope and immutable observed-source provenance. The third candidate newly
mapped `src/mentat/evidence.py`; both reviewers deferred because the review context
omitted that file after reserving the complete proposal diff. The run stopped
before IVL, final review or publication and did not report readiness or a full
implementation proof. The correction reserves sources required by changed FAS
mappings before optional context and retains complete specifications and diffs.
Required material that cannot fit produces an explicit capacity result with
split/revise guidance. Targeted regressions, independent reviews, completion checks
and a renewed self-run remain required.

Two independent reviews additionally found that string-form or missing repository
citations could be skipped. The owner added explicit local-citation resolution and
failure handling, with external URLs labeled uninspected. Both reviewers confirmed
their findings closed; full completion and renewed real-run checks remain pending.

## Bounded recovery from review capacity

The subsequent real self-run at `9faf4e1` completed three independently reviewed
SCL batches. Its fourth proposal newly mapped ten sources and changed no
requirements or claims. All ten sources were required, but their full contents
exceeded the review file-content budget. Both review jobs retried the same
oversized input until exhaustion; no agent approved it, and IVL/final publication
were not reached. The correction preflights specification-review capacity and
returns a controller constraint to a bounded generator revision from the last
accepted base. It preserves rejected drafts, established specification identities
and the remaining source frontier. Missing or malformed evidence is not reclassified
as capacity. New regression, independent-review and completion evidence remains
required before another real readiness run.

Independent review found that inferring recovery from an older FAS checkpoint
could discard a later approved application fix. The regression reproduced that
rollback. Recovery now carries a controller-owned accepted code/specification base
through every SCL continuation and validates its ancestry; missing or invalid
older metadata stops recovery without a guessed reset. Focused tests retain the
approved fix and established IDs, preserve rejected drafts and pending paths,
exercise two actual reviewers and IVL continuation, and retain the three-round
limit across resume. Full completion and renewed real-run evidence remain pending.

## Isolated worker packaging

The real self-run at `b60e1ba` completed all SCL source batches, exercised bounded
capacity recovery, and entered IVL. Candidate `7b8665c` passed 1,754 native tests
with 53 documented skips and all 45 protected checker fixtures with zero skips.
Two source checks proved the same single clamp obligation; these are not two
distinct properties. Packaging remained incomplete and final review/publication
were not reached. A graceful checkpoint preserved the session and its retry ledger.

The package gate exposed missing uv resolver metadata despite cached pinned wheel
archives, then a writable cache inside `/work` being included in the source
distribution and exceeding the 256 MiB file limit. The corrected runner binds its
per-job cache outside the source checkout. Artifact validation also clears inherited
Python import overrides and uses Python archive inspection rather than an undeclared
`awk` dependency. A disposable fixture based on that candidate with the artifact
script correction passed the exact real package gate through the corrected runner;
separate real wheel/sdist inspection excluded the cache marker, while the old-layout
control included it. This is fixture evidence, not integrated-candidate acceptance.

Provisioning now resolves only the existing five pinned Hatchling dependencies
through public uv commands. A clean-cache guest probe first reproduced offline
resolution failure, then produced both artifacts as `nobody` with Python 3.12 and
3.14 after seeding; selected pins remained unchanged. Nine focused regressions
passed without new skips. Independent review, integrated full checks and explicit
fresh-worker/profile initialization remain required. Independent security review
additionally found checkout module shadowing in Python archive inspection. All
inline artifact checks now use isolated Python, and a hostile `tarfile.py` regression
rejects Git metadata without executing the checkout module.

## Source context for specification corrections

The fresh-profile self-run at `f0ed32f` completed independently reviewed source
batches, but a later correction could not inspect the test file named in reviewer
feedback. Both failed revision attempts changed the mapped
`tests/test_source_control_flow.py` note while that targeted file was omitted from
their generator context for capacity. The strict mapping check rejected both;
IVL and final publication were not reached. Read-only replay showed that the
review context contained the same complete source and a 6,589-byte diff, while
the correction context treated the file as optional. Corrections now reserve
the mapped/cited/contract sources of their unreviewed input before optional
context, retaining the existing bounds and accepted-base recovery. New regression,
independent-review and completion evidence remains required.
