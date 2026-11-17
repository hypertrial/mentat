import subprocess

import pytest

from mentat import openspec


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    return tmp_path


def commit(repo):
    git(repo, "add", "-A")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "spec")
    return git(repo, "rev-parse", "HEAD")


def write(repo, path, content):
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content)
    return file


def test_import_uses_committed_nested_specs_and_precise_source_spans(repo):
    path = "openspec/specs/feature/nested/spec.md"
    spec = write(
        repo,
        path,
        "# Capability\n\n### Requirement: Login\nUsers sign in.\n\n#### Scenario: Success\n- Given a user\n- Then accepted\n\n#### Scenario: Rejected\n- Then rejected\n",
    )
    sha = commit(repo)
    blob = git(repo, "rev-parse", f"{sha}:{path}")
    spec.write_text("dirty content ignored\n")
    draft = openspec.import_spec(repo, sha)
    assert draft["status"] == "pass"
    assert draft["revision"] == sha
    assert len(draft["claims"]) == 2
    first, second = draft["claims"]
    assert first["requires_behavior_check"] is True
    assert first["claim"]["title"] == "Login: Success"
    assert (
        first["claim"]["statement"]
        == "Users sign in.\n\n#### Scenario: Success\n- Given a user\n- Then accepted"
    )
    provenance = first["claim"]["provenance"]
    assert provenance["origin"] == "declared"
    assert provenance["citations"] == [
        {"commit": sha, "blob": blob, "path": path, "start_line": 3, "end_line": 5},
        {"commit": sha, "blob": blob, "path": path, "start_line": 6, "end_line": 9},
    ]
    assert second["claim"]["provenance"]["citations"][1]["start_line"] == 10
    assert second["claim"]["provenance"]["citations"][1]["end_line"] == 11


def test_import_preserves_valid_neighbors_and_ignores_fake_headings(repo):
    write(
        repo,
        "openspec/specs/a/spec.md",
        """# Capability

```
### Requirement: Fake
#### Scenario: Fake
```
<!--
### Requirement: Comment
#### Scenario: Comment
-->
### Requirement: Broken

#### Scenario: Missing body

### Requirement: Real
Text.

#### Scenario: Works
Works.

#### Scenario: Works
Duplicate.
""",
    )
    sha = commit(repo)
    draft = openspec.import_spec(repo, sha)
    assert draft["status"] == "incomplete"
    assert [item["claim"]["title"] for item in draft["claims"]] == ["Real: Works"]
    assert any("missing requirement body" in d["detail"] for d in draft["diagnostics"])
    assert any("duplicate scenario" in d["detail"] for d in draft["diagnostics"])


def test_import_identity_is_stable_across_reordering_but_changes_on_rename(repo):
    path = "openspec/specs/a/spec.md"
    one = "### Requirement: One\nBody one.\n#### Scenario: First\nFirst body.\n"
    two = "### Requirement: Two\nBody two.\n#### Scenario: Second\nSecond body.\n"
    file = write(repo, path, one + "\n" + two)
    first = openspec.import_spec(repo, commit(repo))
    file.write_text(two + "\n" + one)
    reordered = openspec.import_spec(repo, commit(repo))
    file.write_text(two + "\n" + one.replace("First", "Renamed"))
    renamed = openspec.import_spec(repo, commit(repo))

    def ids(result):
        return {item["claim"]["title"]: item["claim"]["id"] for item in result["claims"]}

    assert ids(first) == ids(reordered)
    assert ids(first)["Two: Second"] == ids(renamed)["Two: Second"]
    assert ids(first)["One: First"] != ids(renamed)["One: Renamed"]


def test_import_rejects_symlink_and_keeps_valid_capability(repo):
    write(
        repo,
        "openspec/specs/good/spec.md",
        "### Requirement: Valid\nBody.\n#### Scenario: Yes\nWorks.\n",
    )
    link = repo / "openspec/specs/bad/spec.md"
    link.parent.mkdir(parents=True)
    link.symlink_to("../good/spec.md")
    draft = openspec.import_spec(repo, commit(repo))
    assert draft["status"] == "incomplete"
    assert [item["claim"]["title"] for item in draft["claims"]] == ["Valid: Yes"]
    assert any(d["path"] == str(link.relative_to(repo)) for d in draft["diagnostics"])


def test_import_rejects_near_miss_heading_levels(repo):
    write(
        repo,
        "openspec/specs/a/spec.md",
        "## Requirement: Wrong level\nBody.\n### Scenario: Wrong level\nBody.\n",
    )
    draft = openspec.import_spec(repo, commit(repo))
    assert draft["status"] == "incomplete"
    assert draft["claims"] == []


