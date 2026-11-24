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

Lifecycle candidate `4bbc364a8f61901016f792b57fb60b08256037a6` subsequently
passed independent completion: 1,805 passed and 53 documented optional skips on
both native and Linux, both package checks and static analysis, and 45 protected
checker fixtures without skips. All 226 source hashes stayed unchanged. Two
independent reviews accepted the exact commit; PR #63's hosted check passed in
seven seconds and merged as `cebafba1f3eabe1fdf4572ea16b06bd120dfd1c7`.

## Reviewing pre-authored initial feature specifications

Titanic preflight found that a feature head already containing CAS/FAS could pass
incremental source reviews yet fail composition against the remote base, because
the initial review omitted the pre-authored records. The first cumulative review
now retains the remote base while generation and recovery retain the actual
feature or approved repair checkpoint. Explicit correction bases remain intact;
later incremental batches require a fully validated ancestral review cohort.
Regressions cover no-op and corrected proposals, real capacity recovery, a repair
before initial convergence, forged authority and missing sources. Combined
development feedback was 163 passes and one invalid legacy fixture using literal
`base`/`head` instead of Git objects. The test owner replaced those placeholders
with real commits while retaining the consumed-once assertions; six affected
cases then passed. Independent exact-revision completion remains pending.

Frozen `dab7e5805be64416246a1868dab19e2f1e8d1deb` failed the static type
gate: Pyright could not narrow an optional recovery checkpoint through the
separate validity booleans. This was the sole failure in both full suites
(1,809 passed and 53 documented optional skips on each). Both package checks
and all 45 protected fixtures passed; source hashes remained unchanged. The
debugger added an explicit nonempty-string guard, retaining the existing
inconclusive path for missing/invalid checkpoints. Full Ruff, the configured
Pyright test and ten targeted capacity/initial-review tests pass after the fix.
Renewed exact-revision completion remains required before publication.

Corrected initial-review candidate `c895fab4fa9e0c342cd08ca0a159e1f97caddfd9`
passed independent completion: 1,810 passed and 53 documented optional skips on
both platforms, both package gates, Ruff/Pyright and all 45 protected fixtures
without skips. The 226 source hashes and clean head matched before/after. Both
independent reviews accepted the commit. PR #64's hosted check passed in six
seconds and merged as `e6016c5a917f5cf26c9c040569aa3a0cd3b52959`.

## Candidate-bound counterexample acceptance cases

The controller now supports declared negative-control paths/functions using the
positive source contract. It obtains a real solver witness for the committed
variant, independently checks its Git identity, assigns controller evidence
identity and uses the existing protected runtime replay. Results are separate
from positive claim proofs and do not authorize IIL repairs. Generators see a
pending plan; acceptance reviewers receive the actual candidate-bound evidence.
New local v2 policies pin the ordered source checks and controls; v1 no-case
workflows retain their existing authority and cases require explicit new state.
The worker profile remains unchanged.

Initial focused development feedback passed 47 cases, including real Z3
refutations, nullary witnesses, strict replay bindings, positive-proof projection,
policy compatibility and complete thirteen-case review summaries. Independent
review then found correction feedback type handling, cancellation/deadline and
same-function multi-contract compatibility issues. Their owners must fix and
regress these before freeze. A real isolated-VM replay, complete local gates and
a renewed actual workflow remain pending; mocked worker results are not evidence
of an actual protected runtime execution.

The owners corrected the review findings, restored standalone/no-case generation
and fix-review behavior, and retained multiple legacy contracts on one function.
After source freeze, the escalated offline autonomy/static development gate
passed all 343 tests in 253.32 seconds with unchanged source/test hashes. The
earlier mixed-state/sandbox run remains diagnostic only. A final compatibility
review found the public `timeout_ms` option; validated configured timeouts now
remain pinned/keyed, are capped to the remaining budget, and are excluded from
the logical five-field contract. Nine focused timeout/projection tests, full Ruff
and the configured static gate pass. Independent exact-commit reviews, an actual
protected VM case and complete native/Linux acceptance remain required.

