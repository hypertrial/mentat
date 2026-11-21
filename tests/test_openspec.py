import subprocess

import pytest
from helpers import commit, git

from mentat import openspec


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    return tmp_path


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


def test_import_includes_scenarios_with_plain_level_four_titles(repo):
    path = "openspec/specs/drafts/spec.md"
    write(
        repo,
        path,
        "# Drafts\n\n## Purpose\nDescribe how drafts are saved.\n\n## Requirements\n"
        "### Requirement: Save drafts\nThe editor SHALL retain drafts.\n"
        "#### Scenario: Save online\n- WHEN the editor saves online\n- THEN it stores a copy\n"
        "#### Save offline\n- GIVEN the editor is offline\n- WHEN the editor saves\n"
        "- THEN it stores the draft locally\n",
    )
    result = openspec.import_spec(repo, commit(repo))
    assert result["status"] == "pass"
    claims = [entry["claim"] for entry in result["claims"]]
    assert [claim["title"] for claim in claims] == [
        "Save drafts: Save online",
        "Save drafts: Save offline",
    ]
    assert "stores the draft locally" in claims[1]["statement"]
    assert claims[1]["provenance"]["citations"][1]["start_line"] == 12


@pytest.mark.parametrize("heading", ["#### Scenario: Scenario", "#### Scenario"])
def test_import_accepts_scenario_as_a_title(repo, heading):
    write(
        repo,
        "openspec/specs/drafts/spec.md",
        f"### Requirement: Save drafts\nThe editor SHALL retain drafts.\n{heading}\nIt saves.\n",
    )
    result = openspec.import_spec(repo, commit(repo))
    assert result["status"] == "pass"
    (entry,) = result["claims"]
    assert entry["claim"]["title"] == "Save drafts: Scenario"


@pytest.mark.parametrize("entry", ["gitlink", "symlink"])
def test_import_reports_unreadable_capability_trees(repo, entry):
    write(
        repo,
        "openspec/specs/local/spec.md",
        "### Requirement: Local feature\nIt SHALL work.\n#### Scenario: Works\nIt works.\n",
    )
    first = commit(repo)
    path = "openspec/specs/linked"
    if entry == "gitlink":
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{first},{path}")
        git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "add Git-linked capability")
        revision = git(repo, "rev-parse", "HEAD")
    else:
        (repo / path).symlink_to("local", target_is_directory=True)
        revision = commit(repo)
    draft = openspec.import_spec(repo, revision)
    assert draft["status"] == "incomplete"
    assert [item["claim"]["title"] for item in draft["claims"]] == ["Local feature: Works"]
    assert draft["diagnostics"] == [{"path": path, "line": 1, "detail": "unsupported spec entry"}]


@pytest.mark.parametrize("count,expected_calls", [(1, 2), (128, 8)])
def test_import_batches_git_objects_and_preserves_identity_and_provenance(
    repo, monkeypatch, count, expected_calls
):
    import hashlib

    from mentat import process

    source = "### Requirement: Feature\nBody.\n#### Scenario: Works\nWorks.\n"
    for index in reversed(range(count)):
        write(repo, f"openspec/specs/{index:03}/spec.md", source)
    revision = commit(repo)
    real_run = process.run
    calls = []

    def counted(command, *args, **kwargs):
        if "cat-file" in command:
            calls.append(command)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(process, "run", counted)
    draft = openspec.import_spec(repo, revision)
    assert draft["status"] == "pass"
    assert len(calls) == expected_calls
    assert len(draft["claims"]) == count
    blob = git(repo, "rev-parse", f"{revision}:openspec/specs/000/spec.md")
    for index, entry in enumerate(draft["claims"]):
        path = f"openspec/specs/{index:03}/spec.md"
        identity = hashlib.sha256((path + "\0Feature\0Works").encode()).hexdigest()
        claim = entry["claim"]
        assert claim["id"] == "openspec." + identity
        assert claim["provenance"]["citations"] == [
            {"commit": revision, "blob": blob, "path": path, "start_line": 1, "end_line": 2},
            {"commit": revision, "blob": blob, "path": path, "start_line": 3, "end_line": 4},
        ]