def test_import_limits_claims_without_dropping_previous_ones(repo):
    text = "".join(f"### Requirement: R{i}\nBody.\n#### Scenario: S\nBody.\n" for i in range(129))
    write(repo, "openspec/specs/a/spec.md", text)
    draft = openspec.import_spec(repo, commit(repo))
    assert draft["status"] == "incomplete"
    assert len(draft["claims"]) == 128
    assert any("too many claims" in d["detail"] for d in draft["diagnostics"])


def test_malformed_requirement_and_orphan_scenario_do_not_hide_valid_neighbor(repo):
    write(
        repo,
        "openspec/specs/a/spec.md",
        """#### Scenario: Orphan
Body.
### Requirement Missing colon
Body.
#### Scenario: Bad
Body.
### Requirement: Good
Body.
#### Scenario: Good
Body.
### Requirement: Good
Body.
#### Scenario: Again
Body.
""",
    )
    draft = openspec.import_spec(repo, commit(repo))
    assert draft["status"] == "incomplete"
    assert [item["claim"]["title"] for item in draft["claims"]] == ["Good: Good"]
    details = [item["detail"] for item in draft["diagnostics"]]
    assert any("orphan scenario" in detail for detail in details)
    assert any("malformed requirement" in detail for detail in details)
    assert any("duplicate requirement" in detail for detail in details)


def test_invalid_utf8_spec_does_not_hide_valid_capability(repo):
    write(
        repo,
        "openspec/specs/good/spec.md",
        "### Requirement: Good\nBody.\n#### Scenario: Good\nBody.\n",
    )
    bad = repo / "openspec/specs/bad/spec.md"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\xff")
    draft = openspec.import_spec(repo, commit(repo))
    assert draft["status"] == "incomplete"
    assert [item["claim"]["title"] for item in draft["claims"]] == ["Good: Good"]
    assert any(item["path"] == "openspec/specs/bad/spec.md" for item in draft["diagnostics"])


def test_cli_draft_adoption_requires_behavior_and_preserves_citations(repo):
    import json

    from typer.testing import CliRunner

    from mentat import cli
    from mentat.claims import render_claims
    from mentat.project import Contract

    spec = write(
        repo,
        "openspec/specs/a/spec.md",
        "### Requirement: Login\nBody.\n#### Scenario: Success\nWorks.\n",
    )
    base = commit(repo)
    spec.write_text("dirty and uncommitted\n")
    invoke = CliRunner().invoke
    result = invoke(
        cli.app, ["spec", "import-openspec", "--repo", str(repo), "--base", base, "--json"]
    )
    assert result.exit_code == 0, result.output
    draft = json.loads(result.output)
    claim = draft["claims"][0]["claim"]
    assert draft["claims"][0]["requires_behavior_check"] is True
    assert spec.read_text() == "dirty and uncommitted\n"

    manifest = {
        "version": 1,
        "claims": [{"id": "original", "statement": "Original"}, claim],
        "formal": [
            {
                "id": "proof",
                "claim": "original",
                "engine": "z3",
                "source": "proof.smt2",
                "expected": "sat",
            }
        ],
        "behavior": [{"id": "check", "claim": "original", "argv": ["true"]}],
        "allow_host": True,
    }
    manifest_path = write(repo, ".mentat/contract.json", json.dumps(manifest))
    write(repo, ".mentat/proof.smt2", "(assert true)\n")
    invalid_base = commit(repo)
    result = invoke(
        cli.app,
        [
            "spec",
            "check",
            "--repo",
            str(repo),
            "--base",
            invalid_base,
            "--runner",
            "host",
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "incomplete"

    manifest["behavior"].append({"id": "new-check", "claim": claim["id"], "argv": ["true"]})
    manifest_path.write_text(json.dumps(manifest))
    valid_base = commit(repo)
    result = invoke(
        cli.app,
        ["spec", "check", "--repo", str(repo), "--base", valid_base, "--runner", "host", "--json"],
    )
    assert result.exit_code == 0, result.output
    reviewed = json.loads(result.output)
    adopted = next(item for item in reviewed["review"] if item["id"] == claim["id"])
    assert adopted["provenance"] == claim["provenance"]
    rendered = render_claims(Contract.model_validate(manifest)).decode()
    for citation in claim["provenance"]["citations"]:
        assert f"{citation['path']}:{citation['start_line']}-{citation['end_line']}" in rendered
        assert citation["commit"] in rendered and citation["blob"] in rendered