At frozen `adf0d13b5a4625af853b963d5bf9ef493481bc07`, the independent
verifier executed the real case primitive. Z3 produced the integer witness
`value=1, lower=-1, upper=0`; protected CPython 3.12.13 confirmed a true
precondition and false postcondition with matching source/blob/contract/tool and
evidence identities. This is direct selected-function replay, with module
binding/callers outside scope. Both package gates, static analysis and all 45
protected fixtures passed. The complete suites each had 1,876 passes, 53 optional
skips and one failure: the repository's module-boundary rule prohibits importing
the private replay constant across modules. The verifier confirmed 227 unchanged
source/script/test hashes and a clean exact head.

The implementation now exposes an immutable public replay-program getter;
execution and program contents remain unchanged. Getter parity is checked against
actual CPython execution and review-context source/hash. Boundary/source-case/
static checks pass (159 tests), alongside full Ruff. Renewed exact-revision VM
and completion evidence remains required.

## Renewed real repair-loop fixture

Using controller `adf0d13` and an explicitly initialized v2 local policy, the
identity fixture again produced the actual SAT witness `value=0` against
`ad4b817979cca46316944debcfe4ff98abc9caf1`. Protected reproduction and distinct
entailment/domain adjudicators confirmed the defect. Repair candidate
`5797f66cfb0048cb3f0ff1133a9e2898beceae25` passed its actual gates, source
proof and original-witness repair replay, followed by independent fix, refreshed
specification, verification and final review pairs. Session
`90389eb32c654b9895753df1b670b12e` retained 26 jobs, six requirements and one
claim. The original-head failing-check branch remained inconclusive while the
repair branch continued.

The disposable repository deliberately has no `origin`; publication failed and
the session paused with an unconfirmed remote outcome. No publication or merge
is claimed. This exercise demonstrates the actual repair/review path under the
new policy; real Mentat publication and whole-workflow readiness remain pending.

## Current self-run acceptance gap

PR #70 passed both independent reviews and native/Linux completion (1,995 passed
and 53 documented optional skips each), both packages, all 45 protected fixtures
without skips, fast/static checks and the complete 296-path inventory. All 233
recorded source/script/test hashes stayed unchanged. The hosted check passed in
five seconds; the guarded merge became
`971bc390423b4814298c93f0eed42a2927852ac5`, with the same tree as the verified
candidate.

Fresh actual session `38b9e5429c414c98a5f11b0a87e6b9b4` completed thirteen
accepted SCL cohorts without the previous provenance deferral or a capacity
failure. Candidate `d08d6c77339b4c8407e46c7224fb87616a69c521` passed its configured
Linux suite (1,995 passed, 53 documented optional skips), package check and all
45 protected fixtures. Its selected clamp had SAT preconditions and UNSAT
violations; the faulty variant produced the real SAT witness value 1, lower -1,
upper 0, whose protected CPython 3.12.13 replay had a true precondition and false
postcondition. Both scoped IVL reviewers accepted.

All eighteen real source reviewers across nine batches then deferred. Their
persisted exact prompts confirm the source-review mode omitted the existing
cited-source continuity descriptors, so they could not establish the historical
revisions in otherwise supported current observations. One test batch also lacked
`src/mentat/source_verify.py`, an explicit citation of its linked requirement.
Its actual 321,901-character files subtotal and 428,817-character context had room
for that file's 23,470-character JSON entry under both unchanged limits. No other
distinct source defect was identified in those reviews. The session ended blocked
without aggregate review or publication. Its scoped proof evidence remains
historical; no deferral is treated as acceptance. Corrected bounded input, fresh
independent reviews/completion and another real run remain required before Titanic.

The scoped context correction passed 13 source-batch workflow, provenance,
support and eviction tests, plus the existing 17-case citation suite and static
checks. Representative cases fail against the prior controller. A read-only
preflight of the actual v8 candidate retained 66 obligations in nine batches;
maximum source JSON was 463,530 characters, with no affirmative descriptor for
an absent file. The previously omitted source verifier is now included in the
linked test batch: files subtotal 349,872, complete context 463,530. Aggregate
JSON was 493,929 characters (494,464 including wrapper) with eighteen synthetic
4,097-byte findings, one byte above each review's allowed maximum. These are
capacity and regression results only; historical deferrals remain unchanged.

