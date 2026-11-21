# Approved Lean proof campaigns

The opt-in `lean-comparator` engine checks proofs of approved models. A model proof
does not prove that an application implements the model. Keep tested implementation
correspondence, witnesses, source mutations and operational assumptions separate.
Existing `lean` checks still use Lean 4.31; Z3 behavior is unchanged.

## Environment and contract

Use a dedicated Linux VM with cgroup v2, systemd, Python, bubblewrap and the official
Lean 4.35.0 distribution at `/opt/lean-4.35.0`. The toolchain must be root-owned
and not writable by proof authors. Mentat refuses a toolchain whose files or links
differ from the official x86_64 or aarch64 archive that
`scripts/install-proof-checker` installs, before running any of its programs. The
expected digests are part of the verifier source. The operator account runs `systemd-run`,
`systemctl`, `chown` and `rm` through `sudo -n`. That grant is root-equivalent:
`systemd-run` can start any command as root. Grant it only in a dedicated VM
whose operator account you already trust with root. Do not mount host directories,
forward credentials or put readable secrets in the guest filesystem. The source
sandbox can read the guest system; read-only access does not hide secrets.

Keep the distribution's namespace protections enabled. Install any required
bubblewrap AppArmor exception only through the operator's normal reviewed setup.
Missing tools, namespace support or service permissions produce an actionable
incomplete result. Mentat does not alter the machine's security policy.

On a fresh Ubuntu machine, `bash scripts/install-proof-checker` installs the
distribution tools and the checksum-pinned official Linux archive. It refuses to
replace an existing toolchain and makes no AppArmor or sysctl changes by default.
The existing Ubuntu CI and Linux release jobs explicitly use `--ci-profile` to
load a checksum-pinned upstream bubblewrap profile on their disposable runners.
It refuses existing policy or local overrides, verifies enforcement, and preserves
the global namespace restriction. The profile permits bubblewrap setup and denies
capabilities to its children; the checker supplies network and filesystem isolation.
The CI environment guard prevents accidental use, rather than attesting host identity.
CI sets `MENTAT_REQUIRE_PROOF_CHECKER=1`; missing
checker prerequisites then fail the real fixtures instead of skipping them.
The existing release matrix requires the checker on Linux and excludes only its
Linux-only fixture module on macOS; all selected release tests retain their
no-skip prerequisite gate. Lean 4.31 stays on `PATH` for legacy checks.

Declare each target with explicit import and axiom policy:

```json
{
  "id": "offers.capacity-proof",
  "claim": "offers.capacity",
  "engine": "lean-comparator",
  "source": "models/offers.lean",
  "theorem": "Offers.capacitySafety",
  "allowed_imports": ["Lean"],
  "permitted_axioms": ["propext", "Classical.choice", "Quot.sound"]
}
```

The approved source contains definitions and target statements with theorem proof
holes. Definition holes are forbidden. Version one accepts a deliberately limited
declarative Lean fragment; active commands, unsafe declarations, custom syntax and
tactic definitions require a different reviewed contract rather than a silent
fallback. Keep fixture evaluation in a separate approved behavior check. A parser
policy and an elaborated environment audit both apply. Sharing one source across
targets requires the same import and axiom policy.

## Prepare, submit and verify

Review and approve the final contract digest using `mentat spec approve`. A change
to the verifier, base, repository location or runner requires renewed approval.

```sh
mentat --state "$STATE" proof prepare --repo "$REPO" --base "$BASE" \
  --rev "$CANDIDATE" --out "$TASKS"
mentat --state "$STATE" proof check "$CAMPAIGN" --submission "$SUBMISSION" --json
mentat --state "$STATE" verify --repo "$REPO" --base "$BASE" \
  --rev "$CANDIDATE" --proof-campaign "$CAMPAIGN" --json
```

Use the default Docker runner for untrusted candidates. Host-runner behavior
checks run candidate code as the operator account. That code, or a process it
leaves running, can rewrite approvals, campaigns, ledgers and saved runs in the
operator state.

Preparation freezes every declared target and creates a new task directory outside
the repository and operator state. Repeat `--target FORMAL_ID` to select a subset
of obligations for one campaign. Task instructions and the manifest are review
copies. A submission directory contains one or more selected target directories,
each containing only `Solution.lean`. Submit a separate directory containing those
target directories, not the task root with its manifest and instructions. Extra
files, compiled objects, toolchain/configuration files, symlinks and escaping paths
are rejected. Proofs may add helper lemmas without changing approved statements
or their supporting definitions.

