"""Exercise candidate Mentat against isolated, committed Git fixtures."""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import traceback
from importlib.util import find_spec
from pathlib import Path

try:
    source = Path(os.environ["MENTAT_CANDIDATE_DIR"]).resolve() / "src"
    sys.path.insert(0, str(source))
    package = find_spec("mentat")
    expected = source / "mentat/__init__.py"
    assert package and package.origin and Path(package.origin).resolve() == expected, (
        "Mentat must import from the candidate's src/mentat package"
    )
    try:
        metadata = tomllib.loads((source.parent / "pyproject.toml").read_text(encoding="utf-8"))
        entry_point = metadata["project"]["scripts"]["mentat"]
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        raise AssertionError("The candidate must declare a mentat console command") from exc
    assert entry_point == "mentat.cli:app", (
        "The candidate must expose mentat.cli:app as the mentat console command"
    )
    from typer.testing import CliRunner

    from mentat import cli
except AssertionError:
    traceback.print_exc()
    sys.exit(1)
except Exception:
    traceback.print_exc()
    sys.exit(2)


class Fixture:
    def __init__(self, root, exits=(0,)):
        self.repo = root / "repo"
        self.repo.mkdir()
        self.state = root / "state"
        self.runner = CliRunner()
        self.git("init", "-q")
        self.git("config", "user.name", "Mentat selfcheck")
        self.git("config", "user.email", "selfcheck@example.invalid")
        checks = self.repo / ".mentat"
        checks.mkdir()
        manifest = {
            "version": 1,
            "claims": [{"id": "fixture", "statement": "Fixture behavior succeeds"}],
            "formal": [
                {
                    "id": "model",
                    "claim": "fixture",
                    "engine": "z3",
                    "source": "model.smt2",
                    "expected": "sat",
                }
            ],
            "behavior": [],
            "allow_host": True,
        }
        (checks / "model.smt2").write_text("(assert true)\n")
        for i, code in enumerate(exits):
            name = f"check{i}.py"
            manifest["behavior"].append(
                {
                    "id": f"behavior{i}",
                    "claim": "fixture",
                    "argv": ["python3", "{checks}/" + name],
                    "artifacts": [f"result{i}.txt"],
                }
            )
            (checks / name).write_text(
                "import os\nfrom pathlib import Path\n"
                f"(Path(os.environ['MENTAT_ARTIFACTS_DIR']) / 'result{i}.txt').write_text('approved')\n"
                "assert not Path('untracked.txt').exists()\n"
                "assert not Path('.mentat').exists()\n"
                "print(Path('candidate.txt').read_text())\n"
                f"raise SystemExit({code})\n"
            )
        (checks / "contract.json").write_text(json.dumps(manifest))
        (self.repo / "candidate.txt").write_text("base")
        self.base = self.commit("base")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def invoke(self, *args):
        result = self.runner.invoke(cli.app, ["--state", str(self.state), *args])
        if result.exception and not isinstance(result.exception, SystemExit):
            raise result.exception
        return result

    def scope(self, base=None):
        return ["--repo", str(self.repo), "--base", base or self.base, "--runner", "host"]

    def approve(self, base=None):
        scope = self.scope(base)
        checked = self.invoke("spec", "check", *scope, "--json")
        assert checked.exit_code == 0, checked.output
        digest = json.loads(checked.output)["contract_digest"]
        previous = cli.operator_confirm
        try:
            cli.operator_confirm = lambda _: None
            approved = self.invoke("spec", "approve", digest, *scope)
        finally:
            cli.operator_confirm = previous
        assert approved.exit_code == 0, approved.output
        return digest

    def verify(self, rev=None, base=None):
        result = self.invoke("verify", *self.scope(base), "--rev", rev or self.base, "--json")
        return result, json.loads(result.output)

    def accept(self, run_id, base=None, confirm=False):
        previous = cli.operator_confirm
        try:
            if confirm:
                cli.operator_confirm = lambda _: None
            return self.invoke("accept", run_id, *self.scope(base))
        finally:
            cli.operator_confirm = previous


