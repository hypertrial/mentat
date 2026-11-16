# Project contracts and CLI

Mentat reads the contract and its supporting files from `.mentat/` at the reviewed `--base` commit. It stages implementation files from the candidate `--rev`. Use full commit IDs for both. The candidate can modify its own implementation and tests. The approved files come from the chosen base on every run.

## Write a contract

Create `.mentat/contract.json` and commit it along with the approved models and check scripts. The [quickstart](../examples/quickstart/create.sh) shows a complete contract.

| Field | Contents |
| --- | --- |
| `version` | Manifest version `1`. |
| `claims` | Entries with unique `id`, human-readable `statement`, and optional `assumptions`. |
| `review` | Optional reviewer-facing overview, scope limits, and example links. Requires a short `title` on each claim. |
| `formal` | Entries with `id`, `claim`, `engine`, and the relative `source` file. |
| `behavior` | Entries with `id`, `claim`, and `argv`. Optional `timeout_seconds` defaults to 60 and supports 1–600. Optional `violation_exit_codes` defaults to `[1]` and supports 1–124. Optional `artifacts` lists flat output filenames. |
| `docker_image` | Prepared OCI image reference in the form `repository@sha256:<digest>`. Omit for a host-only contract with `allow_host: true`. |
| `allow_host` | Boolean. Defaults to `false`. |

Every claim needs a behavior check. At least one claim needs both a formal and a behavior check. Use IDs made from letters, digits, dots, underscores, and hyphens, starting with a letter, digit, or underscore. Claim and check IDs are unique across the manifest.

- **Z3** uses the bundled solver. With `engine: "z3"`, set `expected` to `"sat"` or `"unsat"`. The `source` can use SMT-LIB declarations, definitions, and assertions, including `declare-datatype` and `declare-datatypes`. For UNSAT, provide a separate `counterexample` file containing only assertions. Mentat first checks the source alone for satisfiability, then checks both files together for a counterexample.
- **Lean** uses Lean 4.31.0 on the Mentat host. With `engine: "lean"`, set `theorem` to a theorem declared in `source`, such as `"Example.clamp_zero"`. Put the installed toolchain’s `bin` directory on `PATH` and use Lean’s bundled libraries. Mentat checks the theorem and audits its transitive axioms. Review Lean source as executable code because elaboration can run code with the operator’s permissions. Approval explicitly covers this host execution even when behavior checks use Docker.

For behavior, the working directory is the staged candidate root. Each `argv` element is one argument and may be empty after the command name. A leading `{checks}/` selects an approved file relative to `.mentat/`. For example, use `["/bin/sh", "{checks}/check.sh"]`. The runner sets `MENTAT_CANDIDATE_DIR`, `MENTAT_CHECKS_DIR`, and `MENTAT_ARTIFACTS_DIR`. Write optional diagnostic files to the artifacts directory.

Bare commands use absolute entries from the runner’s `PATH`. Use an explicit path such as `./tool` to run a candidate program. For approved Python tests, use `python -I -m pytest` to keep candidate modules out of the launcher’s import path.

Status reflects the checks you wrote. A passing model check establishes a result about the model under its assumptions. A passing behavior check establishes that an approved command passed for the recorded snapshot and environment. For checks that use candidate-owned tests, review changes to those tests as part of the candidate.

## Claims Spec

Edit claim wording, assumptions, and check associations in `.mentat/contract.json`. Regenerate and commit the review copy with the contract.

To present the numbered layout, add a `review` object with an `overview` paragraph and a `scope_limits` list. Optionally add an `examples` list whose entries contain `label`, `summary`, and an HTTPS `url`. Add a short `title` to every claim. The review copy displays claims in contract order and shows their stable claim and check IDs. Contracts using `review` require a Mentat version that supports this layout. Existing version-1 contracts without `review` retain their original rendering.

```sh
mentat claims render --repo "$REPO"
mentat claims check --repo "$REPO"
mentat claims check --repo "$REPO" --base "$BASE" --json
```

The last command reads only the named commit. A missing or stale review copy returns exit `1`. Invalid input or an unavailable service returns `2`. For a contract that includes `claims.md`, contract loading requires the committed file to match the generated copy. Contracts without the file can still be approved and verified.

### Publish to one Notion page

Grant a Notion connection read and update access to the page, and provide its token in `MENTAT_NOTION_TOKEN`. Set `PAGE` to the page URL or ID and `BASE` to the full contract commit ID.

