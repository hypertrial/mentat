# Agent entry point

Git is the source of truth for code, specifications, decisions, and handoffs. Run substantial verification locally; GitHub CI is a lightweight syntax and whitespace check. Record the exact commands, revision, outcomes, and unavailable prerequisites in the implementation evidence.

Read [`PROJECT_AGENT.md`](PROJECT_AGENT.md) before implementation. It holds repository-specific invariants, stack notes, and the native verification commands wrapped by `scripts/verify-fast` and `scripts/verify`.

## Work

Before substantial implementation, record intended behavior, invariants, regression surfaces, and verification in a versioned repository specification. Keep scope and decisions readable without access to another service.

## Lifecycle

specify → test/reproduce → implement → targeted verification → full verification → adversarial review → fix findings → re-verify → CI → evidence → done

Implement the smallest coherent change. Do not silently expand scope. Record unrelated findings separately.

## Verification

Development feedback: `scripts/verify-fast`

Completion gate: `scripts/verify`

A change is not complete while required verification is failing or unrun. Record why if a required command cannot run.

## Evidence and handoffs

Record decisions, dependencies, blockers, and evidence in Git alongside the affected specification.

Incomplete work must include a handoff a fresh agent can resume without chat history.

Consequential changes need independent adversarial review before acceptance. Reviewers never silently fix code and authors never approve their own changes.