def base_pinning(root):
    f = Fixture(root)
    digest = f.approve()
    (f.repo / "candidate.txt").write_text("committed candidate")
    (f.repo / ".mentat/check0.py").write_text("raise SystemExit(1)\n")
    candidate = f.commit("candidate")
    (f.repo / "candidate.txt").write_text("dirty candidate")
    (f.repo / ".mentat/check0.py").write_text("raise SystemExit(2)\n")
    (f.repo / "untracked.txt").write_text("dirty")
    result, record = f.verify(candidate)
    assert result.exit_code == 0, result.output
    assert record["revision"] == candidate and record["base"] == f.base
    assert record["contract_digest"] == digest
    bundle = f.state / "runs" / record["run_id"]
    check = record["cases"][0]["checks"][0]
    assert (bundle / check["stdout"]).read_text().strip() == "committed candidate"
    assert (bundle / check["artifacts"]["result0.txt"]).read_text() == "approved"
    result, _ = f.verify(candidate, candidate)
    assert result.exit_code == 2, result.output


def outcomes(root):
    for index, (codes, expected, status) in enumerate(
        [
            ((0,), 0, "pass"),
            ((1,), 1, "violation"),
            ((2,), 2, "incomplete"),
            ((1, 2), 2, "incomplete"),
            ((2, 1), 2, "incomplete"),
        ]
    ):
        directory = root / str(index)
        directory.mkdir()
        f = Fixture(directory, codes)
        f.approve()
        result, record = f.verify()
        assert result.exit_code == expected and record["status"] == status, result.output
        assert [case["status"] for case in record["cases"]] == [
            {0: "pass", 1: "violation", 2: "incomplete"}[code] for code in codes
        ]


def acceptance(root):
    f = Fixture(root)
    f.approve()
    result, record = f.verify()
    assert result.exit_code == 0, result.output
    run_id = record["run_id"]
    rejected = f.accept(run_id)
    assert rejected.exit_code == 2 and "interactive" in rejected.output.lower(), rejected.output
    assert not (f.state / "acceptances" / f"{run_id}.json").exists()
    accepted = f.accept(run_id, confirm=True)
    assert accepted.exit_code == 0, accepted.output
    assert (
        json.loads((f.state / "acceptances" / f"{run_id}.json").read_text())["revision"] == f.base
    )
    (f.repo / "candidate.txt").write_text("new base")
    newer = f.commit("new base")
    f.approve(newer)
    assert f.accept(run_id, base=newer, confirm=True).exit_code == 2
    (f.state / "runs" / run_id / "report.md").write_text("modified")
    assert f.accept(run_id, confirm=True).exit_code == 2
    other = root / "failing"
    other.mkdir()
    failed = Fixture(other, (1,))
    failed.approve()
    result, record = failed.verify()
    assert result.exit_code == 1, result.output
    assert failed.accept(record["run_id"], confirm=True).exit_code == 2
    incomplete_dir = root / "incomplete"
    incomplete_dir.mkdir()
    incomplete = Fixture(incomplete_dir, (2,))
    incomplete.approve()
    result, record = incomplete.verify()
    assert result.exit_code == 2, result.output
    assert incomplete.accept(record["run_id"], confirm=True).exit_code == 2


def integrity(root):
    f = Fixture(root)
    f.approve()
    result, record = f.verify()
    assert result.exit_code == 0, result.output
    run_id = record["run_id"]
    bundle = f.state / "runs" / run_id
    destination = root / "export"
    exported = f.invoke("evidence", "export", run_id, "--out", str(destination), "--json")
    assert exported.exit_code == 0, exported.output
    manifest = json.loads((destination / "manifest.json").read_text())
    report = (destination / "report.md").read_text()
    for key in ("base", "revision", "contract_digest", "snapshot_digest", "reproduce"):
        assert manifest[key] == record[key], (key, manifest)
    for key in ("base", "revision", "contract_digest", "reproduce"):
        assert record[key] in report, (key, report)
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == {
        p.name: p.read_bytes() for p in bundle.iterdir()
    }
    for name in ("report.md", record["cases"][0]["checks"][0]["artifacts"]["result0.txt"]):
        path = bundle / name
        original = path.read_bytes()
        path.write_bytes(b"tampered")
        rejected = f.invoke("evidence", "export", run_id, "--json")
        assert rejected.exit_code == 2, rejected.output
        path.write_bytes(original)


SCENARIOS = {
    "contract.base-pinning": base_pinning,
    "verification.outcomes": outcomes,
    "acceptance.complete-pass": acceptance,
    "evidence.integrity": integrity,
}

if __name__ == "__main__":
    try:
        with tempfile.TemporaryDirectory(prefix="mentat-selfcheck-") as directory:
            SCENARIOS[sys.argv[1]](Path(directory))
    except AssertionError:
        traceback.print_exc()
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(2)
