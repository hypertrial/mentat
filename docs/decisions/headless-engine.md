# Headless assurance engine and independent AI Cockpit

Accepted product decision, 2026-11-24. This explicitly revises the earlier
Notion-derived placement of monitoring presentation inside Mentat. The separate
AI Cockpit product owns all frontend and presentation, and may later supervise
other engines. This task neither creates nor edits that repository.

Mentat remains a single-user, local-first assurance engine. It owns SCL, IVL,
IIL, durable state, optional feedback, controlled cancellation, proof and check
execution, evidence qualification, policy and guarded publication/merge. It must
operate without a cockpit, web browser or listening socket. A client cannot
promote tests to proof, approve its own changes, weaken policy, reset limits or
authorize publication. Existing independent review, isolation, source barriers,
exact identity checks and unknown-remote no-replay rules remain mandatory.

## Inventory before implementation

At frozen b91a7b640e057605daafa068edeeb501cfffcc5d, presentation lives in
`autonomy_ui.py` (inline HTML/CSS/JS, live dashboard, offline session report),
`evidence_html.py` (saved-run and comparison HTML) and the Markdown report/PR
formatters in `evidence.py` and `evidence_report.py`. Launch surfaces include
`ui`, default UI startup in `run`/`resume`, `--ui/--no-ui`, `evidence render`,
`evidence compare --out` and `evidence pr-summary`. Even `run --no-ui` writes an
HTML report. Wheels package these Python modules; there is no separate static
asset package.

Reusable engine capabilities are mixed with presentation: allowlisted/redacted
public records and citation/source identities in `autonomy_ui`; live
Git/policy/tool freshness in `autonomy_cli._project`; scoped assurance in
`project_snapshot`; saved-run acceptance receipt validation in `evidence_html`;
and claim/check grouping/review metadata in `evidence_report`. Preserve these
capabilities in headless modules rather than copying or retaining renderers.

The old server has no discoverable integration version; its events endpoint
silently takes the last 1,000 events from an unbounded full snapshot. Raw
`status --json` contains more private data than the HTTP projection. Existing
snapshot collection, strings and arrays are not a bounded integration contract.
Existing stop is process-local; feedback is journaled and later consumed.

Consumers found locally are CLI/UI/evidence/citation/freshness/qualification,
saved-record parity and release-artifact tests, `.mentat/selfcheck.py`, release
and record-generation scripts, README, autonomy/vision/contracts/example docs,
the legacy contract/Claims Spec, and CAS/FAS inventory. No neighboring repository
consumer was found; external clients may exist. This is a breaking development
interface migration, not a claim that no one uses the old surfaces.

## Versioned replacement and compatibility

Implement the local JSON v1 contract in `docs/integration-v1.md` before removing
the old interfaces. Preserve qualified engine derivations and safe source links.
`run` and `resume` default to headless and optionally start the integration server
using an explicitly supplied private rendezvous file. A separately launched
`integration serve` inspects one saved session and can queue optional feedback;
it cannot stop or otherwise impersonate a live controller. Machine CLI access
remains available. No compatibility HTML copies, renderer aliases or hidden UI
assets remain.

New verification bundles contain machine evidence, not Markdown/HTML reports.
Use an explicit versioned bundle-format distinction; validate legacy and new
formats separately and reject unknown formats. Historical reports, manifests,
receipts and fixture bytes remain immutable and readable with their original
integrity requirements. Do not infer the new format from a missing old report.
Comparison/export return actual machine references, never nonexistent report
paths. Remove the human PR-summary command; keep engine-required GitHub
publication and its exact-head assurance metadata. OpenSpec, FAS, generated
Claims Spec and committed specifications are canonical product data, not a
frontend, and remain supported.

Update the monitoring requirement in place under its persistent UUID, recording
the explicit product decision and earlier statement in revision history. Update
legacy contract text/checks and regenerate Claims Spec coherently; this is an
authorized architecture change, not weakening requirements to hide a defect.
New inventory rows remain unexamined. Removed files are removed from complete
inventory; preserve other requirements, claims, proof states and provenance.

## Ownership, sequencing and acceptance

Finish and collect the in-flight native gate on b91 without modifying it. Keep
that historical result accurate; defer its superseded follow-on gates. Work on a
new branch. Record this decision and the contract before substantial code.
Use nonoverlapping owners for integration/state, projections/transport, and
CLI/evidence migration; independent test development, architecture/security/data
review and final verification use GPT-6.1 Terra at high reasoning. Authors never
review or verify their own changes.

Acceptance requires actual local HTTP interactions covering authentication and
origin/Host/framing rejection, bounds and slow clients, consistent paged snapshots,
more than 1,000 updates/reconnect, expired/foreign/ahead/restarted cursors,
unavailable/stopped/restarted controller states, stale evidence and qualified
proof versus tests, durable duplicate/conflicting feedback, stop races and
restart semantics, and operation without any UI. Keep sensitive raw paths,
artifacts, prompts, feedback, credentials and policy authority out of the public
contract. Test original legacy evidence integrity as well as new machine bundles.
No renderer/module/HTML/CSS/JS/old launch surface may survive as executable UI.

Repeat independent review after material fixes. Freeze the exact accepted
revision before full native/Linux/package/protected-worker gates; serialize
expensive work and keep hosted CI syntax/whitespace only. The integration and
verifier identity changes require fresh approvals where applicable and a new
actual qualifying Mentat self-run. Historical b91 tests, browser smoke and old
self-runs cannot establish readiness for the new engine.

Before that full self-run, exercise a small real disposable private GitHub/SSH/
VM workflow and feasible lost-acknowledgement recovery, clearly identifying
injected faults versus real service behavior. Titanic starts only after the
agreed actual readiness gate; preserve its application, data, coverage, privacy,
free GitHub plan and automatic-merge safeguards. Do not delay it for unrelated
cleanup. Passing tests/reviews establish neither absence of all defects nor
whole-application formal proof.
