# Autonomous Mentat implementation

Status: implementation in progress. Baseline: `f4486141fd257cbb6c205552606e6bee572081f3`.

This is the ready implementation specification approved on 2026-11-23. Product authority is the versioned [vision](vision.md), derived from the [Notion page](https://app.notion.com/p/Hypertrial-Mentat-419fbf156f898000ae6cf59282e35137). Do not begin Titanic onboarding until the Mentat readiness checks pass.

## Intended behavior and invariants

- Three session-scoped loops maintain CAS, FAS, verification artifacts, and established application fixes. Each has separate generator and discriminator sessions; all use GPT-6.1 Terra at high effort.
- CAS covers product, functional, nonfunctional, architecture, data/API, and acceptance. Observed, intended, inferred, and uncertain claims remain distinct. Unsupported/unattempted/stale obligations are retained.
- The application keeps its runtime independent of the verification sidecar. A proof about a model is never silently presented as proof of source behavior.
- A local durable coordinator bounds work to two hours, three concurrent agents, and three unsuccessful rounds per unchanged item. It checkpoints on cancellation or stability and resumes without resetting limits or duplicating work.
- Local tests, browser coverage, source proofs, Lean checks, packaging, and adversarial validation establish the evidence. GitHub runs only inexpensive syntax/whitespace checks.
- Existing SSH and GitHub CLI authentication are used; local results and independent reviews authorize qualifying PR merges only in an active session. The local process and GitHub account are trusted. No new signing infrastructure is introduced.
- Sensitive verification, acceptance, CI, and guarantee changes require two independent reviews. Head/base changes invalidate dependent results and reviews. Missing access or uninspectable branch enforcement prevents merging.
- Legacy CLI contracts and saved evidence remain readable with their original semantics.

## Delivery order

1. Versioned product baseline and interfaces.
2. Durable store and session coordinator.
3. Dedicated local Linux worker provisioning and diagnostics.
4. Specification convergence with persistent identities and lineage.
5. Granular implementation verification and a narrow audited Python-to-Z3 backend.
6. Independently reviewed implementation improvements and guarded GitHub operations.
7. Secure localhost monitoring and optional asynchronous feedback.
8. Compatibility, local completion checks, lean CI, fixture workflows, and Mentat self-run.
9. Only after readiness: Titanic CAS/FAS/sidecar, existing coverage gate, first scoped direct source proof, and full-loop demonstration.

## Regression surface and acceptance

Preserve legacy records, fingerprint boundaries, contracts, exit codes, and independent approval semantics. Verify job leases/fencing, crash recovery, retries across resumes, graph invalidation, malformed state, independent authorship, source identity, non-vacuity, unsupported syntax, counterexample replay, missing tools, skipped required tests, UI authentication/origin/path handling, and stale merge protection. Exercise full loops on disposable Git fixtures before a real self-run.

Completion commands are `scripts/verify-fast` and `scripts/verify`, plus real worker checks in the dedicated Linux environment and browser verification of the UI. Record unavailable prerequisites explicitly; they do not count as passing readiness.

## Baseline evidence

Before source changes: `scripts/verify` completed with 1,479 passed and 53 skipped in 105.85 seconds. Skips concern unavailable Lean, dedicated Linux checker, and upstream mirrors; this is not the full no-gap readiness gate. Python 3.12.16 and locked development dependencies were installed into the ignored local environment. GitHub CLI and Lima were not available on the initial sandbox PATH.

## Decisions

- 2026-11-23: verification generation must retain a complete candidate FAS inventory after adding artifacts. After enforcing the agent's verification-only path scope, validate the existing parent specification, append only genuinely new staged paths as controller-owned `unexamined` surfaces with empty requirement links, and validate the complete candidate before commit. Preserve every existing requirement, claim and surface record, proof status and lineage; agent FAS edits remain prohibited. Fresh verification/final reviews see the full resulting diff and checks bind the new candidate head. Before any GitHub publication/reconciliation, validate the exact clean candidate's complete inventory again. Regressions must cover artifact addition, both reviewers, fresh-session validation, preexisting omission rejection, immutable semantic digests and publication rejection before GitHub side effects.

- 2026-11-23: specification correction generators must receive the same complete source needed to assess the draft they are correcting. Compare the unreviewed input FAS with its recorded cumulative review base and reserve mapped/cited/direct-contract sources before optional context, retaining accepted-base and observation provenance. Keep strict mapping validation and all context bounds. Genuine capacity must use the existing bounded split/revise path rather than repeat an impossible correction input; preserve established records, frontier and retry counters. Regressions must exercise reviewer feedback about a source file that default correction context would omit, then a validated correction and two fresh independent reviews.

- 2026-11-23: keep every writable worker cache outside the candidate source checkout, with its own per-job confined bind and unchanged cleanup, network/capability isolation and file-size limits. Source and wheel artifacts must exclude worker caches even when the target has no corresponding ignore rule. Installed-artifact validation must ignore inherited Python source-path overrides so it imports the installed wheel or sdist rather than the checkout, including current-directory module shadowing; keep the normal worker source-import environment intact. Provisioning must use public uv resolution to seed the complete existing pinned Hatchling dependency closure before freezing the trusted cache/profile; do not construct private cache metadata, choose unpinned versions or rely on installed packages satisfying isolated builds. Test missing-metadata reproduction, clean-cache seeding and fresh unprivileged offline source/wheel builds on both supported interpreters, plus real candidate packaging. Keep script and packaged provisioner mirror identical. Profile checksum changes require explicit trusted reprovisioning and policy initialization, with no silent authority repin.

- 2026-11-23: preflight specification-review context before creating reviewer jobs. A genuine size/capacity failure is a controller constraint, never an independent review decision. Record the rejected candidate and required source paths, then return bounded split/revise feedback to a generator starting from an explicitly tracked accepted code/specification base that retains every applicable approved application fix and CAS checkpoint. A broader cumulative diff base alone does not authorize rollback; ambiguous older state fails closed. Preserve established IDs and intent, repair/source metadata, prior authors, original batch bounds and unreviewed path frontier; retain rejected drafts in Git/history. Count recovery against the existing three-round limit across replay/resume. Only fitting proposals receive fresh independent reviews. Missing/unsafe citations and malformed state remain failures rather than capacity recovery. Do not relax source selection or size bounds to make a proposal fit. Regressions must cover capacity followed by a smaller accepted proposal, remaining paths revisited, exhaustion, deterministic replay and absence of forged approvals.

- 2026-11-23: specification reviewers must receive the complete source needed to assess newly asserted mappings before unrelated optional context. Derive required paths from the candidate FAS compared with the cumulative review baseline, retaining them through corrections; include relevant changed requirement or claim associations and direct source contracts. Normalize changed-record repository citations from explicit path objects or path strings, including supported line anchors; missing, unsafe or malformed local citations produce an explicit failure. Label HTTP(S) references as uninspected external metadata. Preserve unchanged historical records. Reserve complete FAS/CAS and the full proposal diff as before. If the required material exceeds a supported bound, fail explicitly with split/revise guidance; do not approve an uninspectable mapping. Regressions must reproduce a source file present for generation but displaced from both reviews by diff/context budgeting, and verify full required source, candidate identity and mandatory context in both reviewers' actual prompts.

- 2026-11-23: distinguish session source/base, each job's actual source context, the immutable revision observed by a generator, the proposal under review and its cumulative review base. Versioned observations cite the immutable input revision or file blob; they must not embed their own resulting commit hash. Reviewers assess cited ancestor provenance against relevant unchanged content and scope, and flag real drift. Preserve exact candidate evidence freshness and full-diff review. Regressions must exercise generation followed by both reviewers at a different candidate head.

- 2026-11-23: provide optional bounded data-only `patch_fas` changes at `.mentat/fas.json` for specification generators. Large-specification agents repeatedly deferred supported observations because whole-file replacement was impractical. Bind each patch to the exact prior file SHA-256 and each update to the existing record digest; insertions require absent stable IDs or surface paths. Permit only complete record upserts in requirements, claims and surfaces; forbid deletions, arbitrary paths, duplicate edits, executable operations and stale bases. Retain no-follow confined writes, preflight all changes, bound input/result sizes, and validate the complete resulting specification and immediate-parent lineage before independent full-diff review. Preserve existing write/delete responses. Test large-FAS compact edits, stale/conflicting/malformed data, path/symlink boundaries, limits, atomic preflight and actual review integration.

- 2026-11-23: every specification candidate inventories its complete staged Git tree. The controller may append deterministic `unexamined` surfaces with no requirement links only for newly introduced paths absent from the immediate parent's inventory. Missing existing surfaces, malformed records and duplicate rows remain errors. These metadata changes are committed and independently reviewed; they establish no correspondence or proof. Review retries retain the original cumulative review base, while record lineage is validated against the immediate parent. Regressions must cover new sidecars, omitted existing paths, rejected drafts, CAS-only corrections and allowed deletions. Staging must handle already-staged deletions without dropping allowed-path checks.

- 2026-11-23: the user removed external work-tracking requirements from this repository. Repository specifications and evidence own implementation intent and handoffs.
- 2026-11-23: the user explicitly simplified Git integration to SSH and existing `gh` authentication, local records and independent reviews. No new signer, signed receipt, or bootstrap workflow is part of this implementation.
