# Private repository merges without paid GitHub features

Status: implementation proposal authorized for preparation and independent review on 2026-11-24. Not activated or accepted yet.

## User decision and current evidence

The user requires Titanic to stay private in the free `hypertrial` organization and incur no paid upgrades. The user explicitly approved preparation of an isolated, repo-specific controller-enforced merge mode and coordination with the main implementation chat. GitHub API inspection on 2026-11-24 reported organization plan `free`, Titanic visibility `private`, and repository admin access for the authenticated account. Both branch protection and ruleset inspection returned HTTP 403 with GitHub's feature-entitlement message. No billing, repository visibility, remote settings, application files or data have been changed.

GitHub documents private branch protection as a paid-plan feature: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches . The controller mode does not claim that GitHub enforces its local requirements or that an independent remote attestation exists.

## Intended behavior

The existing protected-GitHub merge mode remains the default. API errors never select an alternative mode. A separate trusted `mentat init --merge-policy controller-private` operation may pin a private repository identity and this policy in a new local state directory. Existing pinned policies cannot be replaced or silently upgraded; resuming uses exactly the pinned policy. The versioned workspace retains its existing configuration schema and separately authorizes `auto_merge`.

Controller-private mode retains all mandatory local checks, worker profile and source evidence, independent reviews, source-finalization barriers and epochs, resource/attempt limits, active-session authorization, and current head/base/diff bindings. The configured local controller and authenticated GitHub account remain trusted. This mode does not provide GitHub server-enforced restrictions on other account holders.

Only a same-repository PR targeting `main` of the pinned private repository may qualify. Inspect the actual repository visibility, PR head/base/ref and complete changed-file list. Missing access, ambiguous identities, unsupported states or stale evidence prevent the write. Recheck identity and active authorization immediately before the remote update.

Prepare a normal merge commit whose ordered parents are exactly the verified base and verified PR head, and whose tree is exactly the tested candidate tree. Verify the base is an ancestor of the head. Atomically fast-forward both `refs/heads/main` and the controller-owned PR branch to that descendant commit, using an explicit expected-old Git lease on each ref and `--atomic`. Both refs must change so the server transaction fences both original base and original PR head. Never rewrite history, update another ref, use admin bypass, delete a branch during this operation, or call paid-feature APIs. The derived PR head is this validated merge commit; its tree is identical to the reviewed candidate and its second parent is the original reviewed head. A moved remote base must reject the update even if it moves after the final preflight.

Retain the controller's existing durable remote-effect marker before any write. An uncertain push or unconfirmed GitHub result remains unknown and is reconciled before retry. Controller-private reconciliation accepts only the explicit two-parent shape, derived PR head and exact tested tree for that policy; protected mode continues to accept its existing pinned one-parent squash. A changed PR head cannot be reported as merged. Polling, if any, must be bounded; an unresolved outcome checkpoints.

## Acceptance and regression surfaces

- Explicit opt-in is pinned to one private repository; public targets, mismatched repositories, malformed policies, unauthorized automatic merging, silent downgrade and mutation of existing policy are rejected.
- Existing protected mode behavior and one-parent reconciliation stay compatible.
- All candidate-bound checks and independent reviews remain mandatory; missing, stale, duplicate or author-owned evidence blocks a write.
- Real disposable Git remotes demonstrate a successful exact-tree fast-forward merge, compare-and-swap rejection after either a base or head race, all-or-none ref updates, no history loss and only the two intended refs changing.
- Inactive/cancelled sessions and head/base/visibility changes prevent dispatch. An uncertain result never becomes a pass or unreviewed retry.
- Reconciliation validates the correct mode-specific parent order, tree, head, base and PR identity, including interruption after remote success.
- All work is isolated from the main chat's frozen checkout and worker. No paid upgrade, repository publication, Titanic activation or remote merge occurs during preparation.

## Verification and handoff

Run lightweight focused tests and static checks locally first. Independent adversarial review, full native/Linux/package/protected gates and exact-head acceptance are required before adoption. The main chat owns that integration and activation. Never change the controller identity in a running assurance session. The protected Mentat self-run remains protected; Titanic opt-in follows accepted integration and readiness.

## Hardened operation identity and transport

