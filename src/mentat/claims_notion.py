"""Compare or publish a Claims Spec on a single Notion page."""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

_START = "Source: working-tree draft"
_END = "---"
_SOURCE = re.compile(r"Source commit: `(?:[0-9a-f]{40}|[0-9a-f]{64})` · ([1-9][0-9]{0,2}) claims?")
_DRAFT = re.compile(r"Source: working-tree draft · ([1-9][0-9]{0,2}) claims?")
_PAGE_ID = r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_LIMIT = 4_000_000
_CODE = re.compile(r"<code>([A-Za-z0-9_.-]+)</code>")
_CLAIM_HEADING = re.compile(r"## (?:[A-Za-z0-9_.-]|\\_)+")
_CHECK_IDS = r"`[A-Za-z0-9_][A-Za-z0-9_.-]*`(?:, `[A-Za-z0-9_][A-Za-z0-9_.-]*`)*"
_FORMAL_ROW = re.compile(r"- Formal checks: (?:None|" + _CHECK_IDS + ")")
_BEHAVIOR_ROW = re.compile(r"- Behavior checks: " + _CHECK_IDS)
_INTRO = "Generated from `contract.json`. Edit the contract and regenerate."
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CONTAINER = re.compile(
    r"^\s*<(\/?)(callout|column|columns|details|li|mail|meeting-notes|ol|p|quote|"
    r"synced-block(?:-reference)?|tab|tabs|toggle|ul)(?=[\s>/])"
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _page_id(page: str) -> str:
    if re.fullmatch(_PAGE_ID, page):
        return str(UUID(page))
    url = urlsplit(page)
    if any(key == "p" for key, _ in parse_qsl(url.query, keep_blank_values=True)):
        raise ValueError("provide a direct Notion page URL or page ID for a peek link")
    host = url.hostname or ""
    if (
        url.scheme == "https"
        and (
            host in {"notion.so", "www.notion.so", "app.notion.com", "notion.site"}
            or host.endswith(".notion.site")
        )
        and url.netloc == host
    ):
        segment = url.path.rstrip("/").rsplit("/", 1)[-1]
        match = re.search(r"(?:^|-)(" + _PAGE_ID + r")\Z", segment)
        if match:
            return str(UUID(match[1]))
    raise ValueError("provide a Notion page URL or page ID")


def _request(page_id: str, payload: dict | None = None) -> str:
    token = os.environ.get("MENTAT_NOTION_TOKEN")
    if not token or not token.isascii() or not token.isprintable() or " " in token:
        raise ValueError("set MENTAT_NOTION_TOKEN to a Notion connection token")
    url = f"https://api.notion.com/v1/pages/{page_id}/markdown"
    if payload is None:
        url += "?markdown_version=v2"
    request = Request(
        url,
        data=None
        if payload is None
        else json.dumps({**payload, "markdown_version": "v2"}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2026-03-11",
            "Content-Type": "application/json",
        },
        method="GET" if payload is None else "PATCH",
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(_LIMIT + 1)
    except HTTPError as exc:
        raise ValueError(
            f"Notion API returned HTTP {exc.code}; check page access or retry later"
        ) from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Notion API request failed; check page status before retrying") from None
    if len(raw) > _LIMIT:
        raise ValueError("Notion response exceeded the size limit")
    body = json.loads(raw)
    if (
        not isinstance(body, dict)
        or body.get("object") != "page_markdown"
        or body.get("id") != page_id
        or body.get("truncated") is not False
        or body.get("unknown_block_ids")
        or body.get("warnings")
        or not isinstance(body.get("markdown"), str)
    ):
        raise ValueError("Notion returned incomplete page content or a warning")
    return body["markdown"]


def _section(markdown: str) -> str | None:
    starts, ends = [], []
    fence = ""
    containers = []
    offset = 0
    for raw in markdown.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        candidate = _FENCE.match(line)
        container = _CONTAINER.match(line)
        if fence:
            if candidate and candidate[1].startswith(fence) and not candidate[2].strip():
                fence = ""
        elif candidate:
            fence = candidate[1]
        elif container:
            closing, name = container.groups()
            if closing and containers and containers[-1] == name:
                containers.pop()
            elif not closing and not line.rstrip().endswith(("/>", f"</{name}>")):
                containers.append(name)
        elif not containers:
            if (
                line == _START
                or _DRAFT.fullmatch(line)
                or _SOURCE.fullmatch(_CODE.sub(r"`\1`", line))
            ):
                starts.append(offset)
            elif line == _END and starts and not ends:
                ends.append(offset + len(line))
        offset += len(raw)
    if not starts:
        return None
    if not ends or starts[-1] >= ends[0]:
        raise ValueError("Notion page has ambiguous Claims Spec section boundaries")
    section = markdown[starts[0] : ends[0]]
    lines = _normalized(section.removesuffix(_END))
    if lines != [_START]:
        if lines[0] == _START:
            raise ValueError("Notion page has ambiguous Claims Spec section boundaries")
        provenance = _SOURCE.fullmatch(lines[0]) or _DRAFT.fullmatch(lines[0])
        if provenance is None or not _has_claims_layout(lines[1:], int(provenance[1])):
            raise ValueError("the Notion divider must immediately follow the Claims Spec")
    return section


def _body(claims: bytes) -> str:
    return claims.decode().split("\n", 1)[1].strip("\n")


def _normalized(markdown: str) -> list[str]:
    # Notion can reserialize inline code and inter-block spacing.
    markdown = _CODE.sub(r"`\1`", markdown)
    return [line for line in markdown.splitlines() if line.strip()]


def _has_claims_layout(lines: list[str], count: int) -> bool:
    if not lines or lines[0] != _INTRO:
        return False
    headings = [i for i, line in enumerate(lines) if _CLAIM_HEADING.fullmatch(line)]
    if len(headings) != count or headings[0] != 1:
        return False
    for start, end in zip(headings, [*headings[1:], len(lines)], strict=True):
        if (
            end - start < 4
            or not _FORMAL_ROW.fullmatch(lines[end - 2])
            or not _BEHAVIOR_ROW.fullmatch(lines[end - 1])
            or any(_BEHAVIOR_ROW.fullmatch(line) for line in lines[start + 1 : end - 1])
        ):
            return False
    return True


def _matches(section: str | None, claims: bytes) -> bool:
    if section is None:
        return False
    section = section.replace("\r\n", "\n")
    source, _, body = section.partition("\n\n")
    return bool(_SOURCE.fullmatch(_CODE.sub(r"`\1`", source))) and _normalized(
        body.removesuffix(_END)
    ) == _normalized(_body(claims))


def notion_claims(page: str, claims: bytes, base: str, *, publish: bool = False) -> bool:
    page_id = _page_id(page)
    current = _section(_request(page_id))
    if _matches(current, claims):
        return True
    if not publish:
        return False

    body = _body(claims)
    count = sum(bool(_CLAIM_HEADING.fullmatch(line)) for line in body.splitlines())
    label = "claim" if count == 1 else "claims"
    desired = f"Source commit: `{base}` · {count} {label}\n\n{body}\n\n{_END}"
    if current is None:
        raise ValueError(
            "initialize the Notion page with a 'Source: working-tree draft' paragraph "
            "followed by a divider before publishing"
        )
    if _section(desired) != desired:
        raise ValueError("generated Claims Spec has ambiguous Notion section boundaries")
    payload = {
        "type": "update_content",
        "update_content": {"content_updates": [{"old_str": current, "new_str": desired}]},
    }
    try:
        _request(page_id, payload)
    except ValueError:
        if _matches(_section(_request(page_id)), claims):
            return True
        raise
    if not _matches(_section(_request(page_id)), claims):
        raise ValueError("Notion content did not match the Claims Spec after publication")
    return True
