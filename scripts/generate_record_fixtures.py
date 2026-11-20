#!/usr/bin/env python3
"""Generate the saved-record fixture corpus with one Mentat revision.

Run this with the `mentat` executable of the revision whose records should be
saved. The output embeds absolute host paths, the interpreter path, and PATH,
so commit it only from the record-fixtures workflow (see CONTRIBUTING.md).
"""

from __future__ import annotations

import argparse
import json
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

EXIT_CODES = {"pass": 0, "violation": 1, "incomplete": 2}
PROMPT = b"[y/N]"
COMMAND_TIMEOUT = 600
CONFIRM_TIMEOUT = 300
GIT = [
    "-c",
    "user.name=Mentat Fixtures",
    "-c",
    "user.email=fixtures@example.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgSign=false",
]

# Prepended to the quickstart launcher so it exits outside the declared violation codes.
INCOMPLETE_PREFIX = "# Launcher fails outside the violation codes.\nexit 3\n"
CLAMP_MODEL = """(declare-const x Int)
(assert (and (<= (- 3) x) (<= x 3)))
(define-fun clamp ((n Int)) Int (ite (< n 0) 0 (ite (> n 2) 2 n)))
"""
CLAMP_COUNTEREXAMPLE = """(assert (or
  (and (< x 0) (distinct (clamp x) 0))
  (and (<= 0 x) (<= x 2) (distinct (clamp x) x))
  (and (> x 2) (distinct (clamp x) 2))))
"""
# Forces the formal gate to report a violation: x = 1 satisfies the model.
SATISFIABLE_COUNTEREXAMPLE = "(assert (= x 1))\n"
QUALIFIED_CLAMP = """#!/bin/sh
set -eu
if [ "$1" -lt 0 ]; then
    printf '%s\\n' 0
elif [ "$1" -gt 2 ]; then
    printf '%s\\n' 2
else
    printf '%s\\n' "$1"
fi
"""
# The launcher writes the case report; --negative-control makes "upper" the expected failure.
QUALIFIED_CHECK = """set -u
report="$MENTAT_ARTIFACTS_DIR/cases.json"
upper_expected=2
if [ "${1:-}" = "--negative-control" ]; then
    upper_expected=9
fi
value() {
    out=$(./clamp "$1") || exit 2
}
lower=pass identity=pass upper=pass
for input in -3 -1; do
    value "$input"
    [ "$out" = 0 ] || lower=fail
done
for input in 0 1 2; do
    value "$input"
    [ "$out" = "$input" ] || identity=fail
done
value 3
[ "$out" = "$upper_expected" ] || upper=fail
printf '{"schema_version":1,"cases":{"identity":"%s","lower":"%s","upper":"%s"}}\\n' \\
    "$identity" "$lower" "$upper" > "$report"
case "$lower$identity$upper" in
    *fail*) exit 10 ;;
esac
"""
PRINT_ZERO = "printf '%s\\n' 0"
PRINT_QUOTED_ZERO = "printf '%s\\n' \"0\""


def json_bytes(value) -> bytes:
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode()


def mutant(before: str, after: str, expected_case: str) -> dict:
    return {
        "check": "clamp-cases",
        "path": "clamp",
        "before": before,
        "after": after,
        "expected_case": expected_case,
    }


# upper-bound is killed (3 fails "upper"), upper-equivalent survives (the same
# results), and identity-exit errors (the launcher exits 2 before its report).
MIXED_MUTANTS = {
    "upper-bound": mutant("printf '%s\\n' 2", "printf '%s\\n' 3", "upper"),
    "upper-equivalent": mutant('"$1" -gt 2', '"$1" -ge 3', "upper"),
    "identity-exit": mutant("printf '%s\\n' \"$1\"", "exit 2", "identity"),
}
KILLED_MUTANTS = {
    "upper-bound": mutant("printf '%s\\n' 2", "printf '%s\\n' 3", "upper"),
    "lower-bound": mutant(PRINT_ZERO, "printf '%s\\n' 1", "lower"),
}
SURVIVED_MUTANTS = {"upper-equivalent": mutant('"$1" -gt 2', '"$1" -ge 3', "upper")}
ABSENT_MUTANTS = {"lower-bound": mutant(PRINT_ZERO, "printf '%s\\n' 1", "lower")}


def manifest_text(mutants: dict) -> str:
    return json_bytes({"version": 1, "mutants": mutants}).decode()


def replace_once(old: str, new: str):
    """Return an edit that replaces the only occurrence of old in a file."""

    def edit(text: str) -> str:
        expect(text.count(old) == 1, f"expected exactly one {old!r} in the file")
        return text.replace(old, new)

    return edit