Independent review additionally found that directly cited or formal-target
obligations with unlinked inventory surfaces could miss their related record
context. Selection now follows those explicit path relationships as well as
surface requirement IDs, using complete verified baseline/current inventories.
Three positive link regressions fail against the prior candidate, and the
literal-`#L1` filename boundary remains isolated. The complete source-review test
file passed all 68 cases; static checks and the repeated v8 capacity measurements
passed. Fresh frozen-head review and completion remain pending.

PR #69 passed both independent reviews and exact-head completion: canonical native
and Linux suites each passed 1,993 tests with 53 documented optional skips; the
protected checker passed all 45 without skips; both packages, fast/static checks,
296-path inventory and 233 unchanged source/script/test hashes passed. The hosted
check passed in ten seconds. It merged as
`f56a716e77e3f2c2c5cc465c15f765091da0441e` with strict/admin-enforced protection;
the merged tree matched the verified feature tree.

Fresh actual session `ef847e0daded45949f32bd9d91cfcefd` accepted five SCL cohorts,
including real corrections, then blocked on a provenance deferral before current
mandatory checks, final source batches or publication. A corrected Docker-runtime
note stated supported current behavior but also asserted that a previous
description was mistaken, citing two distinct task-input revisions. The
correction generator had seen the earlier note and feedback; the later review
diff started at the earlier gap baseline and did not supply that intermediate
note. Both reviewers found the current classification supported, but one deferred
the unsupported retrospective assertion. The controller correctly left the
ambiguous citation unverified. No capacity failure occurred, and no readiness or
publication is inferred. Current-source correction guidance, regression review
and completion, then another genuine self-run remain pending. The blocked
session and its attempt counts remain unchanged.

Both independent reviews accepted frozen candidate
`f8d5e616198bcd52434ee9619923731c496279e3`. Its full native and Linux gates each
reported 1,983 passed, ten failed and 53 documented optional skips. All ten
failures share an adapter-specific fixture that constructs publication proposals
without the new source-stage precondition; they stop before their intended
adapter and specification-staleness assertions. Read-only diagnosis found no
production defect in those traces. The fixture correction must preserve every
original assertion, while genuine source receipts and publication rejection
remain exercised separately. Fresh committed-head review and completion remain
pending; these failed runs do not establish readiness.

The corrected fixture isolates only the new source-coverage validator as an
adapter-test precondition. The entire adapter integration file plus the genuine
source-receipt/forged-prompt publication workflow passed all 17 cases. Production
code was unchanged; a fresh exact-head full gate remains required.

At that same unchanged candidate, both package checks, fast/static checks and
296-path FAS inventory passed. The dedicated protected checker passed all 45
fixtures without skips. All 233 source, script and test hashes matched before and
after; manifest SHA-256 was
`45695d356b0659ee47fa308c549fb0f0a0fc9d4007a21ca9b80e53e5da4d61a6`.

The bounded final-source implementation adds a current-head coverage plan,
independent source-batch pairs, private exact-input manifests and aggregate
review. A read-only preflight of the archived v6 candidate derived 65 obligations
in eight batches; the largest source context was 471,466 bytes. Aggregate context
was 494,872 bytes including its wrapper with sixteen synthetic 4,097-byte
findings, one byte above each review's allowed maximum. These are capacity
measurements, not real acceptances, and no historical decisions were changed.

Independent review found three defects: structured accepted output could
be omitted from aggregate findings, an artifact replaced with a FIFO could
block before file-type validation, and a CAS-only edit could leave its unchanged
FAS requirement and mapped source outside the source plan. The planner now follows
both baseline and current CAS paths; the repeated v6 sizing measurements above
are unchanged. Final independent review of that fix remains pending. Accepted
source reviews require the other structured output arrays to be present and
empty; nonblocking reads reject
nonregular artifacts before consuming them. Regression checks cover the actual
runner's prompt, scope and receipt tampering, private path/content boundaries,
growing reads, FIFO substitutions and publication before any GitHub effect.
Final committed-head reviews, native/Linux/package/protected completion and a
fresh published self-run remain required before Titanic onboarding.

