import http.client

import pytest
from helpers import commit
from typer.testing import CliRunner

from mentat import claims_notion as notion
from mentat import cli

pytest_plugins = ["test_project"]

PAGE = "41afbf15-6f89-80a5-854a-c5fc7b82e923"
URL = "https://app.notion.com/p/Mentat-Claims-Spec-41afbf156f8980a5854ac5fc7b82e923"
BASE = "a" * 40
CLAIMS = (
    "# Claims Spec\n\n"
    f"{notion._INTRO}\n\n## claim.id\n\nThe actual claim.\n\n"
    "- Formal checks: None\n- Behavior checks: `check.id`\n"
).encode()


def test_seed_publish_check_and_noop(monkeypatch):
    contents = f"Unrelated content\n\n---\n\n{notion._START}\n\n{notion._END}\n\nOther notes\n\n---"
    writes = []

    def request(page_id, payload=None):
        nonlocal contents
        assert page_id == PAGE
        if payload:
            writes.append(payload)
            edit = payload["update_content"]["content_updates"][0]
            assert contents.count(edit["old_str"]) == 1
            contents = contents.replace(edit["old_str"], edit["new_str"], 1)
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert not notion.notion_claims(URL, CLAIMS, BASE)
    assert notion.notion_claims(URL, CLAIMS, BASE, publish=True)
    assert f"Source commit: `{BASE}`" in contents
    assert notion.notion_claims(PAGE, CLAIMS, BASE, publish=True)
    assert len(writes) == 1
    revised = CLAIMS.replace(b"actual", b"updated")
    assert not notion.notion_claims(URL, revised, BASE)
    assert notion.notion_claims(URL, revised, BASE, publish=True)
    assert len(writes) == 2
    assert contents.startswith("Unrelated content\n")
    assert contents.endswith("Other notes\n\n---")
    assert "The updated claim." in contents
    assert "Mentat Claims Spec" not in contents


@pytest.mark.parametrize(
    "markdown",
    [notion._START, f"{notion._END}\n{notion._START}", f"{notion._START}\n{notion._START}\n---"],
)
def test_ambiguous_page_never_writes(monkeypatch, markdown):
    def request(page_id, payload=None):
        assert payload is None
        return markdown

    monkeypatch.setattr(notion, "_request", request)
    with pytest.raises(ValueError, match="ambiguous"):
        notion.notion_claims(PAGE, CLAIMS, BASE, publish=True)


def test_concurrent_edit_is_not_retried(monkeypatch):
    calls = []
    original = f"{notion._START} · 1 claims\n\n{notion._body(CLAIMS)}\n\n{notion._END}"

    def request(page_id, payload=None):
        calls.append(payload)
        if payload:
            assert payload["update_content"]["content_updates"][0]["old_str"] == original
            raise ValueError("edit no longer matches")
        return original if len(calls) == 1 else original.replace("actual claim", "concurrent edit")

    monkeypatch.setattr(notion, "_request", request)
    with pytest.raises(ValueError, match="no longer matches"):
        notion.notion_claims(PAGE, CLAIMS, BASE, publish=True)
    assert len(calls) == 3


def test_unknown_write_result_is_read_back_before_retry(monkeypatch):
    contents = f"{notion._START}\n\n{notion._END}"

    def request(page_id, payload=None):
        nonlocal contents
        if payload:
            contents = payload["update_content"]["content_updates"][0]["new_str"]
            raise ValueError("connection lost")
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, CLAIMS, BASE, publish=True)


def test_notion_round_trip_inline_code(monkeypatch):
    body = notion._body(CLAIMS).replace("`check.id`", "<code>check.id</code>")
    contents = f"Source commit: <code>{BASE}</code> · 1 claims\n\n{body}\n\n{notion._END}"
    monkeypatch.setattr(notion, "_request", lambda page_id: contents)
    assert notion.notion_claims(PAGE, CLAIMS, BASE, publish=True)


