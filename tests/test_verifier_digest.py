"""Verifier digest scope, presentation import direction, and Polaris pack inputs."""

import ast
import json
import shutil
from importlib import metadata
from pathlib import Path

import pytest
from helpers import git

import mentat
from mentat import project
from mentat.project import PRESENTATION_MODULES, VERIFIER_MODULES
from mentat.showcases.polaris import model_evidence
from mentat.showcases.polaris.spec import PACK, load_spec

MENTAT = Path(mentat.__file__).parent
SRC = MENTAT.parent
CONTRACT = {
    "version": 1,
    "allow_host": True,
    "claims": [{"id": "alpha", "statement": "Primary claim"}],
    "formal": [
        {"id": "proof", "claim": "alpha", "engine": "z3", "source": "model.smt2", "expected": "sat"}
    ],
    "behavior": [{"id": "native", "claim": "alpha", "argv": ["python3", "{checks}/check.py"]}],
}


def _is_module(name):
    path = SRC.joinpath(*name.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def _imported_modules(path):
    """Absolute names of the modules a file imports, resolving relative imports."""
    package = path.relative_to(SRC).parent.parts
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else ()
            module = ".".join([*base, node.module] if node.module else base)
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return {name for name in names if _is_module(name)}


def test_every_source_module_is_classified_exactly_once():
    listed = [f"{name}.py" for name in (*PRESENTATION_MODULES, *VERIFIER_MODULES)]
    found = {
        path.relative_to(MENTAT).as_posix()
        for path in MENTAT.rglob("*.py")
        if path.relative_to(MENTAT).parts[0] != "showcases"
    }
    assert len(listed) == len(set(listed))
    assert set(listed) == found


@pytest.mark.parametrize("name", [name for name in VERIFIER_MODULES if name != "cli"])
def test_verifier_modules_do_not_import_presentation_modules(name):
    presentation = {f"mentat.{module}" for module in PRESENTATION_MODULES}
    assert not _imported_modules(MENTAT / f"{name}.py") & presentation


@pytest.fixture
def checked_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.test")
    checks = repo / ".mentat"
    checks.mkdir()
    (checks / "contract.json").write_text(json.dumps(CONTRACT))
    (checks / "model.smt2").write_text("(assert true)\n")
    (checks / "check.py").write_text("pass\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    return repo, git(repo, "rev-parse", "HEAD")


@pytest.fixture
def verifier(tmp_path, monkeypatch):
    copied = tmp_path / "verifier"
    copied.mkdir()
    for source in MENTAT.glob("*.py"):
        shutil.copyfile(source, copied / source.name)
    monkeypatch.setattr(project, "__file__", str(copied / "project.py"))
    return copied


def _load(checked):
    repo, base = checked
    return project.load_project(repo, base, "host")


@pytest.mark.parametrize("name", PRESENTATION_MODULES)
def test_presentation_edit_keeps_verifier_digest_and_fingerprint(checked_repo, verifier, name):
    before = _load(checked_repo)
    with (verifier / f"{name}.py").open("a") as stream:
        stream.write("\n# presentation-only change\n")
    after = _load(checked_repo)
    assert after.verifier_digest == before.verifier_digest
    assert after.fingerprint == before.fingerprint


def test_verifier_edit_changes_verifier_digest_and_fingerprint(checked_repo, verifier):
    before = _load(checked_repo)
    with (verifier / "runner.py").open("a") as stream:
        stream.write("\n# verifier change\n")
    after = _load(checked_repo)
    assert after.verifier_digest != before.verifier_digest
    assert after.fingerprint != before.fingerprint


def test_missing_verifier_module_is_refused(checked_repo, verifier):
    (verifier / "runner.py").unlink()
    with pytest.raises(ValueError, match="^Verifier source file is missing$"):
        _load(checked_repo)


def test_z3_version_changes_verifier_digest_and_fingerprint(checked_repo, monkeypatch):
    before = _load(checked_repo)
    installed = metadata.version
    monkeypatch.setattr(
        project.metadata,
        "version",
        lambda name: "0.0.0" if name == "z3-solver" else installed(name),
    )
    after = _load(checked_repo)
    assert after.verifier_digest != before.verifier_digest
    assert after.fingerprint != before.fingerprint


def test_missing_z3_distribution_is_refused(checked_repo, monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(project.metadata, "version", missing)
    with pytest.raises(ValueError, match="^Install the project's z3-solver dependency\\.$"):
        _load(checked_repo)


def test_pack_digest_hashes_every_mentat_module_polaris_imports(monkeypatch):
    hashed = []
    digest_file = model_evidence.file_digest

    def record(path):
        hashed.append(Path(path))
        return digest_file(path)

    monkeypatch.setattr(model_evidence, "file_digest", record)
    model_evidence.pack_digest(load_spec(PACK / "contract.json"))
    hashed_modules = {f"mentat.{path.stem}" for path in hashed if path.parent == MENTAT}
    imported = set()
    for path in (MENTAT / "showcases" / "polaris").rglob("*.py"):
        imported |= _imported_modules(path)
    outside = {
        name
        for name in imported
        if name.startswith("mentat.") and not name.startswith("mentat.showcases")
    }
    assert outside
    assert outside <= hashed_modules
