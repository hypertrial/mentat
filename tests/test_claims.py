import copy
import json

import pytest
from markdown_it import MarkdownIt
from test_project import commit
from typer.testing import CliRunner

from mentat import cli
from mentat.project import load_project

pytest_plugins = ["test_project"]


def invoke(repo, *args):
    return CliRunner().invoke(cli.app, ["claims", *args, "--repo", str(repo)])


def write_contract(repo, contract):
    (repo / ".mentat/contract.json").write_text(json.dumps(contract, ensure_ascii=False))


@pytest.mark.parametrize("numbered", [False, True])
def test_claims_render_exposes_exact_selectors(project_repo, numbered):
    repo, _, manifest = project_repo
    if numbered:
        manifest["review"] = {"overview": "An app.", "scope_limits": ["Only recorded cases."]}
        manifest["claims"][0]["title"] = "Review the app"
    manifest["behavior"][0]["argv"].append("{selectors}")
    manifest["behavior"][0]["selectors"] = ["tests/test_app.py::test_case[one]", "two `ticks`"]
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    content = (repo / ".mentat/claims.md").read_text()
    assert r"Check `behavior` selects tests/test\_app.py::test\_case\[one\]" in content
    assert r"Check `behavior` selects two \`ticks\`" in content


def test_render_is_deterministic_and_check_detects_drift(project_repo):
    repo, _, manifest = project_repo
    missing = invoke(repo, "check", "--json")
    assert missing.exit_code == 1, missing.output
    json.loads(missing.output)

    first = invoke(repo, "render", "--json")
    assert first.exit_code == 0, first.output
    json.loads(first.output)
    path = repo / ".mentat/claims.md"
    original = path.read_bytes()
    assert original
    assert invoke(repo, "check").exit_code == 0
    assert invoke(repo, "render").exit_code == 0
    assert path.read_bytes() == original

    path.write_bytes(original + b"\nmanual edit\n")
    assert invoke(repo, "check", "--json").exit_code == 1
    assert invoke(repo, "render").exit_code == 0
    assert path.read_bytes() == original

    changed = copy.deepcopy(manifest)
    changed["claims"][0]["statement"] = "A changed claim"
    write_contract(repo, changed)
    assert invoke(repo, "check").exit_code == 1
    assert invoke(repo, "render").exit_code == 0
    assert path.read_bytes() != original
    assert invoke(repo, "check").exit_code == 0


def test_base_check_and_load_ignore_dirty_claims_but_reject_committed_drift(project_repo):
    repo, legacy, _ = project_repo
    # A legacy contract without a committed claims document remains loadable.
    load_project(repo, legacy, "host")
    assert invoke(repo, "check", "--base", legacy).exit_code == 1
    assert invoke(repo, "render").exit_code == 0
    synced = commit(repo, "add generated claims")
    load_project(repo, synced, "host")
    (repo / ".mentat/claims.md").write_text("dirty edit\n")
    assert invoke(repo, "check", "--base", synced).exit_code == 0
    assert invoke(repo, "check").exit_code == 1
    load_project(repo, synced, "host")

    stale = commit(repo, "commit stale claims")
    assert invoke(repo, "check", "--base", stale).exit_code == 1
    with pytest.raises(ValueError):
        load_project(repo, stale, "host")
    result = CliRunner().invoke(
        cli.app,
        ["spec", "check", "--repo", str(repo), "--base", stale, "--runner", "host", "--json"],
    )
    assert result.exit_code != 0, result.output


def test_invalid_inputs_fail_without_overwriting_claims(project_repo):
    repo, base, _ = project_repo
    assert invoke(repo, "render").exit_code == 0
    path = repo / ".mentat/claims.md"
    original = path.read_bytes()
    (repo / ".mentat/contract.json").write_text("{invalid")
    for command in ("render", "check"):
        result = invoke(repo, command, "--json")
        assert result.exit_code == 2, result.output
        json.loads(result.output)
    assert path.read_bytes() == original
    for invalid in ("HEAD", base[:12], "0" * 40):
        result = invoke(repo, "check", "--base", invalid, "--json")
        assert result.exit_code == 2, result.output
        json.loads(result.output)