def qualified_contract() -> dict:
    return {
        "version": 1,
        "allow_host": True,
        "claims": [
            {
                "id": "clamp",
                "statement": "Map inputs to 0 through 2 and preserve inputs already in range.",
                "assumptions": ["Inputs are integers from -3 through 3."],
            }
        ],
        "formal": [
            {
                "id": "clamp-model",
                "claim": "clamp",
                "engine": "z3",
                "source": "clamp.smt2",
                "expected": "unsat",
                "counterexample": "counterexample.smt2",
            }
        ],
        "behavior": [
            {
                "id": "clamp-cases",
                "claim": "clamp",
                "argv": ["/bin/sh", "{checks}/check.sh"],
                "artifacts": ["cases.json"],
                "violation_exit_codes": [10],
                "case_report": {
                    "artifact": "cases.json",
                    "required_cases": ["identity", "lower", "upper"],
                },
                "negative_control": {
                    "args": ["--negative-control"],
                    "violation_exit_code": 10,
                    "failing_case": "upper",
                },
            }
        ],
        "source_mutations": "mutants.json",
    }


class Failure(RuntimeError):
    """A scenario did not produce the status it was built to produce."""


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def stop_group(process: subprocess.Popen) -> None:
    """Kill whatever remains in the child's session, then reap the child."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_group(argv: list[str], env: dict) -> tuple[int, str, str]:
    process = subprocess.Popen(
        argv,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = process.communicate(timeout=COMMAND_TIMEOUT)
    finally:
        stop_group(process)
    return process.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def behavior_checks(record: dict) -> list[dict]:
    return [check for case in record["cases"] for check in case["checks"]]


def outcomes(record: dict) -> dict[str, str]:
    return {item["id"]: item["outcome"] for item in record["source_qualifications"]}


class Generator:
    def __init__(self, args: argparse.Namespace):
        self.mentat = Path(os.path.abspath(args.mentat))
        self.examples = Path(os.path.abspath(args.examples))
        self.work = Path(os.path.abspath(args.work))
        self.out = Path(os.path.abspath(args.out))
        self.label = args.label
        self.source_sha = args.source_sha
        self.full = args.features == "full"
        self.require_all = args.require_all
        self.state = self.work / "state"
        path = os.pathsep.join(filter(None, [str(self.mentat.parent), os.environ.get("PATH")]))
        self.env = {**os.environ, "PATH": path}
        self.scenarios: list[dict] = []
        self.status_by_run: dict[str, str] = {}
        self.compare_pairs: list[dict] = []
        self.not_run: dict[str, str] = {}

    def run_all(self) -> None:
        expect(self.mentat.is_file(), f"--mentat is not a file: {self.mentat}")
        expect(self.examples.is_dir(), f"--examples is not a directory: {self.examples}")
        expect(not self.work.exists(), f"--work must not exist: {self.work}")
        expect(not self.out.exists(), f"--out must not exist: {self.out}")
        self.work.mkdir(parents=True)
        self.state.mkdir(mode=0o700)
        (self.work / "repos").mkdir()
        self.quickstart()
        self.python_src()
        if self.full:
            self.qualified()
        self.finish()

    # Subprocess helpers.

    def argv(self, *args) -> list[str]:
        return [str(self.mentat), "--state", str(self.state), *map(str, args)]

    def run(self, argv: list[str]) -> tuple[int, str, str]:
        return run_group(argv, self.env)

    def confirm(self, argv: list[str]) -> tuple[int, str]:
        """Run an operator command on a pseudo-terminal and answer its one prompt with yes."""
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=self.env,
                start_new_session=True,
            )
        except BaseException:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)
        output = b""
        answered = False
        deadline = time.monotonic() + CONFIRM_TIMEOUT
        try:
            while True:
                remaining = deadline - time.monotonic()
                expect(remaining > 0, f"operator command exceeded {CONFIRM_TIMEOUT} seconds")
                ready, _, _ = select.select([master], [], [], min(remaining, 1.0))
                if not ready:
                    continue
                try:
                    chunk = os.read(master, 65536)
                except OSError:  # EIO: the child closed the terminal; wait for its exit.
                    break
                if not chunk:
                    break
                output += chunk
                if not answered and PROMPT in output:
                    os.write(master, b"y\n")
                    answered = True
            code = process.wait(timeout=max(deadline - time.monotonic(), 1.0))
        finally:
            os.close(master)
            stop_group(process)
        return code, output.decode(errors="replace")

    def git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *GIT, *args],
            capture_output=True,
            check=False,
        )
        expect(
            result.returncode == 0,
            f"git {' '.join(args)} failed in {repo}: {result.stderr.decode(errors='replace')}",
        )
        return result.stdout.decode(errors="replace")

    def rev(self, repo: Path, ref: str) -> str:
        return self.git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()

    def create_example(self, name: str, repo: Path) -> None:
        code, out, err = self.run(["sh", str(self.examples / name / "create.sh"), str(repo)])
        expect(code == 0, f"examples/{name}/create.sh failed: {out}{err}")

    def variant(self, repo: Path, parent: str, tag: str, edits: dict) -> str:
        """Commit a new base on top of parent, applying each edit to the file it names."""
        self.git(repo, "checkout", "-q", "--detach", parent)
        for name, edit in edits.items():
            path = repo / name
            path.write_text(edit(path.read_text()))
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", f"Variant base for {tag}")
        self.git(repo, "tag", tag)
        return self.rev(repo, "HEAD")

    # Operator steps.

    def approve(self, repo: Path, base: str, *, trusted: bool = False) -> None:
        code, out, err = self.run(
            self.argv("spec", "check", "--repo", repo, "--base", base, "--runner", "host", "--json")
        )
        expect(code == 0, f"spec check failed for {base}: {out}{err}")
        digest = json.loads(out)["contract_digest"]
        argv = self.argv(
            "spec", "approve", digest, "--repo", repo, "--base", base, "--runner", "host"
        )
        if trusted:
            code, out, err = self.run([*argv, "--trusted-chat-approval"])
            text = out + err
        else:
            code, text = self.confirm(argv)
        expect(code == 0, f"approval of {base} failed: {text}")

    def verify(
        self,
        name: str,
        repo: Path,
        base: str,
        rev: str,
        expected: str,
        *,
        qualified: dict[str, str] | None = None,
    ) -> tuple[str, dict]:
        """Verify rev under base and check its status, exit code, and qualification outcomes.

        qualified maps each mutant ID to its expected outcome; it is compared as a whole.
        """
        code, out, err = self.run(
            self.argv(
                "verify", "--repo", repo, "--base", base, "--rev", rev, "--runner", "host", "--json"
            )
        )
        try:
            result = json.loads(out)
        except ValueError as exc:
            raise Failure(f"{name}: verify printed no JSON record: {out}{err}") from exc
        run_id = result.get("run_id", "")
        expect(
            result.get("status") == expected and code == EXIT_CODES[expected] and run_id,
            f"{name}: expected {expected} with exit {EXIT_CODES[expected]}, got "
            f"{result.get('status')} with exit {code}: {result.get('detail', err)}",
        )
        record = self.manifest(run_id)
        if qualified is not None:
            observed = outcomes(record)
            expect(
                observed == qualified, f"{name}: qualification outcomes {observed} != {qualified}"
            )
        self.scenarios.append(
            {
                "accepted": False,
                "exit_code": code,
                "expected_status": expected,
                "name": name,
                "repo": record["repo"],
                "run_id": run_id,
            }
        )
        self.status_by_run[run_id] = expected
        return run_id, record

    def accept(self, run_id: str, repo: Path, base: str, *, trusted: bool = False) -> None:
        argv = self.argv("accept", run_id, "--repo", repo, "--base", base, "--runner", "host")
        if trusted:
            code, out, err = self.run([*argv, "--trusted-chat-approval"])
            text = out + err
        else:
            code, text = self.confirm(argv)
        expect(code == 0, f"acceptance of run {run_id} failed: {text}")
        expect(
            (self.state / "acceptances" / f"{run_id}.json").is_file(),
            f"acceptance record missing for run {run_id}",
        )
        for scenario in self.scenarios:
            if scenario["run_id"] == run_id:
                scenario["accepted"] = True

    def compare(self, before: str, after: str, *, across: bool = False) -> None:
        extra = ["--across-contracts"] if across else []
        statuses = {self.status_by_run[before], self.status_by_run[after]}
        expected = 2 if "incomplete" in statuses else 0
        code, out, err = self.run(self.argv("evidence", "compare", before, after, "--json", *extra))
        expect(code == expected, f"compare {before} {after} exited {code}: {out}{err}")
        if self.full:  # The HTML comparison is not part of the v0.1.2 CLI.
            html = self.work / f"compare-{len(self.compare_pairs) + 1}.html"
            code, out, err = self.run(
                self.argv("evidence", "compare", before, after, "--out", html, *extra)
            )
            expect(
                code == 0 and html.is_file() and html.stat().st_size > 0,
                f"comparison report for {before} {after} failed: {out}{err}",
            )
        self.compare_pairs.append(
            {
                "across_contracts": across,
                "after": after,
                "before": before,
                "exit_code": expected,
            }
        )

    def manifest(self, run_id: str) -> dict:
        return json.loads((self.state / "runs" / run_id / "manifest.json").read_bytes())

    # Scenarios.

    def quickstart(self) -> None:
        repo = self.work / "repos" / "quickstart"
        self.create_example("quickstart", repo)
        base = self.rev(repo, "mentat-demo-base")
        fixed = self.rev(repo, "mentat-demo-fixed")
        self.approve(repo, base)
        violation, _ = self.verify("quickstart-violation", repo, base, base, "violation")
        passed, _ = self.verify("quickstart-pass", repo, base, fixed, "pass")
        self.accept(passed, repo, base)
        self.compare(violation, passed)

        # The launcher exits 3, outside the declared violation codes.
        incomplete_base = self.variant(
            repo,
            base,
            "mentat-demo-incomplete",
            {".mentat/check.sh": lambda text: INCOMPLETE_PREFIX + text},
        )
        self.approve(repo, incomplete_base)
        _, record = self.verify(
            "quickstart-incomplete", repo, incomplete_base, incomplete_base, "incomplete"
        )
        expect(record["behavior_skipped"] is False, "quickstart-incomplete skipped behavior")
        checks = behavior_checks(record)
        expect(
            [check["exit_code"] for check in checks] == [3],
            f"quickstart-incomplete behavior exit codes were {[c.get('exit_code') for c in checks]}",
        )

        formal_base = self.variant(
            repo,
            base,
            "mentat-demo-formal",
            {".mentat/counterexample.smt2": lambda _: SATISFIABLE_COUNTEREXAMPLE},
        )
        self.approve(repo, formal_base)
        formal, record = self.verify(
            "quickstart-formal-skip", repo, formal_base, formal_base, "violation"
        )
        expect(record["behavior_skipped"] is True, "quickstart-formal-skip ran behavior checks")
        if self.full:
            self.compare(violation, formal, across=True)

    def python_src(self) -> None:
        python = shutil.which("python3", path=self.env["PATH"])
        if python is None:
            self.not_run["python-src"] = "no python3 on the effective PATH"
            return
        probe = subprocess.run([python, "-c", "import pytest"], capture_output=True, check=False)
        if probe.returncode:
            self.not_run["python-src"] = "python3 on the effective PATH cannot import pytest"
            return
        repo = self.work / "repos" / "python-src"
        self.create_example("python-src", repo)
        base = self.rev(repo, "mentat-demo-base")
        bad = self.rev(repo, "mentat-demo-bad")
        self.approve(repo, base)
        self.verify("python-src-violation", repo, base, bad, "violation")
        self.verify("python-src-pass", repo, base, base, "pass")

    def qualified(self) -> None:
        repo = self.work / "repos" / "qualified"
        repo.mkdir()
        self.git(repo, "init", "-q")
        files = {
            ".mentat/contract.json": json_bytes(qualified_contract()).decode(),
            ".mentat/clamp.smt2": CLAMP_MODEL,
            ".mentat/counterexample.smt2": CLAMP_COUNTEREXAMPLE,
            ".mentat/check.sh": QUALIFIED_CHECK,
            ".mentat/mutants.json": manifest_text(MIXED_MUTANTS),
            "clamp": QUALIFIED_CLAMP,
        }
        for name, text in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        os.chmod(repo / "clamp", 0o755)
        self.git(repo, "add", "--force", "--", ".")
        self.git(repo, "commit", "-q", "-m", "Add contract, clamp, and mutation manifest")
        self.git(repo, "tag", "mentat-qualified-base")
        base = self.rev(repo, "HEAD")

        self.approve(repo, base)
        self.verify(
            "qualified-mixed",
            repo,
            base,
            base,
            "incomplete",
            qualified={
                "upper-bound": "killed",
                "upper-equivalent": "survived",
                "identity-exit": "error",
            },
        )

        killed_base = self.variant(
            repo,
            base,
            "mentat-qualified-killed",
            {".mentat/mutants.json": lambda _: manifest_text(KILLED_MUTANTS)},
        )
        self.approve(repo, killed_base, trusted=True)
        killed, _ = self.verify(
            "qualified-all-killed",
            repo,
            killed_base,
            killed_base,
            "pass",
            qualified={"upper-bound": "killed", "lower-bound": "killed"},
        )
        self.accept(killed, repo, killed_base, trusted=True)
        self.compare(self.run_id_for("qualified-mixed"), killed, across=True)

        survived_base = self.variant(
            repo,
            base,
            "mentat-qualified-survived",
            {".mentat/mutants.json": lambda _: manifest_text(SURVIVED_MUTANTS)},
        )
        self.approve(repo, survived_base)
        _, record = self.verify(
            "qualified-survived-only",
            repo,
            survived_base,
            survived_base,
            "violation",
            qualified={"upper-equivalent": "survived"},
        )
        expect(
            record["model_checks"][0]["status"] == "pass"
            and all(check["status"] == "pass" for check in behavior_checks(record)),
            "qualified-survived-only has a failing formal or behavior check",
        )

        absent_base = self.variant(
            repo,
            base,
            "mentat-qualified-absent",
            {".mentat/mutants.json": lambda _: manifest_text(ABSENT_MUTANTS)},
        )
        self.approve(repo, absent_base)
        # The candidate no longer contains the mutant's text, so the mutation cannot apply.
        candidate = self.variant(
            repo,
            absent_base,
            "mentat-qualified-candidate",
            {"clamp": replace_once(PRINT_ZERO, PRINT_QUOTED_ZERO)},
        )
        _, record = self.verify(
            "qualified-error-absent",
            repo,
            absent_base,
            candidate,
            "incomplete",
            qualified={"lower-bound": "error"},
        )
        item = record["source_qualifications"][0]
        expect(
            item["applied_count"] == 0 and item.get("check_result") is None,
            f"qualified-error-absent was not an unapplied mutation: {item}",
        )

        control_base = self.variant(
            repo,
            base,
            "mentat-qualified-control",
            {
                ".mentat/check.sh": replace_once(
                    "    upper_expected=9\n", "    upper_expected=2\n"
                ),
                ".mentat/mutants.json": lambda _: manifest_text(SURVIVED_MUTANTS),
            },
        )
        self.approve(repo, control_base)
        _, record = self.verify(
            "qualified-unqualified-control",
            repo,
            control_base,
            control_base,
            "incomplete",
            qualified={"upper-equivalent": "error"},
        )
        expect(
            behavior_checks(record)[0]["negative_control"]["qualified"] is False,
            "qualified-unqualified-control produced a qualified negative control",
        )

        formal_base = self.variant(
            repo,
            base,
            "mentat-qualified-formal",
            {".mentat/counterexample.smt2": lambda _: SATISFIABLE_COUNTEREXAMPLE},
        )
        self.approve(repo, formal_base)
        _, record = self.verify(
            "qualified-formal-skip", repo, formal_base, formal_base, "violation", qualified={}
        )
        expect(record["behavior_skipped"] is True, "qualified-formal-skip ran behavior checks")
        expect(
            record["source_qualifications"] == [],
            "qualified-formal-skip recorded source qualifications",
        )

    def run_id_for(self, name: str) -> str:
        matches = [scenario["run_id"] for scenario in self.scenarios if scenario["name"] == name]
        expect(len(matches) == 1, f"expected one scenario named {name}")
        return matches[0]

    def finish(self) -> None:
        code, out, err = self.run([str(self.mentat), "--version"])
        expect(code == 0, f"mentat --version failed: {err}")
        version = out.strip()
        expect(version, "mentat --version printed nothing")
        if self.require_all:
            expect(not self.not_run, f"--require-all: scenarios were not run: {self.not_run}")
        index = {
            "compare_pairs": self.compare_pairs,
            "features": "full" if self.full else "basic",
            "label": self.label,
            "mentat_version": version,
            "not_run": self.not_run,
            "scenarios": self.scenarios,
            "source_sha": self.source_sha,
        }
        self.out.mkdir(parents=True)
        shutil.copytree(
            self.state, self.out / "state", ignore=shutil.ignore_patterns(".publish.lock")
        )
        (self.out / "index.json").write_bytes(json_bytes(index))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mentat", required=True, help="mentat executable to write the records")
    parser.add_argument("--examples", required=True, help="examples directory of that revision")
    parser.add_argument("--work", required=True, help="new scratch directory for repos and state")
    parser.add_argument("--out", required=True, help="new directory for the fixture set")
    parser.add_argument("--label", required=True, help="corpus label, such as v0.1.2")
    parser.add_argument("--source-sha", required=True, help="full commit ID of the revision")
    parser.add_argument("--features", required=True, choices=["basic", "full"])
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="fail instead of recording scenarios that cannot run here",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        Generator(parse_args(argv)).run_all()
    except (Failure, subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"record fixtures: {exc!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
