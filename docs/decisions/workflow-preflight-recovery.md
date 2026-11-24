# Complete workflow preflight and recovery

This specification precedes implementation. Audited main is
`ee7643d9afb57d8cd9db04bf14d0dfd461f406d0`, with the same tree as independently
tested candidate `409bda948ac91418448c2c8fc552076d06be9a05`. Product authority remains
the versioned [vision](../vision.md) and [implementation plan](../implementation-plan.md).

## Consolidated validated findings

Independent static passes traced requirements, security and durable state across
the whole SCL, IVL, IIL, evidence, publication, merge and resume workflow. Each
failure below reproduced twice with a passing nearby control. A separate run
confirmed all four failures with four passing controls. Reproductions use real
Git, coordinator scheduling and SQLite, with simulated model/worker/GitHub adapters.

| Priority | Finding | Required behavior |
| --- | --- | --- |
| P1 | A crash after session pins but before all initial enqueues leaves resume without SCL or configured source checks. | Establish the complete initial work atomically or through durable idempotent replay before dispatch. |
| P2 | An ordinary retry can start before its second reviewer finishes and omit that reviewer's findings. | One authenticated complete cohort controls a bounded successor retaining all applicable findings. |
| P2 | Human feedback disappears from specification continuation payloads. | Preserve original optional input through deferral, review revision, capacity recovery, accepted-frontier continuation and resume. |
| P3 | Structured citation paths disappear from live and exported monitor projections. | Preserve safe repository-relative paths without exposing host paths or weakening rendering safety. |

The first two findings concern missed work and lost context, not a demonstrated
unsafe merge or false proof. Citation reproduction establishes lost path text,
not every hyperlink behavior. These observations are not an exhaustive bug claim.

## Invariants and implementation ownership

- Preserve policy/tool/revision pinning, records, job identities, leases, source
  barriers, immutable source inputs, proof predicates and unknown-remote no-replay
  rules. Partial bootstrap must not silently omit work or accept changed input.
- Preserve required independent participants, non-authorship and fresh evidence.
  Revision/defer/failure/exhaustion retain their meanings. Keep the dedicated
  source-correction path and cumulative attempt/round/frontier/cycle bounds.
- Distinguish original human input from later review findings and retain both for
  the proper continuation. Optional input never gates transitions or authorizes
  unrelated edits. Oversized context stays explicit; no truncation or limit rise.
- Keep the coordinator cap at 240000 bytes. Any extracted execution module must
  enter classification, fingerprints and complete inventory. New inventory rows
  establish neither correspondence nor proof.
- One implementation owner handles coordinator/state/bootstrap/feedback files;
  a separate owner handles citation projection. Test ownership is independent.
  Authors never review or verify their own changes.
- Titanic application, tests, dependencies, CSV, ignored database, privacy and
  coverage gates remain untouched. No paid upgrade, visibility change or heavier
  GitHub CI is authorized.

## Validation before actual readiness

Regress crash boundaries and Store reopen; exact initial work and pins; both
review completion orders and unresolved peers; optional feedback across all
continuations; stale/bounded source corrections; and safe/unsafe citation paths.
Exercise positive/negative workflows, stale heads/evidence, exhausted attempts,
large contexts, concurrent frontiers and correction cycles. Record mock boundaries.
Repeat independent review after material fixes. Freeze the stable accepted revision
before required native, Linux, packaging and zero-skip protected checks. Serialize
expensive suites; repeat successful full gates only after a change or failure.
Hosted CI remains syntax/whitespace only.

## Real integration rehearsal before the self-run

Use a new disposable private GitHub repository and authoritative local state,
never Titanic. Run meaningful passing and failing gates in the existing isolated
Lima worker. Exercise actual SSH receive-pack, both explicit expected-old ref
commands, original PR identity and GitHub merged-state recognition. Every actual
model loop role uses GPT-6.1 Terra with high reasoning.

Use a separate disposable operation to inject a local lost acknowledgement after
an actual remote effect where feasible. Preserve the original intent and state,
reopen/reconcile, and establish no repeated dispatch plus exact positive remote
observation. Distinguish client fault injection from a real GitHub outage. Scripted
reviews or fabricated service/authority responses cannot establish actual review
or service behavior. State the limits of any simulated harness.

These real-service behaviors remain unverified until exercised. A fresh actual
Mentat self-run still requires current checks/proofs, independent source inputs
and reviews, resolved corrections and observed guarded publication/merge before
Titanic onboarding. Tests and review agreement are not whole-application proof.

## Excluded leads and remaining boundaries

Hostile preexisting local Git configuration was not established as an in-scope
exploit: model writes cannot edit `.git`, checks run in the isolated worker, and
the extra private-policy configuration rejection concerns transport. Optional
filesystem-monitor neutralization is hardening, not a validated readiness defect.
Worker bundles intentionally include reachable tracked history, visible to candidate
code. No host-credential exposure or promise of HEAD-only visibility was established.
Local authority, SSH/account trust, supported proof semantics and late GitHub
metadata remain explicit limits. Continue to Titanic at agreed actual readiness;
unrelated cleanup must not delay it.