The terminal v6 session and merged proof-context feature were preserved before
pruning their 62 retired worktrees and local development branches. The reviewed
archive helper retained Git bundles, full working-tree and index snapshots,
coherent state and all raw agent/evidence files in private controller history
`20261123T222850Z-v6-retired-worktrees/manifest.json`. Both current coding owners
confirmed quiescence; independent process and open-file audits found no selected
users. The controller lock remained held through dry-run, prepare and retirement.
Post-retirement Git lists only clean `main` and the active bounded-review branch.

Proof-source candidate `a06e9b11b335f4576bcb64276a5e1230bd7bc200`
passed both independent reviews before final code completion: native and Linux
each 1,937 passed with 53 documented optional skips; both package checks,
Ruff/Pyright, complete 293-path inventory and all 45 protected fixtures without
skips passed. All 230 source/script/test hashes and the clean head matched before
and after. Both actual saved v5 review inputs independently reconstructed with
the contract and mapped test present. PR #68's hosted check passed in ten seconds
and merged as `2f98e3aba372639906d304ac0bfcca98bc835882`.

Fresh session `1b305614c3dc40b6967772e689429dfb` completed fourteen accepted
SCL cohorts, including genuine bounded capacity recovery. Candidate
`8eb5c9bf8b465ad242e73f38fc27f9eac3ed7600` passed its configured full suite
(1,937 passed, 53 documented optional skips), packaging, all 45 protected fixtures,
scoped positive clamp proof and real protected negative-case replay. Both IVL
reviewers accepted and explicitly assessed the supplied clamp source and declared
contract. The complete per-reviewer input-file list was not durably recorded in
the old outcomes, so no stronger inspection claim is inferred.

Both genuine final reviewers deferred: 57 of 62 cumulatively changed mapped
sources were omitted. Those 62 files total 1,352,139 raw bytes / 1,433,089 JSON
characters, exceeding the unchanged 360,000-character file-content limit. The
477,882-character final context retained full diff, proof/control and historical
cohort descriptors, but those descriptors did not durably attest each reviewer's
supplied source or include substantive findings. A retrospective reconstruction
confirms one earlier pair saw `gitio.py`; it does not manufacture missing actual
input attestations or semantic correctness. Session v6 ended blocked without
publication. Current-head bounded source review and exact input provenance,
independent review/completion and a real published self-run remain pending.

Final bounded-context candidate `e5b6becb5d00d5cb4df14f27d8b007166e3e8f39`
passed both independent reviews before final completion: native and Linux each
1,899 passed with 53 documented optional skips, both package gates, Ruff/Pyright,
complete 292-path inventory and all 45 protected fixtures without skips. All 229
source/script/test hashes and the clean head matched before and after. The
independent verifier reconstructed both saved final-review inputs at 482,850
characters without dropping required material or creating decisions. PR #67's
hosted check passed in eight seconds and merged as
`6b4d08ba17a1ba6bc3ed10a5c2db7e4500fc7d83`.

Fresh session `64995fd381564d18adde7bb0c0124a75` completed eleven accepted
SCL cohorts and recorded one model-capability deferral explicitly. Verification
candidate `12cac44a685ff8b6e68bdc46cf7b3f875a12572a` passed the configured
suite (1,899 passed, 53 documented skips), packaging and all 45 protected fixtures
without skips. Its scoped positive clamp proof and actual negative solver case
plus protected CPython replay passed with matching source/candidate/evidence.

Verification reviewers split accept/defer because the current declared
`examples/source-proof/contract.json` was omitted. Read-only inspection confirmed
that its three logical fields exactly match the configured and FAS contracts,
and FAS maps the file to the active requirement. Existing proof selection only
required the implementation and control paths. The requirement's mapped and cited
source fits existing limits (approximately 160,634 characters of mandatory files,
279,306 for complete context with other sources explicitly omitted). The session
ended blocked before final review or publication. No approval is inferred from
the matching artifact or passing checks. General active-proof source selection,
independent reviews/completion and renewed real workflow remain pending.