@pytest.mark.parametrize(
    "wrapper",
    ["{}", " ```md\n{}\n```", "<toggle>\n{}\n</toggle>", "<synced-block>\n{}\n</synced-block>"],
)
def test_never_append_or_replace_example_markers(monkeypatch, wrapper):
    section = f"{notion._START}\n\n{notion._END}" if wrapper != "{}" else ""

    def request(page_id, payload=None):
        assert payload is None
        return wrapper.format(section)

    monkeypatch.setattr(notion, "_request", request)
    assert not notion.notion_claims(PAGE, CLAIMS, BASE)
    with pytest.raises(ValueError, match="initialize"):
        notion.notion_claims(PAGE, CLAIMS, BASE, publish=True)


def test_missing_literal_markdown_is_drift(monkeypatch):
    claims = CLAIMS.replace(b"The actual claim.", b"Use *name*.")
    contents = f"Source commit: `{BASE}` · 1 claims\n\n{notion._body(claims)}\n\n{notion._END}"
    monkeypatch.setattr(notion, "_request", lambda page_id: contents)
    expected = claims.replace(b"*name*", b"\\*name\\*")
    assert not notion.notion_claims(PAGE, expected, BASE)


def test_unrelated_divider_is_not_used_when_section_end_goes_missing(monkeypatch):
    markdown = (
        f"{notion._START} · 1 claims\n\n{notion._body(CLAIMS)}\n\n"
        "## rollout\n\nRelease notes\n\n- Formal checks: None\n- Behavior checks: `smoke`\n\n---"
    )

    def request(page_id, payload=None):
        assert payload is None
        return markdown

    monkeypatch.setattr(notion, "_request", request)
    with pytest.raises(ValueError, match="immediately follow"):
        notion.notion_claims(PAGE, CLAIMS, BASE, publish=True)


def test_source_label_is_allowed_as_literal_claim_text(monkeypatch):
    contents = f"{notion._START}\n\n{notion._END}"

    def request(page_id, payload=None):
        nonlocal contents
        if payload:
            contents = payload["update_content"]["content_updates"][0]["new_str"]
        return contents

    monkeypatch.setattr(notion, "_request", request)
    claims = CLAIMS.replace(b"The actual claim.", notion._START.encode())
    assert notion.notion_claims(PAGE, claims, BASE, publish=True)
    assert notion.notion_claims(PAGE, claims, BASE)


def test_second_source_range_on_same_page_is_rejected(monkeypatch):
    body = f"Source commit: `{BASE}` · 1 claim\n\n{notion._body(CLAIMS)}\n\n---"

    def request(page_id, payload=None):
        assert payload is None
        return f"{body}\n\n{body}"

    monkeypatch.setattr(notion, "_request", request)
    with pytest.raises(ValueError, match="ambiguous"):
        notion.notion_claims(PAGE, CLAIMS, BASE, publish=True)


def test_auth_and_target_validation(monkeypatch):
    monkeypatch.delenv("MENTAT_NOTION_TOKEN", raising=False)
    with pytest.raises(ValueError, match="MENTAT_NOTION_TOKEN"):
        notion.notion_claims(PAGE, CLAIMS, BASE)
    for target in (f"https://external.invalid/{PAGE}", f"https://user@app.notion.com/{PAGE}"):
        with pytest.raises(ValueError, match="URL or page ID"):
            notion.notion_claims(target, CLAIMS, BASE, publish=True)
    with pytest.raises(ValueError, match="direct Notion page URL"):
        notion.notion_claims(f"{URL}?p={'b' * 32}", CLAIMS, BASE, publish=True)


NUMBERED = (
    "# Claims Spec\n\n"
    "## Overview\n\nA concise overview.\n\n"
    "## Scope limits\n\n- Only supported inputs.\n\n"
    "## Examples\n\n- [Example](https://example.com/demo) — A useful example.\n\n"
    "## Claims\n\n### 1. Validate the candidate\n\nThe actual claim.\n\n"
    "Claim `claim.id` · Behavior `check.id` · Formal `proof.id`\n"
).encode()


