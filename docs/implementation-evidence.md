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

## Inventory after verification generation

The self-run at `40328f6` successfully exercised source-aware specification
correction and bounded capacity recovery, completed SCL and entered IVL. Its
candidate `341a0f7` introduced
`.mentat/verification/check_eligibility_model.py` without a corresponding FAS
surface. Exact candidate validation failed before publication, and the run was
checkpointed without a completed precheck or readiness claim.

The controller now validates the parent inventory and adds only newly staged
verification artifacts as explicit unexamined surfaces with no requirement links.
Existing semantic records and proof statuses remain unchanged. Both review stages
see the complete FAS diff, and publication independently validates the exact clean
candidate before GitHub interaction. Regressions cover a fresh session at the new
candidate, unchanged claim digests, prohibited agent FAS edits, existing omissions
and invalid-candidate rejection before remote side effects. Final exact-revision
completion and renewed live-workflow evidence remain required. The first full
gates exposed five real-boundary integration fixtures without the now-required
FAS. The test owner added valid committed specifications while retaining adapter
and publication assertions; 22 targeted integration/inventory checks passed.
Full native and Linux reruns remain required on the revised commit.

The revised exact commit `65ac32bb59674dc84a8a3be9b8006c7a51ebe5d4`
subsequently passed native and isolated Linux completion (1,773 passed, 53
documented optional skips on each), native and Linux installed-package checks,
and static analysis. Independent code and security review accepted the change;
PR #61 merged as `ed3b872b299663cecff7fb87429c8b0342cf5fb4`.

## Composition across specification and verification reviews

The real self-run at `ed3b872` completed its independently reviewed specification
batches and generated no additional verification files. For candidate
`a2c704a09fb7fb0060830a8fdc96666c08fad03c`, fresh isolated checks passed:
1,773 tests with 53 documented optional skips, installed-package validation,
and all 45 protected checker fixtures with no skips. The configured and FAS
clamp checks covered the same one conditional source obligation, with satisfiable
preconditions and an unsatisfiable violation query.

Both verification reviewers deferred because their no-op proposal used the entire
session-base specification diff while omitting previously reviewed application
sources and the current proof frontend. Earlier source-review decisions were
stored but absent from the later review context. Session
`f0b8c08b2b974a24967952528b815e5e` ended blocked before final review or publication;
this is not a completed workflow or Titanic readiness result. The next change
preserves the cumulative final diff, supplies exact current proof context and
composes validated, unchanged correspondence reviews as process evidence. It
must not convert that evidence into a mathematical proof or silently inherit
unreviewed baseline records. Independent review, completion checks and a renewed
real self-run remain required.

Independent review additionally identified citation normalization that could bind
a filename ending in a line-anchor pattern to another tracked file. The shared
resolver must preserve exact tracked filenames before interpreting anchors.
Repair-chain tests also exposed sibling SCL history and source bindings changed
by approved IIL fixes. Only ancestral reviews may contribute to the candidate;
sibling work is explicitly outside its composed assurance. A repaired source
requires fresh source-aware SCL review before its binding can replace the old
one. Controller-added unexamined rows remain inventory metadata across repairs.
These findings require regression tests and renewed independent review.

Frozen candidate `62fead6760a5a33e622b0d87190b1f0d9ffbe3c5` failed both
native and Linux completion with the same 31 failures, 1,750 passes and 53
documented optional skips. The independent verifier confirmed identical source
manifests before and after. Both package gates and static analysis passed;
the protected checker separately passed 45 tests with no skips. This candidate
was not published or accepted. Twenty-six standalone context tests stopped at
an unnecessary session assertion before their citation/capacity assertions;
five real-adapter publication tests rejected source-only candidates because of
a blanket generation-chain condition. The correction preserves standalone
bounded context selection without inventing historical review authority, keeps
strict composition in actual review jobs, and rechecks the precise accepted-work
reconciliation invariant before publication. All masked tests, full local gates
and independent reviews must be repeated on the corrected candidate.

