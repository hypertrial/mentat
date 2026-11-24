# Autonomous application assurance

Versioned from the approved 2026-11-23 product vision. Established intent is not inferred from implementation success.

### Requirement: agent-operated
<!-- mentat:requirement-id=b4085f53-5591-5dbf-8718-d21877944bf5; area=product -->

Mentat operates SCL, IVL, and IIL autonomously during an active session; optional human feedback never gates a loop transition.

#### Scenario: agent-operated acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: behavior-preservation
<!-- mentat:requirement-id=d86b4556-1832-529d-be52-8412a2bcdc95; area=product -->

Application changes serve recorded intended behavior. Verification does not become an application runtime dependency or justify a rewrite solely for easier proof.

#### Scenario: behavior-preservation acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: cas
<!-- mentat:requirement-id=3d7c49cc-e290-5e74-94d8-016ba4ce4b5e; area=functional -->

SCL maintains complete OpenSpec CAS across all six areas and distinguishes observed, intended, inferred, and uncertain requirements.

#### Scenario: cas acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: fas
<!-- mentat:requirement-id=c7ddc910-3dec-59a4-af95-4a62094bd503; area=functional -->

FAS records stable atomic claims, formal meaning, preconditions, assumptions, implementation links, and explicit unformalized or unsupported gaps.

#### Scenario: fas acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: independence
<!-- mentat:requirement-id=2752c9c0-b068-5aa5-9337-84a64a08478d; area=functional -->

Every loop uses separate generator and discriminator sessions. Authors cannot independently review their own artifacts; material fixes require renewed review.

#### Scenario: independence acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: verification
<!-- mentat:requirement-id=80c41572-1639-5ca6-99d9-538bb20e1de5; area=functional -->

IVL executes and scrutinizes formal obligations and implementation checks independently; a failed proof attempt is unresolved rather than an established application defect.

#### Scenario: verification acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: improvement
<!-- mentat:requirement-id=f968da79-9b96-54e6-b84c-87f408302708; area=functional -->

IIL fixes only independently adjudicated, reproduced violations of established requirements, then refreshes SCL and IVL and preserves regression obligations.

#### Scenario: improvement acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: monitoring
<!-- mentat:requirement-id=f6d43cfc-273d-5487-b265-310f1e5b2619; area=functional -->

The live UI presents readable CAS, FAS, loop activity, decisions, findings, evidence, and revision changes; optional feedback reaches subsequent iterations.

#### Scenario: monitoring acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: budgets
<!-- mentat:requirement-id=ce55ac49-b6d5-5469-833f-b5262a895ea1; area=nonfunctional -->

Each invocation runs at most two hours with at most three agents concurrently; jobs, attempts and the limit of three unsuccessful rounds per unchanged item remain durable across resume.

#### Scenario: budgets acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: execution-isolation
<!-- mentat:requirement-id=abbaf6b2-d9d1-5f95-9cc2-74e1783cd35f; area=nonfunctional -->

Generated application and proof execution is bounded in a dedicated Linux environment without host mounts, controller credentials, or merge authority.

#### Scenario: execution-isolation acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: honesty
<!-- mentat:requirement-id=cca768da-b2a5-5cfd-8ad0-738c9e69de96; area=nonfunctional -->

Model results, conditional source proofs, empirical checks, inconclusive outcomes, unsupported scope, stale evidence, and unattempted work remain distinguishable.

#### Scenario: honesty acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: sidecar
<!-- mentat:requirement-id=a1886d10-0091-5825-891d-1b2a18e4b997; area=architecture -->

The application, repository OpenSpec and FAS, verification sidecar, and evidence descriptors evolve together while verification tooling remains operationally independent.

#### Scenario: sidecar acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: local-controller
<!-- mentat:requirement-id=d6ad5546-1d76-5e3c-a9b7-320b2c4f4acc; area=architecture -->

A trusted local session controller checkpoints on stability, cancellation, or exhaustion and resumes without duplicate work; all agents use GPT-6.1 Terra at high effort.

#### Scenario: local-controller acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: provenance
<!-- mentat:requirement-id=dda11903-8c6a-5849-8fc3-cbedfcfd3ab2; area=data-api -->

Every result binds the exact code, specification, assumptions, method, translator, tools, and environment; relevant changes invalidate dependent current assurance.

#### Scenario: provenance acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: lineage
<!-- mentat:requirement-id=f5ccc5ca-7707-59bf-88ff-dc27170ba8fa; area=data-api -->

Persistent UUID identifiers survive requirement edits and preserve explicit renames, splits, merges, supersession, and evidence history.

#### Scenario: lineage acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: local-gates
<!-- mentat:requirement-id=4891d2bd-4717-540b-8d90-a0e92b078cd6; area=acceptance -->

Substantial tests, browser checks, proofs, required worker fixtures, and packaging run locally. GitHub CI performs only syntax and whitespace checks.

#### Scenario: local-gates acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: guarded-merges
<!-- mentat:requirement-id=72ae2fdf-994e-5ad4-b214-e0e65fa1a39f; area=acceptance -->

Existing SSH and gh authentication advance qualifying PRs only during active sessions, with exact-revision local checks, independent reviews, enforced branch rules, and two reviews for sensitive changes.

#### Scenario: guarded-merges acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: source-fixture
<!-- mentat:requirement-id=2bcfa241-162e-5b32-8b37-79deae3acd37; area=acceptance -->

The committed clamp fixture satisfies its declared bounds for exact integer inputs with lower no greater than upper; a faulty candidate produces a replayable counterexample.

#### Scenario: source-fixture acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.

### Requirement: readiness
<!-- mentat:requirement-id=32c5a5b9-002e-5163-9ded-6a55f9e46250; area=acceptance -->

Titanic onboarding begins only after complete Mentat acceptance scenarios, real required worker checks, independent review, and a real Mentat self-run.

#### Scenario: readiness acceptance

The independent discriminator inspects the relevant source, revision-bound evidence, assumptions, and explicit coverage gaps. Missing or stale evidence remains unresolved; agent agreement alone does not establish correctness.