The scoped selection correction now reserves active proof requirements' mapped
surfaces and local citations, with exact contract and identity checks. Both saved
v5 verification-review payloads reconstruct from a disposable backup at 476,509
characters, including the previously omitted declaration and mapped test, all
eleven cohorts and required proof/control material. No live records or decisions
were changed. Independent test development passed 29 new regressions; the old
code fails the missing-contract case. Existing source-case (68), context/citation
(21), and context-budget (6) tests also pass. The fast gate, 92 boundary/static
checks and whitespace check pass. Exact-commit independent review and completion
remain pending.

Independent security review found that explicit claim/requirement IDs bypassed
association validation when FAS was absent. The correction must require FAS for
identified proof contexts while preserving ID-less standalone behavior. No final
completion run was started before this review; the guard and regressions require
renewed exact-commit review first.

The absent-FAS guard and all nine identity/context combinations are now covered;
the expanded proof-source file passes 38 tests, and the isolated pre-guard code
fails the new case. ID-less standalone behavior remains covered. Both real v5
contexts still reconstruct unchanged at 476,509 characters with the required
declaration and mapped test. All 68 source-case compatibility tests, the fast
gate and 92 boundary/static checks pass after the guard. Exact-commit independent
review and final completion remain pending.

The final citation candidate `d22041b1b2811b6541177bea2e95baa61eca5694`
passed independent completion: native and Linux each 1,893 passed with 53
documented optional skips, both package gates, Ruff/Pyright, complete 291-path
inventory and all 45 protected fixtures without skips. Its clean head and 228
source/script/test hashes remained unchanged. Both independent reviews accepted;
PR #66's hosted check passed in nine seconds and merged as
`979363c961621039e4ef44aed0854fd76e3f7822`.

Fresh session `827511ea83cb40dca8d9bf2c16845109` completed eleven accepted
SCL cohorts. Reviewers used actual cited-revision ancestry and matching endpoint
blob evidence. Candidate `fe712d27a3946f500c38d8799ca48b2af0a97679`
passed the configured Linux suite (1,893 passed, 53 documented skips), packaging
and all 45 protected fixtures without skips. Its positive clamp check had a
satisfiable precondition and unsatisfiable violation query. The separate negative
control produced the actual integer witness `value=1, lower=-1, upper=0`;
protected CPython 3.12.13 reproduced true precondition, false postcondition and a
violation with matching candidate/source/blob/evidence identity. Both independent
verification reviewers accepted this scoped evidence.

Both final-review jobs exhausted three attempts before a reviewer could run:
their serialized context exceeded its fixed bound. Read-only reproduction from a
database backup measured 511,588 characters, including 28,308 characters of
omission descriptors that the reservation estimate missed. More than 200,000
characters were optional source; complete mandatory material fits. The session
ended blocked without final acceptance or publication. This is not workflow
readiness, and an exhausted job status is not an approval. A bounded optional
source eviction fix, independent completion and renewed actual workflow remain
pending.

The bounded-context correction retains every mandatory item and evicts only
explicitly tracked optional source contents with omission/frontier accounting.
Disposable reconstruction of both actual saved final-review payloads succeeds
at 482,850 characters, preserving the complete cumulative diff, all eleven
validated cohorts, full FAS/configuration, trusted verifier and positive/negative
source targets. No agents or review decisions were created in the old session.
Four new regressions pass, including the old capacity failure, true mandatory
overflow, paired final-review workflow and retained targeted-source progress
with an accurate omission frontier. The fast gate and 92 boundary/static checks
pass. Exact-revision independent review and completion remain pending.

Development candidate `abb248f4a9025e04c9ba2ceef25757ccb2732cac`
passed native and Linux suites (1,897 passed, 53 documented optional skips each),
both package checks, Ruff/Pyright, complete 292-path inventory and all 45 protected
fixtures without skips. The clean head and all 229 source/script/test hashes
remained unchanged. Code review accepted its evidence-preservation logic, but
independent security review identified repeated full-context serialization as an
availability issue. These are development gate results, not final acceptance.
The follow-up uses exact linear size accounting and incrementally maintained
target counts, with an actual final serialization and unchanged capacity guard.
Six focused tests now pass, including 400 tiny files with escaped Unicode in
both fitting and genuine-overflow contexts. They permit at most two complete
context serializations; an isolated prelinear implementation performed 402 and
failed the regression. Both saved real review inputs still reconstruct at
482,850 characters with all mandatory content. Existing source-context/citation
feedback passed 21 tests. The fast gate, 92 boundary/static checks and whitespace
check pass. Exact-commit independent review and final completion remain pending.

