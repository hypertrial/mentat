# Autonomous application assurance

Source: [Hypertrial — Mentat](https://app.notion.com/p/Hypertrial-Mentat-419fbf156f898000ae6cf59282e35137), versioned baseline read 2026-11-23. Later source changes are proposals, not silent specification updates.

Accepted amendment, 2026-11-24: the independent AI Cockpit owns all presentation; Mentat is a headless assurance engine. This explicitly revises the earlier in-engine live UI placement. See [the product decision](decisions/headless-engine.md) and [local integration v1](integration-v1.md).

Mentat continuously specifies, verifies, and improves an application during an active coding session. Humans can monitor it through the independent cockpit and may provide optional feedback; no human approval is a required loop transition. Unresolved intent and unavailable capabilities are reported while independent useful work continues. Session limits, permissions, and configured automated controls remain enforced.

## Connected specifications

The Comprehensive Application Specification (CAS) is organized English OpenSpec documentation in the application repository. It covers product requirements, functional requirements, nonfunctional requirements, system architecture, data/API contracts, and acceptance criteria. Requirements have persistent identifiers, source citations, and explicit observed/intended/inferred/uncertain provenance.

The Formal Atomic Specification (FAS) links small formal obligations to CAS requirements and implementation. It records formal meaning, preconditions, invariants, assumptions, and evidence. Requirements without a formalization or supported execution method remain visible as gaps.

Traceability is requirement → formal claim → implementation → artifact → exact revision. Renames, splits, supersession, and dependent invalidation preserve history.

## Agent loops

- Specification Convergence Loop (SCL): generators inspect source and requirements and maintain CAS/FAS; independent discriminators find omissions, mismatches, unjustified assumptions, and inaccurate formalizations. Established intent is never weakened to accommodate a defect.
- Implementation Verification Loop (IVL): a separate agent group builds and executes verification in an isolated sidecar. Discriminators challenge the formal statement, encoding, assumptions, and connection to actual source. A timeout or failed proof attempt is inconclusive, not an application defect.
- Implementation Improvement Loop (IIL): investigate established violations, produce targeted fixes, refresh SCL/IVL, review independently, run local acceptance controls, and advance qualifying PRs. Application changes serve intended behavior; rewriting solely for easier verification is excluded.

All loop agents use GPT-6.1 Terra at high effort. Independent sessions and tools contribute distinct evidence; agreement alone establishes no theorem.

## Isolation and evidence

The application, OpenSpec, FAS, verification sidecar, and artifact descriptors evolve together in Git. Verification dependencies and environments are separate from application runtime. Generated candidate/proof execution is isolated from host credentials and controller authority.

Results identify code, specification, assumptions, model/translator, tool configuration, and environment. Changes invalidate affected results. Formal-model evidence, source-verification evidence, empirical tests, bounds, and gaps retain their actual method and scope. An audited source translator and its runtime semantics are part of the disclosed trusted computing base.

## Monitoring and autonomy

The headless, versioned local integration exposes supported specifications, formal claims and immutable source links, loop/agent activity, decisions and findings, evidence, freshness and change history. The independent cockpit owns browsing, search and all other presentation. Mentat operates without any UI or connected client. Outcomes distinguish established within scope, evidenced violation, inconclusive, unsupported, unattempted, and stale. Optional feedback informs later iterations without mandatory acknowledgment.

The initial product is single-user and session-scoped: two-hour invocations, three concurrent agents, three unsuccessful rounds per unchanged item, and durable resume. Stable or blocked work checkpoints and returns. A local Linux VM performs substantial checks; GitHub CI remains a syntax and whitespace check. Existing SSH and GitHub CLI authentication support guarded PRs and merges during active sessions. Local controller and GitHub-account integrity are trusted; no independent remote attestation is claimed.