def test_host_only_contract_and_docker_requirement(project_repo):
    repo, _, manifest = project_repo
    del manifest["docker_image"]
    write_contract(repo, manifest)
    host_only = commit(repo, "host only")
    load_project(repo, host_only, "host")
    with pytest.raises(ValueError):
        load_project(repo, host_only, "docker")
    assert invoke(repo, "render").exit_code == 0
    manifest["allow_host"] = False
    write_contract(repo, manifest)
    invalid = commit(repo, "no available runner")
    with pytest.raises(ValueError):
        load_project(repo, invalid, "host")
    assert invoke(repo, "render").exit_code == 2


def test_render_preserves_claim_text_assumptions_and_check_associations(project_repo):
    repo, _, manifest = project_repo
    manifest["claims"] = [
        {
            "id": "first",
            "statement": "First *literal* café\n# second [line]",
            "assumptions": ["Given `α`\nand <β>", "No _other_ input"],
        },
        {
            "id": "second",
            "statement": "Independent claim 漢字\r\n---\r\n| field | unit |\n| -- | -- |",
        },
    ]
    manifest["formal"][0]["claim"] = "first"
    manifest["formal"].append({**manifest["formal"][0], "id": "proof2"})
    manifest["behavior"][0]["claim"] = "first"
    manifest["behavior"].append({**manifest["behavior"][0], "id": "behavior2"})
    manifest["behavior"].append({**manifest["behavior"][0], "id": "other", "claim": "second"})
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    text = (repo / ".mentat/claims.md").read_text()
    assert "First \\*literal\\* café\n\\# second \\[line\\]" in text
    assert "Given \\`α\\`\n  and \\<β\\>" in text
    assert "No \\_other\\_ input" in text
    assert "Independent claim 漢字" in text
    assert "Independent claim 漢字\n\\---\n" in text
    assert r"\| field \| unit \|" in text
    first, second = text.split("## second", 1)
    assert "## first" in first
    for check in ("proof", "proof2", "behavior", "behavior2"):
        assert f"`{check}`" in first
        assert f"`{check}`" not in second
    assert "`other`" in second
    assert "`other`" not in first


def test_notion_is_only_consulted_when_requested(project_repo, monkeypatch):
    repo, _, _ = project_repo
    assert invoke(repo, "render").exit_code == 0
    base = commit(repo, "add claims")
    calls = []

    def check_notion(page, content, revision, *, publish):
        calls.append((page, content, revision, publish))
        return True

    monkeypatch.setattr(cli, "notion_claims", check_notion)
    assert invoke(repo, "check").exit_code == 0
    assert invoke(repo, "check", "--base", base).exit_code == 0
    assert calls == []
    assert invoke(repo, "check", "--notion", "page").exit_code == 2
    assert calls == []
    assert invoke(repo, "check", "--base", base, "--notion", "page").exit_code == 0
    assert len(calls) == 1
    assert calls[0] == ("page", (repo / ".mentat/claims.md").read_bytes(), base, False)


def test_render_refuses_symlinked_claims_and_contract_directory(project_repo, tmp_path):
    repo, _, _ = project_repo
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = outside / "claims.md"
    destination.write_text("outside")
    directory = repo / ".mentat"
    (directory / "claims.md").symlink_to(destination)
    assert invoke(repo, "render").exit_code == 2
    assert destination.read_text() == "outside"
    (outside / "contract.json").write_bytes((directory / "contract.json").read_bytes())
    directory.rename(repo / "old-mentat")
    directory.symlink_to(outside, target_is_directory=True)
    assert invoke(repo, "render").exit_code == 2
    assert destination.read_text() == "outside"