`proof check` exits zero for accepted submissions, one for a definitive rejection,
and two when checking cannot complete. A failed attempt is retained. Only the
latest submission for a target is eligible; a later failure cannot fall back to an
earlier accepted proof. The latest-submission rule holds for an intact ledger. A
rolled-back ledger can at most re-select an earlier submission, which is still
rechecked against the approved contract. `proof check` results are advisory.
Ordinary verification checks an accepted latest submission again against the
approved source from the base commit. Missing or rejected proofs make formal
obligations incomplete, and the formal gate skips behavior checks. They do not
demonstrate a product defect.

Repeat `--proof-campaign` to combine campaigns with disjoint targets and identical
candidate, contract, approval, verifier and checker identity. Verification records
the proof source as an artifact, its hash, the approved source hash, theorem,
campaign and toolchain/environment identity. Verification binds each campaign to
the repository, base, runner and candidate it was given. A campaign is used only
if its targets, theorems, import and axiom policies and approved-source hashes
equal those in that base contract. The checker receives approved source bytes
from the base contract. Edited campaign files therefore cannot substitute a
weaker target, but this does not make state trustworthy. Campaign and saved-run
manifests have local receipts; ledgers, submissions and logs are only hash-linked
and can be rewritten together. Receipts are not signatures and cannot protect
state, including approval records, from an actor controlling the account.

The checker uses fresh, separate approved and submission caches. A submission may
contain declarations of any kind, `namespace`, `section`, `end`, `open`, `universe`,
`variable`, `mutual` blocks, and `set_option` for `maxHeartbeats` or `maxRecDepth`,
optionally combined with `in`. Any other command is rejected, including evaluation,
printing, macros, custom syntax, notation, initializers and attribute commands. Commands
that run code while compiling are therefore rejected before compilation. Inside a
submission, `unsafe`, `run_tac`, `by_elab`, `include_str`, `bv_check`, `bv_decide`,
`bv_decide?` and `native_decide`, attributes other than `simp`, `reducible` and
`irreducible`, and `set_option` for other options are also rejected. This in-proof
screen is a list of known code-running forms, not a full allowlist. `decide +native`
is not screened; it runs only safe code reachable from the submission, and the audit
rejects the local axiom it adds.
The screen parses with the full `Lean` syntax whatever the submission's imports. The
exporter reads only the single compiled module from a fresh folder, never the writable
build directory; that boundary relies on safe compile-time code having no file-write or
process-spawn primitive once the rejected forms are excluded. The builtin comparator and
kernel remain the acceptance authority, and the environment audit decides axioms and
holes. Proof-source hashing identifies submitted input; it does not certify how the
exported proof was constructed.

## Host coordination and resumption

Mentat runs no models and owns no model credentials. The coding-agent host owns
authors, author budgets and the following recorded workflow:

1. Attempt to falsify each approved target before assigning construction. Record
   retained schedules, counterexamples and witness coverage.
2. Use two bounded proof authors with separate target directories. Keep one
   expensive check active at a time. Record obligations, dependencies, completed
   lemmas, blockers, durations and available usage in a private host ledger;
   label unavailable usage explicitly.
3. Submit at most three rounds per target. Return exact rejected or incomplete
   obligations to the responsible author. Preserve unsuccessful attempts.
4. Enforce a thirty-minute accumulated active host campaign budget, including
   authors and checks, across resumes. Mentat separately enforces thirty minutes
   of accumulated checking and a 120-second service limit per attempt. An
   interrupted reserved attempt consumes 120 seconds until its completion exists.
5. Resume the same campaign and ledger rather than preparing a fresh campaign to
   reset an exhausted budget. Mentat counts rounds and time from earlier campaigns in
   the same state folder with the same approved contract digest and candidate snapshot
   digest. Rounds count per target; the thirty-minute limit is shared by all targets
   of those campaigns. Verification rechecks are charged only to their own campaign and do not use up
   other campaigns' budgets. The
   contract digest covers the repository path, base commit, runner, approved files and
   verifier version, and the snapshot digest covers the candidate. A new candidate
   commit, base, runner, repository path or verifier version therefore starts a new
   budget; the counting only stops a reset by preparing again for the same candidate.
   An unreadable or malformed campaign entry in the state blocks checks, and so does a
   campaign with the same contract and candidate whose files or ledger are invalid,
   until it is removed. Deleting state is outside what Mentat can enforce. Run full clean
   verification after integrating proofs.

An amendment proposal must contain the counterexample or mismatch, requested
change, guarantee impact and witness impact. Have it reviewed independently and
renew operator approval before preparing changed targets. Authors cannot approve
their own amendments or operator acceptance.

Only a complete passing ordinary run can be accepted with `mentat accept`. The
decision uses an operator terminal by default; `--trusted-chat-approval` permits an
unverified agent relay and can be impersonated by any process running as your
account. An accepted proof campaign alone does not authorize deployment
or establish production readiness. Inspect reports and exported proof/log contents
before sharing them; source, claims and diagnostics can contain sensitive text.
