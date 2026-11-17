# Claims Spec

## What the app does

Mentat is a local CLI for reviewers who need evidence about a committed software change. A reviewer approves the requirements and checks from a Git base commit, then selects the exact candidate to verify. Mentat runs the declared formal and behavior checks, saves the results and a reproduction command, and lets the reviewer record acceptance of a complete passing run.

## Product limits

- Reviewers supply and review the requirements, formal models, and behavior checks.
- Whole-program correctness, automatic repair, merging, and deployment are outside Mentat's scope. Acceptance records a local decision.
- Docker behavior checks use a local, prepared runner. Host behavior checks and Lean source execute with the operator's permissions.

## Examples

- [Quickstart](https://github.com/hypertrial/mentat#try-it-on-a-git-repo) — Create a small Git repository, catch a broken clamp command, and verify the fix.
- [Apache Iceberg-Go](https://github.com/hypertrial/mentat/blob/main/docs/iceberg-go.md) — Replay a compaction commit that left one of twelve rows visible in the active snapshot, then verify the fix with native Go observations and separate Lean and Z3 specification checks.
- [Nemo](https://github.com/hypertrial/mentat/blob/main/docs/nemo.md) — Build two historical revisions and replay a mixed-type sum that returned the wrong value and numeric type.
- [Apache Polaris](https://github.com/hypertrial/mentat/blob/main/docs/polaris.md) — Replay a namespace round-trip bug and export a native pytest regression when the failure reproduces. This showcase gives a confirmed violation precedence over an incomplete check.

## Reviewing and approving a change

### 1. Validate the contract

The contract must declare at least one formal check. Every claim needs a behavior check, and each check must reference a declared claim using a unique check ID.

Claim `contract.validation` · Behavior `check.contract.validation`

### 2. Use the approved base

Mentat loads the contract, formal inputs, approved check scripts, and explicitly referenced test and fixture files from the exact base commit selected by the reviewer.

Claim `contract.base-pinning` · Behavior `check.base-pinning`

### 3. Approve a specific contract

A reviewer sees which checks and declared test selectors support each claim, can compare two committed contracts, and confirms the displayed digest in a terminal. Verification requires approval for the same repository path, base commit, runner mode, and verifier fingerprint.

Claim `approval.scope` · Behavior `check.approval.scope`

## Verifying a candidate

### 4. Stage the exact candidate

Checks see the exact candidate commit, excluding Git metadata, uncommitted files, and the candidate's root .mentat entry. Regular files and confined relative links to tracked regular files are preserved. Unsupported selected Git entries make verification incomplete.

Claim `verification.candidate-pinning` · Behavior `check.verification.candidate-pinning`

### 5. Apply the selected runner's settings

Docker behavior checks use a prepared, digest-pinned image without networking or host mounts. Host behavior checks require separate approval and run with the operator's permissions. Host preflight reports unavailable executables and declared prerequisites.

Claim `runner.execution` · Behavior `check.runner.execution`

### 6. Require named cases and controls

An approved behavior check can require exact results for named cases and a negative control. Missing required case evidence or an unqualified control makes the check incomplete.

Claim `runner.case-evidence` · Behavior `check.runner.case-evidence`

### 7. Check the Z3 model before its counterexample

An UNSAT check requires a satisfiable base model. A satisfiable counterexample violates the claim, and an inconsistent base model makes the check incomplete.

Claim `formal.z3-vacuity` · Behavior `check.formal.z3-vacuity`

### 8. Check the named Lean theorem and its axioms

Mentat checks that the named theorem is declared by the approved Lean source and audits its transitive axioms. Approval discloses that Lean runs on the host.

Claim `formal.lean-audit` · Behavior `check.formal.lean-audit`

### 9. Require formal checks to pass before behavior runs

Behavior checks begin only after every formal check passes. A saved run records when behavior checks were skipped.

Claim `verification.formal-gate` · Behavior `check.verification.formal-gate`

### 10. Distinguish pass, violation, and incomplete

Core verification exits 0 for pass, 1 for a confirmed violation, and 2 for an incomplete run or invalid input. Incomplete takes precedence in mixed results. Saved runs retain the individual outcomes.

Claim `verification.outcomes` · Behavior `check.outcomes`

## Inspecting evidence and accepting a run

### 11. Record how to reproduce the run

The evidence records the base and candidate commits, contract and snapshot digests, recorded tools and runner, check results, and a reproduction command. Host evidence also records the effective tool search path, prerequisite results when inspected, and a same-host replay command.

Claim `evidence.provenance` · Behavior `check.evidence.provenance`

### 12. Review outcomes by claim

The verification report groups formal and behavior evidence by claim, identifies known violations and gaps, and links the checks to their saved artifacts.

Claim `evidence.claim-results` · Behavior `check.evidence.claim-results`

### 13. Check the bundle before export

Evidence export validates the saved manifest against its local receipt and checks the file set and artifact hashes before copying the bundle.

Claim `evidence.integrity` · Behavior `check.integrity`

### 14. Compare two saved runs by claim

Mentat compares intact saved verification runs with the same approved scope by default. An explicit review mode compares runs across contracts, identifies changed claim definitions, and preserves each original verdict.

Claim `evidence.comparison` · Behavior `check.evidence.comparison`

### 15. Accept only a complete pass

Acceptance requires an intact passing run under the current approval and a separate operator confirmation in a terminal.

Claim `acceptance.complete-pass` · Behavior `check.acceptance` · Formal `acceptance.eligibility`

## Maintaining the Claims Spec

### 16. Keep the local and Notion specs in sync

Mentat renders the local spec deterministically and detects drift. Explicit Notion publication mirrors the committed spec in one selected page section and requires a matching local spec at that commit.

Claim `claims.review-sync` · Behavior `check.claims.review-sync`

### 17. Draft cited claims from OpenSpec

Mentat drafts claims from committed OpenSpec scenarios with exact Git source citations. Adopted draft claims require approved checks.

Claim `claims.openspec-draft` · Behavior `check.claims.openspec-draft`

## Verification coverage and limits

### Coverage limits

- A model result applies to the approved model and its assumptions. A passing run covers the selected commit, approved checks, and recorded environment.

### Behavior checks

Check `check.contract.validation` · Claim `contract.validation` · Standalone check

Check `check.base-pinning` · Claim `contract.base-pinning`

- Selector `contract.base-pinning`

Check `check.verification.candidate-pinning` · Claim `verification.candidate-pinning` · Standalone check

Check `check.approval.scope` · Claim `approval.scope`

- Selector `approval.scope`

Check `check.formal.z3-vacuity` · Claim `formal.z3-vacuity` · Standalone check

Check `check.formal.lean-audit` · Claim `formal.lean-audit` · Standalone check

Check `check.runner.execution` · Claim `runner.execution`

- Selector `runner.execution`

Check `check.runner.case-evidence` · Claim `runner.case-evidence` · Standalone check

Check `check.verification.formal-gate` · Claim `verification.formal-gate` · Standalone check

Check `check.outcomes` · Claim `verification.outcomes` · Standalone check

Check `check.evidence.provenance` · Claim `evidence.provenance`

- Selector `evidence.provenance`

Check `check.evidence.claim-results` · Claim `evidence.claim-results` · Standalone check

Check `check.integrity` · Claim `evidence.integrity` · Standalone check

Check `check.evidence.comparison` · Claim `evidence.comparison` · Standalone check

Check `check.acceptance` · Claim `acceptance.complete-pass` · Standalone check

Check `check.claims.review-sync` · Claim `claims.review-sync` · Standalone check

Check `check.claims.openspec-draft` · Claim `claims.openspec-draft` · Standalone check

### Formal checks and assumptions

Formal check `acceptance.eligibility` · Claim `acceptance.complete-pass` · Z3

- Assumption The bounded model represents status, current approval, evidence integrity, and operator confirmation as independent eligibility gates.

Inventory · 17 behavior checks · 1 formal check · 4 declared selectors
