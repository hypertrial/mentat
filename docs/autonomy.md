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

Install Lima on the controller before provisioning. The managed Ubuntu ARM64 VM has no host directory mounts or port forwarding. The pinned image digest is published in [Canonical's checksums](https://cloud-images.ubuntu.com/releases/noble/release-20261017/SHA256SUMS). Generated verification and application execution belongs inside its bounded guest sandbox. The Lean checker retains its separately pinned toolchain and axiom policies.

Git operations use the existing SSH remote. PR and merge operations use existing `gh` authentication. Missing CLI authentication, unreadable branch rules, stale checks, or unavailable worker tools prevent the corresponding operation; they never cause a fallback to an unguarded merge or host execution. Provision the application's development dependencies in the trusted worker environment before requiring its checks.

## Run and resume

```sh
uv run --locked mentat run --repo /absolute/path/to/application
uv run --locked mentat status --repo /absolute/path/to/application --json
uv run --locked mentat resume --repo /absolute/path/to/application
uv run --locked mentat ui --repo /absolute/path/to/application
```

Run from a clean committed tree. `run` starts a bounded controller and displays its local monitoring URL. `--no-ui` supports terminal-only operation. The UI reads a projection of durable records; it does not authorize changes. Its per-session capability is passed in the browser fragment and authenticated API headers, not query strings. Optional feedback is retained for subsequent iterations.

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

Review context prioritizes the complete sources needed to assess changed FAS
mappings and source contracts, alongside mandatory specifications and the full
proposal diff. A mapping is not accepted when its required source cannot be
inspected within the supported context bounds; the proposal must be split or revised.
Changed-record repository citations may use path objects or path strings with
supported line anchors. Missing, unsafe or malformed local citations fail explicitly;
HTTP(S) references are identified as uninspected external metadata.

When a specification proposal exceeds review capacity, the controller records
the constraint and gives the generator bounded split/revise feedback from the last
accepted code and specification base. Recovery must retain approved application fixes;
ambiguous older state does not authorize a rollback. Rejected drafts remain in history and unreviewed
source paths remain pending. This consumes the existing round budget and creates
no reviewer approval; fitting proposals still require fresh independent reviews.

When work is stable, deferred, cancelled, or out of budget, the controller checkpoints. Resumption preserves attempt counts and fences old workers. Changed source or policy makes prior evidence stale; it must not be displayed as current assurance. Review material fixes again and rerun the required local gate at the final candidate revision.

## Direct Python source evidence

The first backend parses committed source without importing or executing it. It supports a small typed pure subset: exact integer/Boolean inputs, assignments, arithmetic addition/subtraction, branching, comparisons, returns, and restricted literal string outputs. Calls, heap effects, loops, dynamic dispatch, floating point, and other unsupported constructs produce incomplete evidence.

```sh
REV=$(git rev-parse HEAD)
uv run --locked mentat source-verify --repo . "$REV" \
  examples/source-proof/clamp.py clamp --spec examples/source-proof/contract.json
```

Exit 0 means the declared obligation was proved within the reported assumptions, 1 is a counterexample, and 2 is incomplete. A satisfiable precondition is required before checking that no violating execution exists. The audited frontend, selected Python semantics, solver, and unmodified function binding are trusted. Input assumptions and caller reachability are separate obligations. This is not a proof of an entire application or of CPython.

## Local acceptance and publishing

Run `scripts/verify` locally, with additional application, worker, proof, browser, and packaging commands as required by the configured gate. GitHub's small syntax/whitespace job is not evidence that those local checks ran.

The controller records local command results and independent reviews against the exact PR head and base, publishes a compact PR summary through `gh`, and merges only while its session remains active. It checks enforced branch protection and required checks again before a match-head merge. If strict freshness cannot be established, the PR remains unmerged. Raw logs and large artifacts stay local; review them before sharing.

The controller, its local records, and the authenticated GitHub account are trusted. There is no separate signer, signed receipt, or independent remote attestation of a local run. Legacy evidence receipts continue to serve their existing integrity role.