@pytest.mark.parametrize("initial", ["seed", "legacy"])
def test_numbered_publish_migrates_existing_section_and_noops(monkeypatch, initial):
    section = (
        f"{notion._START}\n\n{notion._END}"
        if initial == "seed"
        else f"Source commit: `{BASE}` · 1 claim\n\n{notion._body(CLAIMS)}\n\n{notion._END}"
    )
    contents = f"Before notes\n\n{section}\n\nAfter notes"
    writes = []

    def request(page_id, payload=None):
        nonlocal contents
        if payload:
            writes.append(payload)
            edit = payload["update_content"]["content_updates"][0]
            assert contents.count(edit["old_str"]) == 1
            contents = contents.replace(edit["old_str"], edit["new_str"], 1)
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, NUMBERED, BASE, publish=True)
    assert contents.startswith("Before notes\n\n")
    assert contents.endswith("\n\nAfter notes")
    assert "### 1. Validate the candidate" in contents
    assert notion.notion_claims(PAGE, NUMBERED, BASE, publish=True)
    assert len(writes) == 1


def test_numbered_publish_adds_selectors_and_reads_them_back(monkeypatch):
    old = f"Source commit: `{BASE}` · 1 claim\n\n{notion._body(NUMBERED)}\n\n{notion._END}"
    contents = f"Before\n\n{old}\n\nAfter"
    updated = NUMBERED.replace(
        b"Claim `claim.id`",
        b"- Check `check.id` selects test\\_app.py::test\\_case\n\nClaim `claim.id`",
    )
    new_base = "b" * 40

    def request(page_id, payload=None):
        nonlocal contents
        if payload:
            edit = payload["update_content"]["content_updates"][0]
            assert edit["old_str"] == old
            contents = contents.replace(
                old, edit["new_str"].replace("`check.id`", "<code>check.id</code>")
            )
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert not notion.notion_claims(PAGE, updated, new_base)
    assert notion.notion_claims(PAGE, updated, new_base, publish=True)
    assert notion.notion_claims(PAGE, updated, new_base)
    assert "selects `test_app.py::test_case`" in contents
    assert contents.startswith("Before\n\n") and contents.endswith("\n\nAfter")


@pytest.mark.parametrize("line_end", ["\n", "\r\n", "\r"])
def test_numbered_round_trip_inline_code_and_link(monkeypatch, line_end):
    body = notion._body(NUMBERED).replace("`claim.id`", "<code>claim.id</code>")
    body = body.replace("`check.id`", "<code>check.id</code>").replace(
        "`proof.id`", "<code>proof.id</code>"
    )
    contents = f"Source commit: <code>{BASE}</code> · 1 claim\n\n{body}\n\n{notion._END}"
    contents = contents.replace("\n\n", "\n").replace("\n", line_end)

    def request(page_id, payload=None):
        assert payload is None
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, NUMBERED, BASE, publish=True)


def test_numbered_unicode_separator_inside_prose_does_not_create_headings(monkeypatch):
    overview = "A concise overview.\u2028## Examples\u2028### 1. This is still the overview."
    claims = NUMBERED.replace(b"A concise overview.", overview.encode())
    contents = f"{notion._START}\n\n{notion._END}"

    def request(page_id, payload=None):
        nonlocal contents
        if payload:
            edit = payload["update_content"]["content_updates"][0]
            assert edit["old_str"] == contents
            contents = edit["new_str"]
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, claims, BASE, publish=True)
    assert overview in contents
    assert contents.startswith(f"Source commit: `{BASE}` · 1 claim\n")
    assert notion.notion_claims(PAGE, claims, BASE)


@pytest.mark.parametrize(
    "extra",
    [
        "\n\n## Unrelated\n\nDo not delete this.",
        "\n\n## Claims\n\n### 2. Extra claim\n\nExtra.\n\nClaim `extra` · Behavior `extra.check`",
    ],
)
def test_numbered_unknown_content_before_divider_is_never_replaced(monkeypatch, extra):
    contents = (
        f"Source commit: `{BASE}` · 1 claim\n\n{notion._body(NUMBERED)}{extra}\n\n{notion._END}"
    )

    def request(page_id, payload=None):
        assert payload is None
        return contents

    monkeypatch.setattr(notion, "_request", request)
    with pytest.raises(ValueError):
        notion.notion_claims(PAGE, NUMBERED.replace(b"actual", b"updated"), BASE, publish=True)


