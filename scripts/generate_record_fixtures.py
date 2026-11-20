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
import subprocess
import sys
from pathlib import Path

EXIT_CODES = {"pass": 0, "violation": 1, "incomplete": 2}
PROMPT = b"[y/N]"
COMMAND_TIMEOUT = 600
PROMPT_TIMEOUT = 120
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
    "lower-bound": mutant("printf '%s\\n' 0", "printf '%s\\n' 1", "lower"),
}


def manifest_text(mutants: dict) -> str:
    return json_bytes({"version": 1, "mutants": mutants}).decode()


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


class Generator:
    def __init__(self, args: argparse.Namespace):
        self.mentat = Path(os.path.abspath(args.mentat))
        self.examples = Path(os.path.abspath(args.examples))
        self.work = Path(os.path.abspath(args.work))
        self.out = Path(os.path.abspath(args.out))
        self.label = args.label
        self.source_sha = args.source_sha
        self.full = args.features == "full"
        self.state = self.work / "state"
        path = os.pathsep.join(filter(None, [str(self.mentat.parent), os.environ.get("PATH")]))
        self.env = {**os.environ, "PATH": path}
        self.scenarios: list[dict] = []
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
        result = subprocess.run(
            argv, env=self.env, capture_output=True, timeout=COMMAND_TIMEOUT, check=False
        )
        return (
            result.returncode,
            result.stdout.decode(errors="replace"),
            result.stderr.decode(errors="replace"),
        )

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
        finally:
            os.close(slave)
        output = b""
        answered = False
        try:
            while True:
                ready, _, _ = select.select([master], [], [], PROMPT_TIMEOUT)
                if not ready:
                    raise Failure(f"no operator prompt within {PROMPT_TIMEOUT} seconds")
                try:
                    chunk = os.read(master, 65536)
                except OSError:  # Linux reports EIO after the child closes the terminal.
                    break
                if not chunk:
                    break
                output += chunk
                if not answered and PROMPT in output:
                    os.write(master, b"y\n")
                    answered = True
        finally:
            os.close(master)
            if process.poll() is None:
                process.kill()
        return process.wait(COMMAND_TIMEOUT), output.decode(errors="replace")

    def git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *GIT, *args], capture_output=True, check=False
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

    def verify(self, name: str, repo: Path, base: str, rev: str, expected: str) -> tuple[str, dict]:
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

    def compare(self, before: str, after: str) -> None:
        code, out, err = self.run(self.argv("evidence", "compare", before, after, "--json"))
        expect(
            code == 0 and json.loads(out)["status"] == "pass",
            f"compare of {before} and {after} failed: {out}{err}",
        )
        self.compare_pairs.append({"after": after, "before": before})

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

        # Unexpected launcher exit 3 is outside the declared violation codes.
        incomplete_base = self.variant(
            repo,
            base,
            "mentat-demo-incomplete",
            {".mentat/check.sh": lambda text: INCOMPLETE_PREFIX + text},
        )
        self.approve(repo, incomplete_base)
        self.verify("quickstart-incomplete", repo, incomplete_base, incomplete_base, "incomplete")

        formal_base = self.variant(
            repo,
            base,
            "mentat-demo-formal",
            {".mentat/counterexample.smt2": lambda _: SATISFIABLE_COUNTEREXAMPLE},
        )
        self.approve(repo, formal_base)
        _, record = self.verify(
            "quickstart-formal-skip", repo, formal_base, formal_base, "violation"
        )
        expect(record["behavior_skipped"] is True, "quickstart-formal-skip ran behavior checks")

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
        _, record = self.verify("qualified-mixed", repo, base, base, "incomplete")
        outcomes = {item["outcome"] for item in record["source_qualifications"]}
        expect(
            outcomes == {"killed", "survived", "error"},
            f"qualified-mixed outcomes were {sorted(outcomes)}",
        )

        killed_base = self.variant(
            repo,
            base,
            "mentat-qualified-killed",
            {".mentat/mutants.json": lambda _: manifest_text(KILLED_MUTANTS)},
        )
        self.approve(repo, killed_base, trusted=True)
        killed, record = self.verify("qualified-all-killed", repo, killed_base, killed_base, "pass")
        expect(
            {item["outcome"] for item in record["source_qualifications"]} == {"killed"},
            "qualified-all-killed did not kill every mutation",
        )
        self.accept(killed, repo, killed_base, trusted=True)

        formal_base = self.variant(
            repo,
            base,
            "mentat-qualified-formal",
            {".mentat/counterexample.smt2": lambda _: SATISFIABLE_COUNTEREXAMPLE},
        )
        self.approve(repo, formal_base)
        _, record = self.verify(
            "qualified-formal-skip", repo, formal_base, formal_base, "violation"
        )
        expect(record["behavior_skipped"] is True, "qualified-formal-skip ran behavior checks")
        expect(
            all(item["status"] == "skipped" for item in record["source_qualifications"]),
            "qualified-formal-skip ran source qualification",
        )

    def finish(self) -> None:
        code, out, err = self.run([str(self.mentat), "--version"])
        expect(code == 0, f"mentat --version failed: {err}")
        index = {
            "compare_pairs": self.compare_pairs,
            "label": self.label,
            "mentat_version": out.strip(),
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        Generator(parse_args(argv)).run_all()
    except (Failure, subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"record fixtures: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