def test_import_isolates_bad_blobs_across_chunk_boundary(repo, monkeypatch):
    from mentat import gitio

    for index in range(35):
        path = f"openspec/specs/{index:03}/spec.md"
        content = f"### Requirement: R{index}\nBody.\n#### Scenario: S\nBody.\n"
        file = write(repo, path, content)
        if index == 1:
            file.write_bytes(b"x" * 256)
        elif index == 31:
            file.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
        elif index == 32:
            file.write_bytes(b"\xff")
    revision = commit(repo)
    monkeypatch.setattr(gitio, "MAX_FILE", 128)
    original = openspec.read_blobs

    def corrupt_one(root, entries):
        if any("/033/" in path for path in entries):
            raise ValueError("invalid Git object")
        return original(root, entries)

    monkeypatch.setattr(openspec, "read_blobs", corrupt_one)
    draft = openspec.import_spec(repo, revision)
    assert draft["status"] == "incomplete"
    bad = {1, 31, 32, 33}
    assert [entry["claim"]["title"] for entry in draft["claims"]] == [
        f"R{i}: S" for i in range(35) if i not in bad
    ]
    assert [item["path"] for item in draft["diagnostics"]] == [
        f"openspec/specs/{i:03}/spec.md" for i in sorted(bad)
    ]


def fail_paths(monkeypatch, bad):
    original = openspec.read_blobs
    calls = []

    def read(root, entries):
        calls.append(len(entries))
        if any(path in bad for path in entries):
            raise ValueError("git command failed: cat-file") from OSError("unavailable")
        return original(root, entries)

    monkeypatch.setattr(openspec, "read_blobs", read)
    return calls


@pytest.mark.parametrize("failure", [OSError("unavailable"), subprocess.TimeoutExpired("git", 1)])
@pytest.mark.parametrize("wrapped", [False, True])
def test_import_retries_failed_batch_per_file_and_reports_only_the_failing_file(
    repo, monkeypatch, failure, wrapped
):
    for index in range(65):
        write(repo, f"openspec/specs/{index:03}/spec.md",
              f"### Requirement: R{index}\nBody.\n#### Scenario: S\nBody.\n")
    revision = commit(repo)
    original = openspec.read_blobs
    calls = []

    def fail_040(root, entries):
        calls.append(list(entries))
        if any("/040/" in path for path in entries):
            if wrapped:
                raise ValueError("git failed") from failure
            raise failure
        return original(root, entries)

    monkeypatch.setattr(openspec, "read_blobs", fail_040)
    draft = openspec.import_spec(repo, revision)
    assert draft["status"] == "incomplete"
    assert len(calls) == 1 + (1 + 32) + 1
    assert [item["claim"]["title"] for item in draft["claims"]] == [
        f"R{i}: S" for i in range(65) if i != 40
    ]
    detail = "git failed" if wrapped else "git blob read failed"
    assert draft["diagnostics"] == [
        {"path": "openspec/specs/040/spec.md", "line": 1, "detail": detail}
    ]


def test_import_stops_when_no_file_in_a_batch_reads(repo, monkeypatch):
    for index in range(64):
        write(repo, f"openspec/specs/{index:03}/spec.md",
              f"### Requirement: R{index}\nBody.\n#### Scenario: S\nBody.\n")
    revision = commit(repo)
    calls = fail_paths(monkeypatch, {f"openspec/specs/{i:03}/spec.md" for i in range(32)})
    draft = openspec.import_spec(repo, revision)
    assert draft["status"] == "incomplete"
    assert draft["claims"] == []
    assert draft["diagnostics"] == [
        {
            "path": "openspec/specs/000/spec.md",
            "line": 1,
            "detail": "git command failed: cat-file",
        }
    ]
    # One batch read and one retry per file; the second batch is never read.
    assert calls == [32] + [1] * 32


def test_import_reads_failed_single_file_batch_once(repo, monkeypatch):
    for index in range(65):
        write(repo, f"openspec/specs/{index:03}/spec.md",
              f"### Requirement: R{index}\nBody.\n#### Scenario: S\nBody.\n")
    revision = commit(repo)
    calls = fail_paths(monkeypatch, {"openspec/specs/064/spec.md"})
    draft = openspec.import_spec(repo, revision)
    assert [item["path"] for item in draft["diagnostics"]] == ["openspec/specs/064/spec.md"]
    assert len(draft["claims"]) == 64
    assert calls == [32, 32, 1]


