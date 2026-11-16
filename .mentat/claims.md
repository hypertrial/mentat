# Claims Spec

## Overview

Mentat is a local CLI for reviewing a committed software change. A reviewer approves a contract from a Git base commit and selects an exact candidate commit. Mentat runs the declared formal and behavior checks, saves the results and a reproduction command, and lets the reviewer record acceptance of a complete passing run.

## Scope limits

- Reviewers supply and review the requirements, formal models, and behavior checks.
- A model result applies to the approved model and its assumptions. A passing run covers the selected commit, approved checks, and recorded environment.
- Whole-program correctness, automatic repair, merging, and deployment are outside Mentat's scope. Acceptance records a local decision.
- Docker behavior checks use a local, prepared runner. Host behavior checks and Lean source execute with the operator's permissions.

## Examples

- [Quickstart](https://github.com/hypertrial/mentat#try-it-on-a-git-repo) — Create a small Git repository, catch a broken clamp command, and verify the fix.
- [Nemo](https://github.com/hypertrial/mentat/blob/main/docs/nemo.md) — Build two historical revisions and replay a mixed-type sum that returned the wrong value and numeric type.
- [Apache Polaris](https://github.com/hypertrial/mentat/blob/main/docs/polaris.md) — Replay a namespace round-trip bug and export a native pytest regression when the failure reproduces. This showcase gives a confirmed violation precedence over an incomplete check.

## Claims

### 1. Validate the contract

The contract must declare at least one formal check. Every claim needs a behavior check, and each check must reference a declared claim using a unique check ID.

Claim `contract.validation` · Behavior `check.contract.validation`

### 2. Use the approved base

Mentat loads the contract, formal inputs, and approved check scripts from the exact base commit selected by the reviewer.

Claim `contract.base-pinning` · Behavior `check.base-pinning`

### 3. Stage the exact candidate

Checks see the exact candidate commit, excluding Git metadata, uncommitted files, and the candidate's root .mentat entry. Unsupported selected Git entries make verification incomplete.

Claim `verification.candidate-pinning` · Behavior `check.verification.candidate-pinning`

### 4. Approve a specific contract

A reviewer confirms the displayed digest in a terminal. Verification requires approval for the same repository path, base commit, runner mode, and verifier fingerprint.

Claim `approval.scope` · Behavior `check.approval.scope`

### 5. Check the Z3 model before its counterexample

An UNSAT check requires a satisfiable base model. A satisfiable counterexample violates the claim, and an inconsistent base model makes the check incomplete.

Claim `formal.z3-vacuity` · Behavior `check.formal.z3-vacuity`

### 6. Check the named Lean theorem and its axioms

Mentat checks that the named theorem is declared by the approved Lean source and audits its transitive axioms. Approval discloses that Lean runs on the host.

Claim `formal.lean-audit` · Behavior `check.formal.lean-audit`

### 7. Apply the selected runner's settings

Docker behavior checks use a prepared, digest-pinned image without networking or host mounts. Host behavior checks require contract opt-in and separate approval and run with the operator's permissions.

Claim `runner.execution` · Behavior `check.runner.execution`

### 8. Require formal checks to pass before behavior runs

Behavior checks begin only after every formal check passes. A saved run records when behavior checks were skipped.

Claim `verification.formal-gate` · Behavior `check.verification.formal-gate`

### 9. Distinguish pass, violation, and incomplete

Core verification exits 0 for pass, 1 for a confirmed violation, and 2 for an incomplete run or invalid input. Incomplete takes precedence in mixed results. Saved runs retain the individual outcomes.

Claim `verification.outcomes` · Behavior `check.outcomes`

### 10. Record how to reproduce the run

The evidence records the base and candidate commits, contract and snapshot digests, recorded tools and runner, check results, and a reproduction command.

Claim `evidence.provenance` · Behavior `check.evidence.provenance`

### 11. Check the bundle before export

Evidence export validates the saved manifest against its local receipt and checks the file set and artifact hashes before copying the bundle.

Claim `evidence.integrity` · Behavior `check.integrity`

### 12. Accept only a complete pass

Acceptance requires an intact passing run under the current approval and a separate operator confirmation in a terminal.

Assumptions

- The bounded model represents status, current approval, evidence integrity, and operator confirmation as independent eligibility gates.

Claim `acceptance.complete-pass` · Behavior `check.acceptance` · Formal `acceptance.eligibility`

### 13. Keep the local and Notion specs in sync

Mentat renders the local spec deterministically and detects drift. Explicit Notion publication mirrors the committed spec in one selected page section and requires a matching local spec at that commit.

Claim `claims.review-sync` · Behavior `check.claims.review-sync`