def test_numbered_duplicate_sections_are_never_replaced(monkeypatch):
    section = f"Source commit: `{BASE}` · 1 claim\n\n{notion._body(NUMBERED)}\n\n{notion._END}"

    def request(page_id, payload=None):
        assert payload is None
        return f"{section}\n\n{section}"

    monkeypatch.setattr(notion, "_request", request)
    with pytest.raises(ValueError, match="ambiguous"):
        notion.notion_claims(PAGE, NUMBERED, BASE, publish=True)


def test_numbered_concurrent_edit_is_not_retried(monkeypatch):
    original = f"Source commit: `{BASE}` · 1 claim\n\n{notion._body(NUMBERED)}\n\n{notion._END}"
    calls = []

    def request(page_id, payload=None):
        calls.append(payload)
        if payload:
            assert payload["update_content"]["content_updates"][0]["old_str"] == original
            raise ValueError("edit no longer matches")
        return original if len(calls) == 1 else original.replace("actual claim", "concurrent edit")

    monkeypatch.setattr(notion, "_request", request)
    with pytest.raises(ValueError, match="no longer matches"):
        notion.notion_claims(PAGE, NUMBERED.replace(b"actual", b"updated"), BASE, publish=True)
    assert len(calls) == 3


def test_numbered_lost_write_response_reads_back_without_retry(monkeypatch):
    contents = f"{notion._START}\n\n{notion._END}"
    writes = []

    def request(page_id, payload=None):
        nonlocal contents
        if payload:
            writes.append(payload)
            contents = payload["update_content"]["content_updates"][0]["new_str"]
            raise ValueError("connection lost")
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, NUMBERED, BASE, publish=True)
    assert len(writes) == 1


GROUPED = (
    "# Claims Spec\n\n## What the app does\n\nA concise overview.\n\n"
    "## Product limits\n\n- Only supported inputs.\n\n"
    "## Capability\n\n### 1. Validate the candidate\n\nThe actual claim.\n\n"
    "Claim `claim.id` · Behavior `check.id` · Formal `proof.id`\n\n"
    "## Verification coverage and limits\n\n### Behavior checks\n\n"
    "Check `check.id` · Claim `claim.id`\n\n- Selector `tests/test_app.py::test_case[one]`\n\n"
    "### Formal checks and assumptions\n\nFormal check `proof.id` · Claim `claim.id` · Z3\n\n"
    "Inventory · 1 behavior check · 1 formal check · 1 declared selector\n"
).encode()


@pytest.mark.parametrize("prior", [CLAIMS, NUMBERED])
def test_grouped_migrates_prior_layout_preserving_notes_and_noops(monkeypatch, prior):
    section = f"Source commit: `{BASE}` · 1 claim\n\n{notion._body(prior)}\n\n{notion._END}"
    contents = f"Before notes\n\n{section}\n\nAfter notes"
    writes = []

    def request(page_id, payload=None):
        nonlocal contents
        if payload:
            writes.append(payload)
            edit = payload["update_content"]["content_updates"][0]
            assert contents.count(edit["old_str"]) == 1
            contents = contents.replace(edit["old_str"], edit["new_str"], 1)
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, GROUPED, BASE, publish=True)
    assert contents.startswith("Before notes\n\n") and contents.endswith("\n\nAfter notes")
    assert "## Verification coverage and limits" in contents
    assert notion.notion_claims(PAGE, GROUPED, BASE, publish=True)
    assert len(writes) == 1


def test_grouped_accepts_exact_notion_code_selector_but_not_changed_selector(monkeypatch):
    body = notion._body(GROUPED).replace(
        "`tests/test_app.py::test_case[one]`", "<code>tests/test_app.py::test_case[one]</code>"
    )
    contents = f"Source commit: `{BASE}` · 1 claim\n\n{body}\n\n{notion._END}"
    monkeypatch.setattr(notion, "_request", lambda page_id: contents)
    assert notion.notion_claims(PAGE, GROUPED, BASE)
    contents = contents.replace("test_case[one]", "test_case[two]")
    assert not notion.notion_claims(PAGE, GROUPED, BASE)


def test_grouped_trailing_notes_inside_section_are_not_replaced(monkeypatch):
    contents = (
        f"Source commit: `{BASE}` · 1 claim\n\n{notion._body(GROUPED)}"
        f"\n\n## Release notes\n\nDo not delete.\n\n{notion._END}"
    )

    def request(page_id, payload=None):
        assert payload is None
        return contents

    monkeypatch.setattr(notion, "_request", request)
    with pytest.raises(ValueError):
        notion.notion_claims(PAGE, GROUPED.replace(b"actual", b"updated"), BASE, publish=True)


