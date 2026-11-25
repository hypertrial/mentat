# Session-scoped application assurance

The development checkout adds a durable SCL/IVL/IIL workflow beside the legacy approved-contract CLI. The [product baseline](vision.md) and [implementation acceptance gate](implementation-plan.md) describe the intended behavior and remaining readiness work. Historical contract approvals and reports retain their original meaning.

## Initialize a repository

From a development install (`uv sync --locked --extra polaris`), run:

```sh
uv run --locked mentat init --repo /absolute/path/to/application
```

Review and commit `.mentat/workspace.json`. It selects GPT-6.1 Terra at high effort, a two-hour session, at most three concurrent jobs, the base branch, and the required local completion commands. Do not substitute a trivial command for the application's completion gate. A missing command or unavailable worker is a gap, not a pass.

The application does not import Mentat. OpenSpec CAS documents, `.mentat/fas.json`, and isolated verification artifacts are versioned beside application source. Local controller state and raw logs live outside the repository. Use global `--state /absolute/private/directory` to choose a writable location.

## Inspect prerequisites

```sh
uv run --locked mentat worker doctor --repo /absolute/path/to/application
uv run --locked mentat worker provision
```

The pinned GPT-6.1 Terra model requires Codex CLI 0.159.0 or later.
`worker doctor` reports the installed CLI version. A live model call is needed
to confirm access through the current sign-in.
See the [Codex model instructions](https://learn.chatgpt.com/docs/models#choose-a-model-2).

Install Lima on the controller before provisioning. The managed Ubuntu ARM64 VM has no host directory mounts or port forwarding. The pinned image digest is published in [Canonical's checksums](https://cloud-images.ubuntu.com/releases/noble/release-20261017/SHA256SUMS). Generated verification and application execution belongs inside its bounded guest sandbox. The Lean checker retains its separately pinned toolchain and axiom policies.

Git operations use the existing SSH remote. PR metadata and protected merges use existing `gh` authentication. Missing CLI authentication, unreadable branch rules in protected mode, stale checks, or unavailable worker tools prevent the corresponding operation; errors never select another enforcement policy or host execution. Provision the application's development dependencies in the trusted worker environment before requiring its checks.

Writable build caches stay outside the application checkout. Provisioning seeds
the pinned isolated-build dependency closure before freezing the trusted cache.
Installed-artifact checks ignore inherited Python import overrides so that they
validate the installed artifact. A changed worker profile requires explicit trusted
initialization; an old session is not silently repinned or presented as resumed.

## Run and resume

```sh
uv run --locked mentat run --repo /absolute/path/to/application
uv run --locked mentat status --repo /absolute/path/to/application --json
uv run --locked mentat resume --repo /absolute/path/to/application
```

Run from a clean committed tree. `run` and `resume` are headless by default and
produce no rendered report. The independent AI Cockpit owns all presentation.
For a committed FAS-only change to one mapped tracked surface, use
`mentat run --repo PATH --spec-surface TRACKED_PATH` for a read-only
qualification. Resume retains the selected scope. All configured checks and
independent reviews still apply, and unrelated inventory gaps remain visible.
For an optional local adapter, create an operator-owned mode-0700 directory outside
the repository and pass `--integration-file /private/path/connection.json`.
The new file is mode 0600 and contains the loopback URL and bearer capability;
never put the capability in a URL or log. Closing a client does not stop work.
`mentat integration serve --repo PATH --session ID --connection-file PRIVATE_FILE`
inspects saved work and can queue optional feedback; it cannot stop another
controller. See [integration v1](integration-v1.md) for exact schema, discovery,
paging/cursors, freshness, lifecycle, authorization, bounds and migration.
The former `ui` and `--ui/--no-ui` surfaces are removed. Optional feedback remains
untrusted task data and never authorizes policy, proof or publication changes.

SCL maintains all six CAS areas and records observed, intended, inferred, and uncertain requirements. IVL separately records formal obligations, models, implementation connections, executable checks, and unsupported scope. IIL handles evidenced violations of established requirements. Each generator is independently reviewed; consequential checker, CI, acceptance, and guarantee changes require two independent reviews. Application rewrites solely to ease verification are excluded.

Specification generators can propose small FAS record upserts with `patch_fas`
instead of reconstructing the whole file. This data-only operation binds to the
exact previous file SHA-256 and existing record digests; inserts require an absent
stable identifier or surface path. It cannot delete records or edit another file.
The controller validates the complete resulting FAS, lineage and candidate inventory,
then gives independent reviewers the full committed diff. Existing full-file
write/delete proposals remain supported.

Source observations cite the immutable revision or file blob the generator
inspected. The proposal produced from those observations has a separate commit
identity. Reviewers check the observed ancestor and relevant changed content;
a versioned specification does not need to embed its own resulting commit hash.
Execution results and approvals still bind to the exact candidate head and base.

When a run starts from a feature commit ahead of the remote base, its initial
specification review covers that cumulative difference, including pre-authored
CAS/FAS. Generation and capacity recovery keep the committed feature or approved
repair as their actual input. Later batches use incremental bases only after a
validated ancestral specification cohort; a feature commit alone establishes no
review authority. Oversized or uninspectable initial content remains an explicit
bounded gap.

Later reviews carry bounded controller-validated evidence of earlier source
correspondence reviews. It binds the reviewed records and source blobs to actual
independent decisions; unchanged baseline records retain their unreviewed status.
This process evidence accompanies the complete final diff and current proof
source, contracts, frontend and check results. Changed content requires fresh review.

Before final aggregate review, the controller partitions required current source
into bounded correspondence reviews. Coverage derives from the baseline and
candidate FAS and the actual Git changes, including removed mappings, renames and
file modes. Each batch needs two independent non-author reviewers and receives
the complete source and specification records for its declared scope. An
indivisible source or relationship that exceeds the existing limits remains a
gap. Source review establishes an inspected correspondence contribution; it
does not prove the application mathematically.

The controller privately records each source reviewer's exact supplied prompt
and a manifest bound to the job attempt, source, records, candidate, checks and
policy. Earlier reviews without these manifests remain historical. Final
reviewers receive validated current coverage and complete bounded findings,
alongside the full cumulative diff, CAS/FAS and current proof and check evidence.
They assess consistency and outstanding gaps without claiming to have personally
reread every source in one prompt. Coverage is checked again before publication;
missing, stale or mismatched inputs cannot authorize it. Raw prompts stay in
private local state.

An IIL repair queued before SCL accepts a newer specification can start from that
accepted checkpoint while retaining the original witness, reproduction and
adjudicator identities. Reuse currently requires byte-unchanged application and
CAS context and unchanged linked obligations; changed or incomparable context
remains explicitly inconclusive and needs fresh assessment. Correction retries
retain the already repaired head. Protected checks and independent reviews bind
the resulting candidate, and later accepted work is checked again before publishing.

Review context prioritizes the complete sources needed to assess changed FAS
mappings and source contracts, alongside mandatory specifications and the full
proposal diff. A mapping is not accepted when its required source cannot be
inspected within the supported context bounds; the proposal must be split or revised.
Changed-record repository citations may use path objects or path strings with
supported line anchors. Missing, unsafe or malformed local citations fail explicitly;
HTTP(S) references are identified as uninspected external metadata.

New verification artifacts receive explicit unexamined inventory entries from the
controller. This does not establish a requirement link or proof. The complete
candidate inventory is validated again before publication.

Specification corrections reserve the sources needed for the draft under review
as well. A correction is not asked to edit a mapped observation without receiving
the corresponding source; genuine capacity uses the same bounded recovery path.

When a specification proposal exceeds review capacity, the controller records
the constraint and gives the generator bounded split/revise feedback from the last
accepted code and specification base. Recovery must retain approved application fixes;
ambiguous older state does not authorize a rollback. Rejected drafts remain in history and unreviewed
source paths remain pending. This consumes the existing round budget and creates
no reviewer approval; fitting proposals still require fresh independent reviews.

When work is stable, deferred, cancelled, or out of budget, the controller checkpoints. Resumption preserves attempt counts and fences old workers. Changed source or policy makes prior evidence stale; it must not be displayed as current assurance. Review material fixes again and rerun the required local gate at the final candidate revision.

## Direct Python source evidence

Configured source checks can declare `counterexample_cases` as a bounded list of
`{path, function}` objects. Each committed negative-control fixture uses the
positive check's inputs, precondition and postcondition. The controller requires
an actual solver counterexample, binds its exact source and typed witness, and
replays it with the trusted harness in the isolated worker. Case results remain
separate from positive claim proofs and application-defect handling. Generators
see the pending plan; acceptance reviewers receive the actual current results.

New local policies use version 2 and pin the ordered configured source checks,
including their case controls and requirement links. Existing version 1 policies
continue for configurations without cases. To enable cases, initialize explicitly
with a new `--state` directory; the controller preserves older policies and
session records. Add and commit the reviewed descriptors and fixtures before
running the new session. Recognized Mentat repositories require the clamp proof
and its faulty-clamp control at new policy initialization.

The first backend parses committed source without importing or executing it. It supports a small typed pure subset: exact integer/Boolean inputs, assignments, arithmetic addition/subtraction, branching, comparisons, returns, and restricted literal string outputs. Calls, heap effects, loops, dynamic dispatch, floating point, and other unsupported constructs produce incomplete evidence.

```sh
REV=$(git rev-parse HEAD)
uv run --locked mentat source-verify --repo . "$REV" \
  examples/source-proof/clamp.py clamp --spec examples/source-proof/contract.json
```

Exit 0 means the declared obligation was proved within the reported assumptions, 1 is a counterexample, and 2 is incomplete. A satisfiable precondition is required before checking that no violating execution exists. The audited frontend, selected Python semantics, solver, and unmodified function binding are trusted. Input assumptions and caller reachability are separate obligations. This is not a proof of an entire application or of CPython.

## Local acceptance and publishing

Run `scripts/verify` locally, with additional application, worker, proof, browser, and packaging commands as required by the configured gate. GitHub's small syntax/whitespace job is not evidence that those local checks ran.

The controller records local command results and independent reviews against the exact PR head and base, publishes a compact PR summary through `gh`, and merges only while its session remains active. The default protected mode checks enforced branch protection and required checks again before a match-head squash merge. If strict freshness cannot be established, the PR remains unmerged. Raw logs and large artifacts stay local; review them before sharing.

Protected auto-merge requires the target's `main` rules to apply to repository
administrators. With administrator bypass enabled, Mentat reports
`branch protection is not strict or permits bypass` and leaves the PR unmerged.
Enable **Do not allow bypassing the above settings** in the target's classic
branch protection before a protected merge.

The controller, its local records, and the authenticated GitHub account are trusted. There is no separate signer, signed receipt, or independent remote attestation of a local run. Legacy evidence receipts continue to serve their existing integrity role.

## Explicit private repository enforcement

A private GitHub repository on a free organization can use a separately pinned controller policy without paid upgrades or public visibility. This policy trusts the local controller, its authenticated checks and independent reviews, and the GitHub account; GitHub does not enforce branch-protection rules for this mode. The protected mode remains the default, and a 403 never enables this mode automatically.

Set the versioned workspace `auto_merge` to true before initializing a fresh state, then explicitly select the policy:

```sh
mentat --state /absolute/new/private-state init --repo /absolute/repo --merge-policy controller-private
```

Initialization pins the private repository and owner IDs/node IDs, name and visibility and the merge policy alongside the existing minimum checks in local policy version 3. The workspace schema stays unchanged. An existing state cannot switch policies; use a new state and session. Review and commit the workspace before running the controller.

After all current checks and reviews qualify, the controller validates its private repository and PR identities and prepares a merge commit whose tree exactly matches the tested candidate and whose ordered parents are the verified base and head. One SSH receive-pack transaction explicitly submits both expected-old ref updates, fast-forwarding main and the controller-owned PR branch to that derived commit. The server must advertise atomic and report-status support; both exact old values are checked in the atomic server transaction even if a concurrent update reaches the intended target. Missing capabilities or a stale head or base prevents the transaction. The controller rechecks session and repository/PR metadata before dispatch; those metadata observations are outside the atomic ref transaction. No history rewrite, branch deletion, admin bypass or paid API is used.

The PR head becomes the authorized derived commit. Success requires GitHub to confirm that exact PR and mode-specific merge shape. An uncertain push response or delayed GitHub state remains incomplete and uses the existing durable reconciliation path; the controller never repeats a write merely because its response was lost. Full local controls and independent review are required before activating this feature.

Private merge intents are durably journaled before dispatch, including the exact derived OID and raw commit bytes. The controller revalidates its publication checks, source barrier/epoch, local policy and tools at that boundary. Recovery accepts only that recorded intent, including when a push acknowledgement is lost; an unrelated same-shape commit is insufficient. Repository-local Git redirection/includes and implicit transport commands are rejected; inherited Git overrides are neutralized while trusted SSH authentication remains available.

The two-ref transaction does not lock GitHub PR state, visibility or permissions. Those are rechecked observations. A late closure or access change can leave applied refs with an unconfirmed PR result; the controller preserves the intent and checkpoints rather than claiming success or rolling back history. GitHub may report the original approved or the derived PR head; the confirmed merge OID must still equal the exact intended two-parent commit. This is Mentat-operated delivery, not GitHub's paid native auto-merge feature.

The intent pins the original PR number and immutable PR ID/node ID. A separate durable dispatch marker is written exactly once immediately before sending both update commands in the same bounded SSH process that supplied the capability advertisement. Pack construction and capability negotiation happen before that marker. Protocol status for both refs is required; process exit code zero alone does not establish success. Prepared and undispatched work may proceed through fresh guards. Once dispatched, unchanged/open observations remain unknown and never permit another write or a replacement PR/intent. An uncertain network outcome may therefore remain an honest checkpoint until the exact intended result is positively observed. This first mode implements strict no-replay, not bounded idempotent transport retries.