Pin the immutable repository ID/node ID and owner ID/node ID alongside its exact name and private visibility. Recheck those fields, write access and the same-repository PR at mutation and recovery boundaries. Reject repository-local URL rewrites, includes, SSH command/transport overrides and implicit push settings. The runtime Git adapter clears inherited Git environment variables and disables system/global Git configuration; explicit canonical SSH URLs are used for private reads and writes. Trusted user SSH configuration and its authentication agent remain part of the disclosed local trust boundary. No independent transport or account attestation is claimed.

Before any push, persist the exact original head/base/tree, private policy identity, publication job, authority/tool/evidence/review bindings, derived merge OID and bounded raw commit bytes in the existing SQLite journal under its immediate transaction, source barrier and epoch checks. Revalidate the full publication guard and active authorization again at the write boundary. The persisted intent is immutable; a same-tree/same-parent commit with a different OID is never adopted. The bounded raw object permits exact recovery if an unreferenced local prepared object was pruned. Derived commits use the explicit local-controller service identity rather than requiring global Git author configuration.

Only the exact recorded merge OID with its exact tested tree and ordered base/head parents can reconcile. GitHub PR metadata may retain the original approved head or report the authorized derived head; both are explicit allowed observations only when the actual merge OID equals that durable intent. A closed-unmerged, unrelated or unconfirmed PR remains incomplete.

The atomic Git transaction fences only the two ref OIDs. PR open/closed state, private visibility, access permissions, local tools and session state are observations rechecked before dispatch, not values locked by GitHub together with the refs. A metadata change after the final observation may coexist with applied refs. Preserve and reconcile those refs; never claim that the metadata change was atomically rejected, roll back history, repeat an uncertain write or manufacture a successful PR merge.

## Development evidence (not acceptance)

The isolated branch is based on main 43b75826cc83835c7a070bb874c053bfaa2937fb. After initial fixture routing corrections, the hardened focused private-policy/runtime set passed 67 cases in 28.38s. Existing publication/resume/tool-identity cases passed 9 in 71.08s. These include real disposable Git receive-pack transactions, but GitHub API metadata in those tests is mocked. Initial development results are not substituted for the hardened results.

A no-write GitHub capability check at 2026-11-24T08:05:29Z observed the real server advertising `atomic`, with successful `--atomic --dry-run` and unchanged before/after remote-ref digests (6b237ce8586ce5b65ca1f7d6a297ddaf9e2a46bcc12105c9513dacc98970b08c). Private evidence is `/private/tmp/mentat-private-atomic-capability.json`. This establishes advertised capability only; a live PR merge has not been attempted. Actual GitHub merged-state recognition remains for the accepted private workflow demonstration.

Independent reviews and complete native/Linux/package/protected gates remain required before activation. No Titanic files, database, policy, hosted workflows, visibility or billing settings were changed during preparation.

## Dispatch and original publication binding

The durable intent also binds the original PR number, immutable PR ID/node ID and exact publication URL. Reconciliation uses that original identity rather than adopting a replacement PR on the same branch.

Preparation and dispatch are distinct durable events. A capability dry run and all preflight checks occur before dispatch. With no dispatch marker, this implementation has not started a ref write and may proceed after fresh guards. Immediately before the real push, an SQLite transaction revalidates authority, source job, tools, evidence binding, source barrier/epoch and active deadline, and records dispatch exactly once. An unchanged/open remote snapshot after that marker remains unknown; it never authorizes another write, including after resume. Only positive confirmation of the exact recorded merge intent can complete a dispatched operation. The controller never mints a replacement intent to bypass this uncertainty.

This first delivery deliberately provides no automatic retry after a genuinely unknown dispatched network outcome. It checkpoints honestly until the exact result can be observed. A bounded identical-CAS reissue design would be a separate contract and is not implemented. Healthy normal delivery remains automatic. GitHub PR creation/search is explicitly scoped to the pinned repository, independently of gh's default-repository selection.

## Independent review fixes

An unresolved dispatched write fences all new sessions and other dispatches in the same workspace state database, transactionally. The fence clears only after positive confirmation of the exact recorded merge intent, including both remote refs at the derived commit. A new state directory is a separate local authority and cannot inspect another state's journal; operators must preserve and reuse the authoritative state, and separate state directories are not coordinated. Reconciliation fails closed if either ref is unavailable or has moved. Controller-side binary diffs disable text conversion as well as external diff drivers.