def test_grouped_lost_write_response_is_read_back_without_retry(monkeypatch):
    contents = f"{notion._START}\n\n{notion._END}"
    writes = []

    def request(page_id, payload=None):
        nonlocal contents
        if payload:
            writes.append(payload)
            contents = payload["update_content"]["content_updates"][0]["new_str"]
            raise ValueError("connection lost")
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, GROUPED, BASE, publish=True)
    assert len(writes) == 1


def test_grouped_concurrent_edit_is_not_retried(monkeypatch):
    original = f"Source commit: `{BASE}` · 1 claim\n\n{notion._body(GROUPED)}\n\n{notion._END}"
    calls = []

    def request(page_id, payload=None):
        calls.append(payload)
        if payload:
            assert payload["update_content"]["content_updates"][0]["old_str"] == original
            raise ValueError("edit no longer matches")
        return original if len(calls) == 1 else original.replace("actual claim", "concurrent edit")

    monkeypatch.setattr(notion, "_request", request)
    with pytest.raises(ValueError, match="no longer matches"):
        notion.notion_claims(PAGE, GROUPED.replace(b"actual", b"updated"), BASE, publish=True)
    assert len(calls) == 3


def test_grouped_selector_link_is_not_equivalent_to_code(monkeypatch):
    body = notion._body(GROUPED).replace(
        "`tests/test_app.py::test_case[one]`",
        "[tests/test_app.py::test_case[one]](https://example.com)",
    )
    contents = f"Source commit: `{BASE}` · 1 claim\n\n{body}\n\n{notion._END}"
    monkeypatch.setattr(notion, "_request", lambda page_id: contents)
    assert not notion.notion_claims(PAGE, GROUPED, BASE)


def test_historical_notion_angle_escape_is_a_noop(monkeypatch):
    spec = NUMBERED.replace(
        b"Claim `claim.id`",
        rb"- Check `check.id` selects test\_app.py::test\_case\[\<value\>\]"
        b"\n\nClaim `claim.id`",
    )
    body = (
        notion._body(spec)
        .replace(r"\<value\>", r"\<value>")
        .replace("`check.id`", "<code>check.id</code>")
    )
    contents = f"Source commit: `{BASE}` · 1 claim\n\n{body}\n\n{notion._END}"

    def request(page_id, payload=None):
        assert payload is None
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, spec, BASE, publish=True)
    contents = contents.replace(r"\<value>", "<https://example.com>")
    assert not notion.notion_claims(PAGE, spec, BASE)


def test_grouped_selector_accepts_equivalent_backtick_delimiters(monkeypatch):
    body = notion._body(GROUPED).replace(
        "`tests/test_app.py::test_case[one]`", "``tests/test_app.py::test_case[one]``"
    )
    contents = f"Source commit: `{BASE}` · 1 claim\n\n{body}\n\n{notion._END}"
    monkeypatch.setattr(notion, "_request", lambda page_id: contents)
    assert notion.notion_claims(PAGE, GROUPED, BASE)
    contents = contents.replace("case[one]", "case``[one]")
    assert not notion.notion_claims(PAGE, GROUPED, BASE)


def test_grouped_notion_prose_angle_escaping_is_a_noop(monkeypatch):
    claims = GROUPED.replace(
        b"### Behavior checks",
        rb"### Coverage limits"
        b"\n\n"
        rb"- Only samples inside \<window\> are checked when count \> 0."
        b"\n\n"
        b"### Behavior checks",
    )
    body = notion._body(claims).replace(r"\>", ">")
    contents = f"Source commit: `{BASE}` · 1 claim\n\n{body}\n\n{notion._END}"

    def request(page_id, payload=None):
        assert payload is None
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, claims, BASE, publish=True)
    contents = contents.replace(r"\<window>", "<https://example.com>")
    assert not notion.notion_claims(PAGE, claims, BASE)


