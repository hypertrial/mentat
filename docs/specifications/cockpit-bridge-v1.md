# Cockpit project verification bridge v1

Cockpit starts `mentat --state PATH bridge --stdio` as a private subprocess. The bridge serves explicit Git project verification using the same approved contract, checks, evidence validation and acceptance rules as the terminal CLI. Mentat owns assurance and Cockpit owns its local task workflow and authenticated review UI. The loopback autonomy session API remains a separate interface.

## Wire contract

Use bounded UTF-8 newline-delimited JSON. Requests are `{id,method,params}`; responses are `{id,result}` or `{id,error:{code,message}}`. `operation.updated` notifications have `{method,params}` and no request ID. Keep diagnostic output off stdout. `initialize` takes `{version:1}` and returns `{version:1,capabilities,instanceId}`. Capabilities are the 14 implemented v1 methods below. Reject unsupported versions and invalid frames without executing a method. Do not allow unbounded frames, pending decisions or operation results.

A repository scope carries an absolute `repo`, a full committed `base`, a `runner` of `docker` or `host`, and optional `state` when no global state was configured. Candidate methods additionally carry the full committed `revision`. Reject ambiguous or conflicting identities and scope. A run ID is a saved Mentat run identity. A caller-chosen operation ID is scoped to its original verification inputs.

| Method | Additional input | Result |
| --- | --- | --- |
| `initialize` | `version` | `version`, `capabilities`, `instanceId` |
| `project.inspect` | scope | `ContractSummary` |
| `specification.get` | scope | `claims`, `files`, optional `digest` |
| `approval.prepare` | scope, optional `hostConsent` | `DecisionReview` |
| `approval.commit` | `token` only | `approved`, `source` |
| `verification.start` | candidate scope, `operationId`, optional `hostConsent` | `operationId` |
| `verification.cancel` | `operationId` | operation snapshot |
| `operation.get` | `operationId` | operation snapshot |
| `runs.list` | optional scope | `runs` with `runId`, `revision`, `status` |
| `runs.get` | `runId`, optional full `revision` or scope | `assurance`, validated `record` |
| `evidence.export` | `runId`, `out` in a new directory | `path`, `runId` |
| `evidence.compare` | `before`, `after` run IDs | authoritative evidence comparison |
| `acceptance.prepare` | candidate scope, `runId`, optional `hostConsent` | `DecisionReview` |
| `acceptance.commit` | `token` only | `accepted`, `source`, `assurance` |

`ContractSummary` exposes status, runner, claims and the available verifier digest, committed files and approval source. `DecisionReview` carries kind, one-use token, scope, expiry and review detail. Approval presents all committed verification inputs as bounded review artifacts or refuses the decision. Assurance carries run/candidate/base/contract identities, check result, claims and method, available qualification, freshness (`current`, `historical`, `unknown`), eligibility and recorded acceptance. A preserved historical receipt may remain accepted as recorded while eligibility is false. Missing revision or an unavailable freshness check cannot yield current eligibility. Additive optional v1 result fields may be ignored.

## Authority and recovery

- Validate approvals, evidence format and receipt integrity with current Mentat rules before presenting or using records. Recheck HEAD, base, pinned candidate, runner, verifier fingerprint, approval and run manifest as applicable when committing a decision. Tokens expire, are scoped to the issuing instance and cannot be reused. `cockpit_local_operator` records a local session assertion. Host checks require explicit consent for the reviewed scope and again for the actual candidate run.
- A repeated operation ID may return its prior receipt only for the same inputs. A lost reply is reconciled by reading durable operation state. Incomplete or orphaned work is never automatically replayed. A saved completed violation is an operation `completed` with an assurance failure. A cancellation that cannot establish that all work stopped is `incomplete`, including uncertain descendant cleanup. Operation status can be `running`, `completed`, `failed`, `incomplete`, `cancel_requested`, `interrupted` or `canceled`.
- Use the existing validated legacy reader for retained bundles and current machine-readable format for new bundles. Compare and export authoritative checked evidence. Keep a fresh approval when the verifier fingerprint changes. Display text and untrusted artifacts carry no authority.

## Acceptance and evidence

Port from the preserved prototype without moving its Git worktree or changing historical record fixtures. Lock v1 method names and representative assurance/status examples in producer-owned protocol fixtures. Run focused bridge, CLI, evidence, qualification and replay checks followed by `scripts/verify-fast`, `scripts/verify` and the zero-skip dedicated Linux `scripts/verify-worker` gate. Verify installed wheel and sdist from a clean committed source revision. The Cockpit consumer must exercise the installed wheel against the clamp failing baseline, repaired candidate, stale-candidate refusal, acceptance and export. Pairing evidence belongs with the Cockpit consumer; this specification and producer evidence belong in Mentat Git. Cockpit tracking begins at WORK-32.