The source-case candidate `9ee0df3d1c49204c939555f462c43adf4d0a3a49`
passed independent completion: 1,878 native and Linux tests each with 53
documented optional skips, both package checks, Ruff/Pyright and 91 boundary
tests, and all 45 protected checker fixtures without skips. The real solver
case and protected CPython replay passed again; the clean head and all 227
source/script/test hashes remained unchanged. Two independent reviews accepted
the exact commit. PR #65's required hosted check passed in 12 seconds and merged
as `e08a7f7fd28e285770ba7007089373bf0efb0d11`.

A new explicit v2 policy and session
`d8b813cbf14e4602b54180e2c6a3ade2` preserved the previous authorities. Its
initial scoped clamp proof passed, and three specification-review cohorts
accepted. The fourth exhausted three reviewed drafts: its final pair split
accept/defer because the prompt did not establish continuity from an older
`process.py` citation. Independent Git inspection found the cited revision was
an ancestor and its source blob exactly matched the candidate. This is an
evidence-context gap, not an observed source mismatch. The session stopped
blocked after 19 jobs, before mandatory IVL checks, negative-control replay,
final reviews or publication. It does not establish workflow readiness.

The focused citation-continuity correction and renewed workflow evidence remain
pending. No blocked decision is converted into acceptance.

The correction supplies bounded path-specific ancestor and regular-file endpoint
mode/blob evidence for structured historical citations and the existing explicit
task-input revision format. It includes retained correction citations, associated
unchanged records during source refresh, and complete candidate source; equality
does not establish uninterrupted history or semantic correspondence. Independent
test development passed 15 new regressions, including actual scheduled correction
and reviewer jobs; selected positive/count regressions fail on the prior code.
Existing source-context/citation feedback passed 21 tests. The combined new
regression, boundary and configured static gate passed 107 tests; the fast gate
and whitespace check also pass. Exact-commit reviews, completion gates and a
renewed real workflow remain pending.

Frozen citation candidate `a006c93783fe57479fd60f3ed51b481b2b757fb9`
passed independent native and Linux completion (1,893 passed and 53 documented
optional skips each), both package gates, Ruff, Pyright with the explicit
candidate interpreter, complete 291-path inventory and all 45 protected fixtures
without skips. The exact clean head and 228 source/script/test hashes matched
before and after. An initial Pyright invocation selected the wrong interpreter
and reported missing dependencies; its diagnostic log remains alongside the
correct explicit-interpreter pass. Both independent reviews accepted the commit.

Before another real run, generation guidance now recommends the supported
structured revision citation for new local observations, preventing a fresh
generator from choosing an unrecognized prose convention. Existing provenance
remains unchanged. The final prompt candidate requires renewed exact-revision
completion and real workflow evidence before readiness.

The renewed real self-run from `cebafba` completed SCL, including substantive
deadline/exception-cleanup corrections accepted by fresh reviewer pairs. The
lockfile remained an explicit gap. Candidate
`ee6679563395e964377992c2f540de56ac9b1b44` passed complete inventory validation,
1,805 native tests with 53 documented skips, packaging and all 45 protected
fixtures without skips. Both clamp checks represented one conditional obligation,
with satisfiable preconditions and unsatisfiable violation queries.

Both independent IVL reviewers deferred because acceptance requirement
`2bcfa241-162e-5b32-8b37-79deae3acd37` also requires a faulty clamp candidate
to produce a replayable counterexample. The existing mutant test obtains an
actual solver counterexample but does not replay it; the replay unit test uses
a fabricated identity witness. The current evidence does not establish their
composition. Session `cd44c20820d94478a6a0614044afdd4a` ended blocked after
48 jobs, before final review/publication. This is not operational readiness.
A controller-owned, candidate-bound counterexample case with trusted isolated
replay and complete reviewer evidence is required. Prior fixture results remain
historical; no application-wide or translator-correctness theorem is claimed.