Adversarial review of the correction found that checking only sibling receipts
missed a late accepted ancestor whose specification the candidate had reverted.
Publication therefore reconstructs full composition whenever accepted SCL receipts
exist. The compatibility path without receipts permits only byte-unchanged FAS/CAS
from the session baseline and establishes no new specification-review authority.
Tests must cover both late ancestor and sibling acceptance before remote calls.

Security review found Git's quoted non-ASCII/control filenames could evade
string-prefix checks on changed CAS paths. Path enumeration now uses bounded,
NUL-delimited raw Git output with strict decoding and preserves exact leading
whitespace. Inventory, staging, correspondence and publication selectors share
this handling, retaining isolated Git configuration and execution safeguards.
Non-ASCII/control-path regressions and a renewed security review are required.
The review-diff command also explicitly disables Git text conversion so configured
converters cannot run on the host while constructing review context or digests.
A marker-converter regression must verify that the fixture would execute without
the flag, then confirm raw source diff output without converter execution.

Corrected candidate `5d6212d9d58f0bb05415bec6dcf9e691b3ca9fb8` passed
Linux completion (1,789 passed, 53 documented optional skips), both package
gates, static analysis and all 45 protected checker fixtures without skips.
Native completion had one failure, 1,788 passes and 53 skips: Git worktree
registration failed while reading another job's `commondir` with `Result too
large`. A similar intermittent native failure occurred in an earlier candidate.
Source manifests and the exact clean head remained unchanged. The failure
escaped future result handling; concurrent registration is a plausible cause,
not established by the now-deleted temporary fixture. A bounded registration
critical section, explicit job failure handling and deterministic concurrency
regressions are required before another exact-revision completion attempt.

The test engineer reproduced three overlapping registrations on the prior code.
After the fix, registration overlap was one while an agent barrier confirmed
three agents still execute concurrently. An injected registration error after
actual Git creation exhausts exactly three attempts, records an inconclusive
finding, preserves the three branches/worktrees and makes no remote calls;
the prior code escaped the future. Both regressions and the previously failing
native case pass. The critical section is per coordinator; the normal workspace
lock excludes another controller using the same store. Independent state roots
or external Git administration remain outside that lock. Renewed independent
reviews and exact-revision full native/Linux gates remain pending.

The exact registration candidate `63f5121647abed4ade0e9daf714b9df86adee558`
subsequently passed both full suites: 1,791 passed and 53 documented optional
skips on each platform. Native/Linux package checks and static analysis passed;
the protected checker passed all 45 fixtures without skips. The independent
verifier confirmed 226 unchanged before/after hashes and a clean exact head.
Two independent reviews accepted the commit. PR #62's hosted syntax/whitespace
check passed in seven seconds; the guarded merge produced
`c3bbb47399e5ac3f5987194a8205d5007748057f`. This is code acceptance, with the
checkpoint lifecycle and renewed real self-run still required for readiness.

## Preserving accepted checkpoints during repairs

The real serialized regression showed that a prequeued repair used the immutable
session head and created a sibling of an accepted SCL mapping. The publication
guard correctly blocked its loss. First-round investigation now selects a fully
validated compatible accepted checkpoint at execution, preserving original source
evidence, reproduction, entailment and domain-adjudication identities. The actual
prompt, source context and observed generation parent identify the selected head.
Whole application/caller trees, modes, gitlinks and CAS context must be unchanged;
established requirements and their linked claim/contract component are compared
exactly. Incompatibility is an explicit bounded gap before agents or remote calls.
Retries retain the repaired head, and the existing publication guard fences later
accepted work.

At `2741cc6aff6740aafe49e5bc342b4bfa7156c89a`, both affected autonomy modules
passed all 159 tests. Eleven real-review compatibility cases and the actual
promoted repair/retry exercise source, caller, Unicode, mode, CAS, requirement,
contract, forged-review and incomparable-checkpoint boundaries. Two independent
reviews accepted this scoped change. Rebase onto the PR #62 merge preserved the
exact production and test SHA-256 values. Independent full exact-revision
completion and renewed real workflow evidence remain pending.