def test_selfcheck_does_not_fall_back_to_installed_mentat(monkeypatch, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("MENTAT_CANDIDATE_DIR", str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-I", str(root / ".mentat/selfcheck.py"), "contract.base-pinning"],
        env=os.environ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Mentat must import from the candidate" in result.stderr


def test_selfcheck_detects_broken_console_entry_point(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "src/mentat", tmp_path / "src/mentat")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\nmentat = "mentat.cli:missing"\n')
    result = subprocess.run(
        [sys.executable, "-I", str(root / ".mentat/selfcheck.py"), "verification.outcomes"],
        env={**os.environ, "MENTAT_CANDIDATE_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "mentat console command" in result.stderr


def numbered_manifest(manifest):
    manifest = copy.deepcopy(manifest)
    manifest["review"] = {
        "overview": "A concise overview.",
        "scope_limits": ["Only the supported inputs."],
        "examples": [
            {"label": "Example", "summary": "A useful example.", "url": "https://example.com/demo"}
        ],
    }
    manifest["claims"][0]["title"] = "Validate the candidate"
    return manifest


def test_legacy_render_bytes_are_unchanged(project_repo):
    repo, _, _ = project_repo
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    assert (repo / ".mentat/claims.md").read_bytes() == (
        b"# Claims Spec\n\nGenerated from `contract.json`. Edit the contract and regenerate.\n"
        b"\n## claim\n\ncandidate is valid\n\n- Formal checks: `proof`\n"
        b"- Behavior checks: `behavior`\n"
    )


def test_numbered_render_order_ids_and_committed_drift(project_repo):
    repo, _, manifest = project_repo
    manifest = numbered_manifest(manifest)
    manifest["claims"].append(
        {"id": "other", "title": "Second claim", "statement": "Another statement."}
    )
    manifest["behavior"].append({**manifest["behavior"][0], "id": "other.check", "claim": "other"})
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    path = repo / ".mentat/claims.md"
    original = path.read_bytes()
    text = original.decode()
    headings = [line for line in text.splitlines() if line.startswith("#")]
    assert headings == [
        "# Claims Spec",
        "## Overview",
        "## Scope limits",
        "## Examples",
        "## Claims",
        "### 1. Validate the candidate",
        "### 2. Second claim",
    ]
    assert "A concise overview." in text and "Only the supported inputs." in text
    assert "Example" in text and "A useful example." in text and "https://example.com/demo" in text
    assert "Claim `claim` · Behavior `behavior` · Formal `proof`" in text
    assert "Claim `other` · Behavior `other.check`" in text
    assert invoke(repo, "render").exit_code == 0
    assert path.read_bytes() == original
    base = commit(repo, "numbered claims")
    manifest["claims"].reverse()
    write_contract(repo, manifest)
    assert invoke(repo, "check").exit_code == 1
    assert invoke(repo, "check", "--base", base).exit_code == 0
    assert invoke(repo, "render").exit_code == 0
    changed = path.read_text()
    assert changed.index("### 1. Second claim") < changed.index("### 2. Validate the candidate")
    assert "Claim `other` · Behavior `other.check`" in changed
    stale = commit(repo, "changed numbered claims")
    path.write_bytes(original)
    assert invoke(repo, "check", "--base", stale).exit_code == 0
    assert invoke(repo, "check").exit_code == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["claims"][0].pop("title"),
        lambda m: m["claims"][0].update(title=""),
        lambda m: m["review"].pop("overview"),
        lambda m: m["review"].pop("scope_limits"),
        lambda m: m["review"].update(overview=""),
        lambda m: m["review"].update(overview=" \n "),
        lambda m: m["review"].update(scope_limits=[]),
        lambda m: m["review"].update(scope_limits=["  "]),
        lambda m: m["review"].update(
            examples=[{"label": "x", "summary": "s", "url": "http://example.com"}]
        ),
        lambda m: m["review"].update(
            examples=[{"label": "x", "summary": "s", "url": "javascript:alert(1)"}]
        ),
        lambda m: m["review"].update(
            examples=[{"label": "x", "summary": "s", "url": "https://example.com/)\n## injected"}]
        ),
        lambda m: m["review"].update(examples=[{"label": "x", "summary": "s"}]),
    ],
)
def test_invalid_numbered_metadata_never_overwrites(project_repo, change):
    repo, _, manifest = project_repo
    assert invoke(repo, "render").exit_code == 0
    path = repo / ".mentat/claims.md"
    original = path.read_bytes()
    manifest = numbered_manifest(manifest)
    change(manifest)
    write_contract(repo, manifest)
    result = invoke(repo, "render", "--json")
    assert result.exit_code == 2, result.output
    assert path.read_bytes() == original


def test_numbered_optional_examples_and_assumptions(project_repo):
    repo, _, manifest = project_repo
    manifest = numbered_manifest(manifest)
    del manifest["review"]["examples"]
    manifest["claims"][0]["assumptions"] = ["Given a valid input"]
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    text = (repo / ".mentat/claims.md").read_text()
    assert "## Examples" not in text
    assert "Given a valid input" in text
    assert "Claim `claim` · Behavior `behavior` · Formal `proof`" in text


def test_numbered_escapes_example_link_and_preserves_check_associations(project_repo):
    repo, _, manifest = project_repo
    manifest = numbered_manifest(manifest)
    manifest["review"]["examples"][0].update(
        label="Example] text",
        url="https://example.com/a)injected",
    )
    manifest["formal"].append({**manifest["formal"][0], "id": "proof2"})
    manifest["behavior"].append({**manifest["behavior"][0], "id": "behavior2"})
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    text = (repo / ".mentat/claims.md").read_text()
    assert r"[Example\] text](https://example.com/a\)injected)" in text
    assert "Claim `claim` · Behavior `behavior`, `behavior2` · Formal `proof`, `proof2`" in text


def test_numbered_markdown_preserves_literal_heading_hashes_and_link_targets(project_repo):
    repo, _, manifest = project_repo
    manifest = numbered_manifest(manifest)
    manifest["claims"][0]["title"] = "Literal hashes ###"
    examples = manifest["review"]["examples"]
    examples[0]["url"] = "https://example.com/?a=1&copy;"
    examples.append({**examples[0], "url": "https://github.com&commat;evil.example/path"})
    examples.append({**examples[0], "url": "https://example.com/files/guide(final).pdf?sig=hash"})
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    text = (repo / ".mentat/claims.md").read_text()
    parsed = MarkdownIt().parse(text)
    heading_index = next(i for i, token in enumerate(parsed) if token.tag == "h3")
    assert parsed[heading_index + 1].children[0].content == "1. Literal hashes ###"
    links = [
        child.attrGet("href")
        for token in parsed
        for child in token.children or []
        if child.type == "link_open"
    ]
    assert links == [example["url"] for example in examples]


def test_numbered_review_indentation_stays_prose(project_repo):
    repo, _, manifest = project_repo
    manifest = numbered_manifest(manifest)
    manifest["review"]["overview"] = "    A concise overview."
    manifest["review"]["scope_limits"] = ["\n\n    Only the supported inputs.\n  \n"]
    manifest["review"]["examples"][0]["summary"] = "Summary.\n\n    More details."
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    html = MarkdownIt().render((repo / ".mentat/claims.md").read_text())
    assert "<p>A concise overview.</p>" in html
    assert "<li>Only the supported inputs.</li>" in html
    assert "<p>More details.</p>" in html
    assert "<pre>" not in html


def grouped_manifest(manifest):
    manifest = numbered_manifest(manifest)
    manifest["claims"].append(
        {"id": "other", "title": "Another capability", "statement": "Another claim."}
    )
    manifest["review"]["groups"] = [
        {"title": "First capability", "claim_ids": ["other"]},
        {"title": "Second capability", "claim_ids": ["claim"]},
    ]
    manifest["review"]["verification_limits"] = ["Only sampled inputs are checked."]
    manifest["behavior"].append(
        {**copy.deepcopy(manifest["behavior"][0]), "id": "other.check", "claim": "other"}
    )
    manifest["behavior"][0]["argv"].append("{selectors}")
    manifest["behavior"][0]["selectors"] = [
        "tests/test_app.py::test_case[one]",
        "two `ticks`",
        "  café {checks}/x  ",
    ]
    manifest["claims"][0]["assumptions"] = ["Given a valid input"]
    return manifest


def test_grouped_render_order_coverage_literals_and_history(project_repo):
    repo, _, manifest = project_repo
    manifest = grouped_manifest(manifest)
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    path = repo / ".mentat/claims.md"
    original = path.read_bytes()
    text = original.decode()
    assert [line for line in text.splitlines() if line.startswith("#")] == [
        "# Claims Spec",
        "## What the app does",
        "## Product limits",
        "## Examples",
        "## First capability",
        "### 1. Another capability",
        "## Second capability",
        "### 2. Validate the candidate",
        "## Verification coverage and limits",
        "### Coverage limits",
        "### Behavior checks",
        "### Formal checks and assumptions",
    ]
    assert "Claim `claim` · Behavior `behavior` · Formal `proof`" in text
    assert "Check `behavior` · Claim `claim`\n\n- Selector" in text
    assert "Formal check `proof` · Claim `claim` · Z3\n\n- Assumption Given a valid input" in text
    assert "Only sampled inputs are checked." in text
    assert text.rstrip().endswith(
        "Inventory · 2 behavior checks · 1 formal check · 3 declared selectors"
    )
    tokens = MarkdownIt().parse(text)
    code = [
        child.content
        for token in tokens
        for child in token.children or []
        if child.type == "code_inline"
    ]
    for selector in manifest["behavior"][0]["selectors"]:
        assert selector in code
    assert invoke(repo, "render").exit_code == 0
    assert path.read_bytes() == original
    base = commit(repo, "grouped claims")
    load_project(repo, base, "host")
    manifest["review"]["groups"].reverse()
    write_contract(repo, manifest)
    assert invoke(repo, "check").exit_code == 1
    assert invoke(repo, "check", "--base", base).exit_code == 0
    assert invoke(repo, "render").exit_code == 0
    assert path.read_bytes() != original
    load_project(repo, base, "host")


@pytest.mark.parametrize(
    "groups",
    [
        [],
        [{"title": "A", "claim_ids": ["claim", "claim", "other"]}],
        [{"title": "A", "claim_ids": ["claim", "missing", "other"]}],
        [{"title": "A", "claim_ids": ["claim"]}],
        [{"title": "", "claim_ids": ["claim", "other"]}],
        [{"title": " ", "claim_ids": ["claim", "other"]}],
        [{"title": "Same", "claim_ids": ["claim"]}, {"title": "Same", "claim_ids": ["other"]}],
        [{"title": "Verification coverage and limits", "claim_ids": ["claim", "other"]}],
        [{"title": "What the app does", "claim_ids": ["claim", "other"]}],
    ],
)
def test_invalid_group_partition_never_overwrites(project_repo, groups):
    repo, _, manifest = project_repo
    assert invoke(repo, "render").exit_code == 0
    path = repo / ".mentat/claims.md"
    original = path.read_bytes()
    manifest = grouped_manifest(manifest)
    manifest["review"]["groups"] = groups
    write_contract(repo, manifest)
    result = invoke(repo, "render", "--json")
    assert result.exit_code == 2, result.output
    assert path.read_bytes() == original


def test_grouped_standalone_and_optional_coverage_limits(project_repo):
    repo, _, manifest = project_repo
    manifest = grouped_manifest(manifest)
    del manifest["review"]["verification_limits"]
    del manifest["behavior"][0]["selectors"]
    manifest["behavior"][0]["argv"].remove("{selectors}")
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    text = (repo / ".mentat/claims.md").read_text()
    assert "### Coverage limits" not in text
    assert "Check `behavior` · Claim `claim` · Standalone check" in text
    assert text.rstrip().endswith(
        "Inventory · 2 behavior checks · 1 formal check · 0 declared selectors"
    )


def test_grouped_multiline_bullets_stay_single_paragraphs(project_repo):
    repo, _, manifest = project_repo
    manifest = grouped_manifest(manifest)
    manifest["review"]["scope_limits"] = ["Only recorded cases.\n\nGaps remain."]
    manifest["review"]["verification_limits"] = ["Only synthetic cases.\n\nProduction is excluded."]
    manifest["claims"][0]["assumptions"] = ["Given a valid input.\r\nThe clock is fixed."]
    write_contract(repo, manifest)
    result = invoke(repo, "render")
    assert result.exit_code == 0, result.output
    text = (repo / ".mentat/claims.md").read_text()
    assert "- Only recorded cases. Gaps remain.\n" in text
    assert "- Only synthetic cases. Production is excluded.\n" in text
    assert "- Assumption Given a valid input. The clock is fixed.\n" in text