@pytest.mark.parametrize("prefix", ["", "- "])
def test_notion_prose_does_not_match_a_quote_block(monkeypatch, prefix):
    claims = GROUPED.replace(b"The actual claim.", f"{prefix}\\> A literal comparison.".encode())
    body = notion._body(claims).replace(r"\>", ">")
    contents = f"Source commit: `{BASE}` · 1 claim\n\n{body}\n\n{notion._END}"
    monkeypatch.setattr(notion, "_request", lambda page_id: contents)
    assert not notion.notion_claims(PAGE, claims, BASE)


@pytest.mark.parametrize("layout", ["legacy", "numbered", "grouped"])
@pytest.mark.parametrize("named_cases", [True, False])
def test_publish_claim_citations_and_control_inventory(monkeypatch, layout, named_cases):
    import re

    from mentat.claims import render_claims
    from mentat.project import Contract

    case = "test_app.py::test_case[a > b]"
    behavior = {"id": "check.id", "claim": "claim.id", "argv": ["test"]}
    control = {"args": ["bad"], "violation_exit_code": 1}
    if named_cases:
        behavior.update(
            artifacts=["cases.json"],
            case_report={
                "artifact": "cases.json",
                "required_cases": [case],
            },
        )
        control["failing_case"] = case
    behavior["negative_control"] = control
    claim = {
        "id": "claim.id",
        "title": "Claim with citations",
        "statement": "The observed fixture passes.",
        "provenance": {
            "origin": "declared",
            "citations": [
                {
                    "commit": BASE,
                    "blob": "b" * 40,
                    "path": "openspec/specs/cap > item/spec.md",
                    "start_line": 6,
                    "end_line": 10,
                }
            ],
        },
    }
    manifest = {
        "version": 1,
        "claims": [claim],
        "behavior": [behavior],
        "formal": [
            {
                "id": "proof.id",
                "claim": "claim.id",
                "engine": "z3",
                "source": "model.smt2",
                "expected": "sat",
            }
        ],
        "allow_host": True,
    }
    if layout != "legacy":
        review = {"overview": "This is what the app does.", "scope_limits": ["One fixture."]}
        if layout == "grouped":
            review["groups"] = [{"title": "Check the fixture", "claim_ids": ["claim.id"]}]
        manifest["review"] = review
    content = render_claims(Contract.model_validate(manifest))
    original = f"Notes before\n\n{notion._START}\n\n---\n\nNotes after"
    current = original
    writes = []

    def request(page_id, payload=None):
        nonlocal current
        assert page_id == PAGE
        if payload:
            writes.append(payload)
            edit = payload["update_content"]["content_updates"][0]
            assert current.count(edit["old_str"]) == 1
            # Notion may return inline code as HTML rather than backticks.
            readback = re.sub(r"`([^`]+)`", r"<code>\1</code>", edit["new_str"])
            current = current.replace(edit["old_str"], readback, 1)
        return current

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, content, BASE, publish=True)
    assert notion.notion_claims(PAGE, content, BASE)
    assert notion.notion_claims(PAGE, content, BASE, publish=True)
    assert len(writes) == 1
    assert current.startswith("Notes before\n") and current.endswith("\nNotes after")
    assert "- Origin · declared" in current
    assert "<code>openspec/specs/cap > item/spec.md:6-10</code>" in current
    if layout == "grouped":
        assert "- Negative control requires exit <code>1</code>" in current
        if named_cases:
            assert f"- Required case <code>{case}</code>" in current
        assert "0 declared selectors" in current


