import pytest

from mentat import claims_notion as notion

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
