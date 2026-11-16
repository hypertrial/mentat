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
| `behavior` | Entries with `id`, `claim`, and `argv`. Optional `timeout_seconds` defaults to 60 and supports 1–600. Optional `violation_exit_codes` defaults to `[1]` and supports 1–124. Optional `artifacts` lists flat output filenames. Optional `selectors` is a list of arguments expanded at a standalone `{selectors}` element in `argv`. |
| `docker_image` | Prepared OCI image reference in the form `repository@sha256:<digest>`. Omit for a host-only contract with `allow_host: true`. |
| `allow_host` | Boolean. Defaults to `false`. |
| `base_files` | Optional list of tracked file paths outside `.mentat/` to load from the base commit. |
| `host_requirements` | Optional host prerequisites: `executables`, `python_distributions`, and `loopback`. |

Every claim needs a behavior check. At least one claim needs both a formal and a behavior check. Use IDs made from letters, digits, dots, underscores, and hyphens, starting with a letter, digit, or underscore. Claim and check IDs are unique across the manifest.

- **Z3** uses the bundled solver. With `engine: "z3"`, set `expected` to `"sat"` or `"unsat"`. Supported source commands are `set-logic`, `declare-const`, `declare-datatype`, `declare-datatypes`, `declare-fun`, `declare-sort`, `define-fun`, `define-fun-rec`, `define-funs-rec`, `define-sort`, and `assert`. For UNSAT, provide a separate `counterexample` file containing only assertions. Mentat first checks the source alone for satisfiability, then checks both files together for a counterexample.
- **Lean** uses Lean 4.31.0 on the Mentat host. Set `theorem` to a plain Lean identifier declared in `source`, such as `"Example.clamp₀"`. Put the installed toolchain’s `bin` directory on `PATH`. Sources can import Lean’s bundled libraries or use an import-free prelude. Mentat checks the theorem and audits its transitive axioms. Review Lean source as executable code because elaboration can run code with the operator’s permissions. Approval explicitly covers this host execution even when behavior checks use Docker.

For behavior, the working directory is the staged candidate root. Each `argv` element is one argument and may be empty after the command name. A leading `{checks}/` selects an approved file relative to `.mentat/`. For example, use `["/bin/sh", "{checks}/check.sh"]`. The runner sets `MENTAT_CANDIDATE_DIR`, `MENTAT_CHECKS_DIR`, and `MENTAT_ARTIFACTS_DIR`. Write optional diagnostic files to the artifacts directory.

Bare commands use absolute entries from the runner’s `PATH`. Use an explicit path such as `./tool` to run a candidate program. For approved Python tests, use an isolated launcher such as the [src-layout example](../examples/python-src/create.sh).

Status reflects the checks you wrote. A passing model check establishes a result about the model under its assumptions. A passing behavior check establishes that an approved command passed for the recorded snapshot and environment. For checks that use candidate-owned tests, review changes to those tests as part of the candidate.

## Approved base files and selectors

List exact tracked paths in `base_files` to stage their base-commit contents under `{checks}/base/<path>`. Review these files with the rest of the contract before approval. Paths must name regular files outside `.mentat/`. The example stages `tests/conftest.py`, a test, and its JSON fixture from the base. The launcher adds the staged candidate's `src` directory for application imports.

A behavior check may declare `selectors` and a single standalone `{selectors}` argument in `argv`. Mentat expands the list in place, preserving argument boundaries. The example passes pytest node IDs. Its isolated Python launcher disables third-party plugin autoload and restricts config and conftest discovery to the settings and files it explicitly selects. Pytest exit `1` is a violation. Collection errors, no tests collected, and other nonzero exit codes are incomplete by default.

For host checks, `host_requirements` can list executable names or absolute paths in `executables` and installed Python distribution names in `python_distributions`. Set `loopback` to `true` when a check needs to bind an IPv4 loopback listener. Mentat reports availability and treats missing declared prerequisites as incomplete. It inspects Python distribution metadata with the `python3` found on the runner's effective `PATH`.

Compare two contracts that support the selected runner with `--base "$NEW_BASE" --against "$OLD_BASE"` on `spec check` and `spec approve`. The displayed Git diff command reads the original commit objects, matching the inputs Mentat reviewed. Review the comparison and the new contract before approving its digest. Both the local and Notion Claims Specs display declared behavior selectors.

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
3. Export with `mentat evidence export RUN_ID --out NEW_DIRECTORY`. The bundle contains a Markdown report and a JSON manifest with the commit IDs, check results and provenance, artifact hashes, and reproduction command. For host runs, `host_reproduce` uses the recorded working directory, interpreter, and invocation `PATH` on the same host. The report also records the filtered behavior `PATH` and discovered prerequisites. Replay uses the tools currently installed in those locations. Review logs, local paths, and artifacts before sharing the evidence.
4. Run `mentat accept RUN_ID --repo "$REPO" --base "$BASE"` to accept a complete passing run under the matching approval. Both approval and acceptance require a terminal.

Verification exits `0` for pass, `1` for a confirmed violation in a completed run, and `2` for invalid input or an incomplete run. An incomplete check takes precedence in the exit status when another check reports a violation. The manifest retains each result. A model violation can prevent behavior execution. Timeouts, missing tools, and resource kills are incomplete. Behavior output is capped at 1 MiB per stream and 16 MiB per run across logs and artifacts. The manifest records any omitted logs or optional files.

State defaults to `~/.local/state/mentat` or `$XDG_STATE_HOME/mentat`. Set `--state PATH` before the command to use a different operator-owned directory outside the project checkout. The state directory has mode `0700`. Bundle integrity receipts are local, so protect state from other same-user processes.