def test_import_reports_first_and_last_file_of_each_batch(repo, monkeypatch):
    for index in range(64):
        write(repo, f"openspec/specs/{index:03}/spec.md",
              f"### Requirement: R{index}\nBody.\n#### Scenario: S\nBody.\n")
    revision = commit(repo)
    bad = {0, 31, 32, 63}
    fail_paths(monkeypatch, {f"openspec/specs/{i:03}/spec.md" for i in bad})
    draft = openspec.import_spec(repo, revision)
    assert draft["status"] == "incomplete"
    assert [item["path"] for item in draft["diagnostics"]] == [
        f"openspec/specs/{i:03}/spec.md" for i in sorted(bad)
    ]
    assert [item["claim"]["title"] for item in draft["claims"]] == [
        f"R{i}: S" for i in range(64) if i not in bad
    ]


def test_import_keeps_every_file_when_each_retry_reads(repo, monkeypatch):
    for index in range(40):
        write(repo, f"openspec/specs/{index:03}/spec.md",
              f"### Requirement: R{index}\nBody.\n#### Scenario: S\nBody.\n")
    revision = commit(repo)
    original = openspec.read_blobs
    calls = []

    def fail_batches(root, entries):
        calls.append(len(entries))
        if len(entries) > 1:
            raise ValueError("git command failed: cat-file") from OSError("unavailable")
        return original(root, entries)

    monkeypatch.setattr(openspec, "read_blobs", fail_batches)
    draft = openspec.import_spec(repo, revision)
    assert draft["status"] == "pass"
    assert draft["diagnostics"] == []
    assert calls == [32] + [1] * 32 + [8] + [1] * 8
    assert [item["claim"]["title"] for item in draft["claims"]] == [
        f"R{i}: S" for i in range(40)
    ]


def test_import_checks_deadline_before_retrying_bad_batch(repo, monkeypatch):
    for index in range(2):
        write(repo, f"openspec/specs/{index}/spec.md",
              f"### Requirement: R{index}\nBody.\n#### Scenario: S\nBody.\n")
    revision = commit(repo)
    clock = [0]
    monkeypatch.setattr(openspec.time, "monotonic", lambda: clock[0])
    attempts = []

    def fail(root, entries):
        attempts.append(entries)
        clock[0] = 61
        raise ValueError("bad object")

    monkeypatch.setattr(openspec, "read_blobs", fail)
    draft = openspec.import_spec(repo, revision)
    assert len(attempts) == 1
    assert draft["status"] == "incomplete"
    assert "time limit" in draft["diagnostics"][0]["detail"]


def test_import_checks_deadline_between_successfully_read_files(repo, monkeypatch):
    for index in range(2):
        write(repo, f"openspec/specs/{index}/spec.md",
              f"### Requirement: R{index}\nBody.\n#### Scenario: S\nBody.\n")
    revision = commit(repo)
    clock = [0]
    monkeypatch.setattr(openspec.time, "monotonic", lambda: clock[0])
    original = openspec.read_blobs

    def read(root, entries):
        blobs = original(root, entries)
        first = next(iter(blobs))
        mode, data = blobs[first]

        class AdvanceOnDecode(bytes):
            def decode(self, *args, **kwargs):
                clock[0] = 61
                return super().decode(*args, **kwargs)

        blobs[first] = (mode, AdvanceOnDecode(data))
        return blobs

    monkeypatch.setattr(openspec, "read_blobs", read)
    draft = openspec.import_spec(repo, revision)
    assert [item["claim"]["title"] for item in draft["claims"]] == ["R0: S"]
    assert draft["status"] == "incomplete"
    assert "time limit" in draft["diagnostics"][0]["detail"]


def test_import_honors_aggregate_size_and_diagnostic_limits(repo, monkeypatch):
    source = "### Requirement: Good\nBody.\n#### Scenario: S\nBody.\n"
    for index in range(4):
        write(repo, f"openspec/specs/{index}/spec.md", source)
    revision = commit(repo)
    monkeypatch.setattr(openspec, "MAX_TOTAL", len(source.encode()) * 2)
    draft = openspec.import_spec(repo, revision)
    assert len(draft["claims"]) == 2
    assert draft["status"] == "incomplete"
    assert "total size" in draft["diagnostics"][0]["detail"]

    write(repo, "openspec/specs/0/spec.md", "#### Scenario: Orphan\nBody.\n" * 10)
    revision = commit(repo)
    monkeypatch.setattr(openspec, "MAX_TOTAL", 10000)
    monkeypatch.setattr(openspec, "MAX_FILES", 4)
    draft = openspec.import_spec(repo, revision)
    assert draft["status"] == "incomplete"
    assert len(draft["diagnostics"]) == 4