@pytest.mark.parametrize("with_selector", [False, True])
def test_generated_host_environment_publishes_and_round_trips(monkeypatch, with_selector):
    import re

    from mentat.claims import render_claims
    from mentat.project import Contract

    behavior = {
        "id": "check.id",
        "claim": "claim.id",
        "argv": ["test"],
        "host_environment": ["NPM_CONFIG_USERCONFIG", "CA_FILE"],
    }
    if with_selector:
        behavior.update(argv=["test", "{selectors}"], selectors=["test_case"])
    manifest = {
        "version": 1,
        "claims": [{"id": "claim.id", "title": "Test", "statement": "The check passes."}],
        "behavior": [behavior],
        "formal": [
            {
                "id": "proof.id",
                "claim": "claim.id",
                "engine": "z3",
                "source": "model.smt2",
                "expected": "sat",
            }
        ],
        "allow_host": True,
        "review": {
            "overview": "Overview.",
            "scope_limits": ["One fixture."],
            "groups": [{"title": "Capability", "claim_ids": ["claim.id"]}],
        },
    }
    claims = render_claims(Contract.model_validate(manifest))
    contents = f"Before notes\n\n{notion._START}\n\n{notion._END}\n\nAfter notes"
    writes = []

    def request(page_id, payload=None):
        nonlocal contents
        assert page_id == PAGE
        if payload:
            edit = payload["update_content"]["content_updates"][0]
            writes.append(edit)
            assert contents.count(edit["old_str"]) == 1
            readback = re.sub(r"`([^`]+)`", r"<code>\1</code>", edit["new_str"])
            contents = contents.replace(edit["old_str"], readback, 1)
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, claims, BASE, publish=True)
    assert "- Host environment <code>NPM_CONFIG_USERCONFIG</code>" in contents
    assert " · Standalone check" not in contents
    assert notion.notion_claims(PAGE, claims, BASE)
    assert notion.notion_claims(PAGE, claims, BASE, publish=True)
    assert len(writes) == 1
    assert contents.startswith("Before notes\n") and contents.endswith("\nAfter notes")
    contents = contents.replace("<code>CA_FILE</code>", "<code>OTHER_FILE</code>")
    assert not notion.notion_claims(PAGE, claims, BASE)
    assert notion.notion_claims(PAGE, claims, BASE, publish=True)
    assert len(writes) == 2
    contents = contents.replace(
        "### Formal checks and assumptions",
        "- Unexpected operator content\n\n### Formal checks and assumptions",
    )
    with pytest.raises(ValueError):
        notion.notion_claims(PAGE, claims, BASE, publish=True)
    assert len(writes) == 2


def test_grouped_existing_standalone_document_remains_unchanged(monkeypatch):
    claims = GROUPED.replace(
        "Check `check.id` · Claim `claim.id`\n\n- Selector `tests/test_app.py::test_case[one]`".encode(),
        "Check `check.id` · Claim `claim.id` · Standalone check".encode(),
    ).replace(b"1 declared selector", b"0 declared selectors")
    body = notion._body(claims).replace("`check.id`", "<code>check.id</code>")
    contents = f"Source commit: `{BASE}` · 1 claim\n\n{body}\n\n{notion._END}"

    def request(page_id, payload=None):
        assert payload is None
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, claims, BASE, publish=True)


def test_legacy_claim_id_with_trailing_dot_publishes(monkeypatch):
    claims = CLAIMS.replace(b"## claim.id", b"## 7\\.")
    contents = notion._START + "\n\n---"

    def request(page_id, payload=None):
        nonlocal contents
        if payload:
            edit = payload["update_content"]["content_updates"][0]
            assert contents.count(edit["old_str"]) == 1
            contents = contents.replace(edit["old_str"], edit["new_str"], 1)
        return contents

    monkeypatch.setattr(notion, "_request", request)
    assert notion.notion_claims(PAGE, claims, BASE, publish=True)
    assert "## 7\\." in contents
    assert notion.notion_claims(PAGE, claims, BASE)


class _ReplyRefused:
    def __init__(self, error):
        self.error = error

    def open(self, request, timeout=None):
        raise self.error


@pytest.mark.parametrize(
    "error",
    [http.client.BadStatusLine("NOT-HTTP reply"), http.client.IncompleteRead(b"{")],
)
def test_non_http_notion_reply_is_user_error(project_repo, monkeypatch, error):
    repo, _, _ = project_repo
    assert CliRunner().invoke(cli.app, ["claims", "render", "--repo", str(repo)]).exit_code == 0
    base = commit(repo, "add claims")
    monkeypatch.setenv("MENTAT_NOTION_TOKEN", "test-token")
    monkeypatch.setattr(notion, "build_opener", lambda *handlers: _ReplyRefused(error))
    result = CliRunner().invoke(
        cli.app, ["claims", "check", "--repo", str(repo), "--base", base, "--notion", PAGE]
    )
    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit), repr(result.exception)
