# Claims Spec

Generated from `contract.json`. Edit the contract and regenerate.

## contract.base-pinning

Approved checks come from the base and candidate source comes from the exact committed revision.

- Formal checks: None
- Behavior checks: `check.base-pinning`

## verification.outcomes

Verification exits zero for pass, one for violation, and two for incomplete, with incomplete taking precedence.

- Formal checks: None
- Behavior checks: `check.outcomes`

## acceptance.complete-pass

Acceptance requires operator confirmation and an intact complete pass under the current approval.

Assumptions

- The bounded model represents status, current approval, evidence integrity, and operator confirmation as independent eligibility gates.

Checks

- Formal checks: `acceptance.eligibility`
- Behavior checks: `check.acceptance`

## evidence.integrity

Evidence export exposes and preserves pinned provenance and rejects modified reports and artifacts.

- Formal checks: None
- Behavior checks: `check.integrity`
