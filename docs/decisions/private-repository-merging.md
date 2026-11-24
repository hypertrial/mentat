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