On a new page, add a top-level paragraph reading `Source: working-tree draft` and a divider directly after it. Publish replaces the section from the source line through the divider in one conditional edit, so simultaneous first publications cannot create duplicate sections.

```sh
mentat claims check --repo "$REPO" --base "$BASE" --notion "$PAGE"
mentat claims publish --repo "$REPO" --base "$BASE" --notion "$PAGE"
```

Publish writes only the managed Claims Spec section and records its source commit and claim count. It can update an existing page from the original layout to the numbered layout. Keep the divider immediately after the last claim, before any page notes. Make wording changes in the Git contract. An unchanged spec can retain the commit where it was last published. The page is a review copy. Approval and verification depend on the committed contract.

### Verify Mentat itself

From a commit containing the root [Claims Spec](../.mentat/claims.md), run Mentat with `uv run --locked mentat` and select `--runner host` for `spec check`, `spec approve`, and `verify`. The approved checks validate the staged console entry point and use Python and Git to exercise the candidate with isolated repositories and state. Use the host runner with trusted Mentat source.

## Runners

**Docker on Apple Silicon macOS.** Start the dedicated Colima VM and pull your contract’s image into it.

```sh
colima start mentat --runtime docker --vm-type vz --mount none --cpu 4 --memory 6 --disk 20
docker --context colima-mentat pull 'repository@sha256:<digest>'
mentat doctor --repo "$REPO" --base "$BASE"
```

**Docker on Linux x86-64 or ARM64.** The locked Z3 wheel requires glibc 2.27 or later on x86-64 and glibc 2.38 or later on ARM64. Select a named context with a local Unix socket. The following commands use the standard Docker socket.

```sh
docker context create mentat --docker 'host=unix:///var/run/docker.sock'
docker --context mentat pull 'repository@sha256:<digest>'
export MENTAT_DOCKER_CONTEXT=mentat
mentat doctor --repo "$REPO" --base "$BASE"
```

Use `--docker-context NAME` before the command to override the environment. Supply tools and dependencies in the image before verifying. Docker runs one non-root container per behavior check, with its network disabled and without host mounts. Use reviewed images without secrets or declared volumes. Containers share the engine’s kernel and use its storage for writes. Use a dedicated VM with bounded disk capacity for untrusted candidates.

**Host mode.** Set `allow_host` in the reviewed contract and add `--runner host` to `doctor`, `spec check`, `spec approve`, `verify`, and `accept`. Approve this mode separately. Each check gets a fresh temporary copy, but it can use the operator’s filesystem permissions and local network. Use it with code and tools you trust.

Mentat accepts regular Git blobs with executable bits preserved. Symlinks, submodules, and unresolved Git LFS pointers in the selected files yield incomplete results. Limits per loaded tree are 4,096 files, 1 MB per file, and 32 MB total. The candidate snapshot skips the root `.mentat` entry before traversal, so its contents do not count toward these limits. Selected paths retain their spelling from the Git tree. The snapshot excludes Git metadata and uncommitted files.

## Review, verify, and accept

1. Run `mentat spec check --repo "$REPO" --base "$BASE"`. Review the manifest and supporting files at that commit, then approve the displayed digest with `mentat spec approve DIGEST --repo "$REPO" --base "$BASE"`.
2. Run `mentat verify --repo "$REPO" --base "$BASE" --rev "$CANDIDATE" --json`. Formal checks run first. Behavior checks run after they pass. Use a new approval when the base, verifier, repository path, or runner mode changes.
3. Export with `mentat evidence export RUN_ID --out NEW_DIRECTORY`. The bundle contains a Markdown report and a JSON manifest with the commit IDs, check results and provenance, artifact hashes, and reproduction command. Logs and artifacts can contain untrusted or sensitive text. Review them before sharing.
4. Run `mentat accept RUN_ID --repo "$REPO" --base "$BASE"` to accept a complete passing run under the matching approval. Both approval and acceptance require a terminal.

Verification exits `0` for pass, `1` for a confirmed violation in a completed run, and `2` for invalid input or an incomplete run. An incomplete check takes precedence in the exit status when another check reports a violation. The manifest retains each result. A model violation can prevent behavior execution. Timeouts, missing tools, and resource kills are incomplete. Behavior output is capped at 1 MiB per stream and 16 MiB per run across logs and artifacts. The manifest records any omitted logs or optional files.

State defaults to `~/.local/state/mentat` or `$XDG_STATE_HOME/mentat`. Set `--state PATH` before the command to use a different operator-owned directory outside the project checkout. The state directory has mode `0700`. Bundle integrity receipts are local, so protect state from other same-user processes.
