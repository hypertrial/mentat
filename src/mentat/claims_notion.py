"""Compare or publish a Claims Spec on a single Notion page."""

import json
import os
import re
from collections.abc import Iterator
from io import StringIO
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from .claims import code_span, escape_prose

_START = "Source: working-tree draft"
_END = "---"
_SOURCE = re.compile(r"Source commit: `(?:[0-9a-f]{40}|[0-9a-f]{64})` · ([1-9][0-9]{0,2}) claims?")
_DRAFT = re.compile(r"Source: working-tree draft · ([1-9][0-9]{0,2}) claims?")
_PAGE_ID = r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_LIMIT = 4_000_000
_CODE = re.compile(r"<code>([A-Za-z0-9_.\'-]+)</code>")
_CLAIM_HEADING = re.compile(r"## (?:[A-Za-z0-9_.-]|\\_)+")
_ID = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_CHECK_IDS = rf"`{_ID}`(?:, `{_ID}`)*"
_LEGACY_SELECTOR = re.compile(rf"(- Check (?:`{_ID}`|<code>{_ID}</code>) selects )(.*)")
_SELECTOR = "- Selector "
_HOST_ENVIRONMENT = "- Host environment "
_ASSURANCE_CASE = "- Assurance case "
_REQUIRED_CASE = "- Required case "
_CONTROL = re.compile(
    r"(- Negative control requires exit )(?:`([1-9][0-9]{0,2})`|<code>([1-9][0-9]{0,2})</code>)"
    r"(?: and failure of (.+))?"
)
_OID = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
_OID_SPAN = rf"(?:`{_OID}`|<code>{_OID}</code>)"
_CITATION = re.compile(rf"(- Source )(.+)( · commit {_OID_SPAN} · blob {_OID_SPAN})")
_ESCAPE_OR_GREATER_THAN = re.compile(r"\\.|>")
_BLOCK_QUOTE = re.compile(r"^[ \t]*(?:(?:[-+*]|[0-9]+[.)])[ \t]+)*>")
_BEHAVIOR_CHECK = re.compile(rf"Check `({_ID})` · Claim `({_ID})`( · Standalone check)?")
_FORMAL_CHECK = re.compile(rf"Formal check `({_ID})` · Claim `({_ID})` · (Z3|Lean|Lean comparator)")
_INVENTORY = re.compile(
    r"Inventory · ([0-9]+) behavior checks? · ([0-9]+) formal checks? · ([0-9]+) declared selectors?"
)
_APP_HEADING = "## What the app does"
_COVERAGE_HEADING = "## Verification coverage and limits"
_NUMBERED_HEADING = re.compile(r"### ([1-9][0-9]*)\. .+")
_TRACE = re.compile(
    rf"Claim `(?P<claim>{_ID})` · Behavior (?P<behavior>{_CHECK_IDS})"
    rf"(?: · Formal (?P<formal>{_CHECK_IDS}))?"
)
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
    # Markdown defines line endings as LF, CRLF, or CR. Keep Unicode separators in prose.
    for raw in StringIO(markdown, newline=""):
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


def _selector_value(code: str) -> str | None:
    if code.startswith("<code>") and code.endswith("</code>"):
        value = code.removeprefix("<code>").removesuffix("</code>")
        if value and "</code>" not in value:
            return value
    if opening := re.match(r"`+", code):
        delimiter = opening[0]
        if not code.endswith(delimiter):
            return None
        value = code[len(delimiter) : -len(delimiter)]
        if (
            not value
            or value.endswith("`")
            or any(run[0] == delimiter for run in re.finditer(r"`+", value))
        ):
            return None
        if value.startswith(" ") and value.endswith(" ") and value.strip(" "):
            value = value[1:-1]
        return value
    return None


def _escape_closing_angles(text: str) -> str:
    # Notion v2 omits the escape on literal closing angle brackets.
    return _ESCAPE_OR_GREATER_THAN.sub(lambda token: r"\>" if token[0] == ">" else token[0], text)


def _control_summary(line: str) -> str | None:
    match = _CONTROL.fullmatch(line)
    if not match:
        return None
    prefix, code, code_html, case = match.groups()
    code = code or code_html
    if int(code) > 124:
        return None
    if case is None:
        return prefix + code_span(code)
    if value := _selector_value(case):
        return prefix + code_span(code) + " and failure of " + code_span(value)
    return None


def _normalized(markdown: str) -> list[str]:
    # Notion can reserialize inline code and inter-block spacing.
    lines = []
    for raw in StringIO(markdown, newline=""):
        line = raw.rstrip("\r\n")
        if prefix := next(
            (
                item
                for item in (_SELECTOR, _HOST_ENVIRONMENT, _REQUIRED_CASE, _ASSURANCE_CASE)
                if line.startswith(item)
            ),
            None,
        ):
            if (value := _selector_value(line.removeprefix(prefix))) is not None:
                line = prefix + code_span(value)
        elif control := _control_summary(line):
            line = control
        elif match := _CITATION.fullmatch(line):
            if value := _selector_value(match[2]):
                line = match[1] + code_span(value) + _CODE.sub(r"`\1`", match[3])
        elif match := _LEGACY_SELECTOR.fullmatch(line):
            prefix, selector = match.groups()
            value = _selector_value(selector)
            if value is not None:
                selector = escape_prose(value)
            else:
                selector = _escape_closing_angles(selector)
            line = _CODE.sub(r"`\1`", prefix) + selector
        else:
            line = _CODE.sub(r"`\1`", line)
            if not _BLOCK_QUOTE.match(line):
                line = _escape_closing_angles(line)
        if line.strip():
            lines.append(line)
    return lines


def _blocks(lines: list[str], prefix: str) -> Iterator[tuple[str, list[str]]]:
    starts = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    for start, end in zip(starts, [*starts[1:], len(lines)], strict=False):
        yield lines[start], lines[start + 1 : end]


def _grouped_claims(
    groups: list[tuple[str, list[str]]], count: int
) -> tuple[dict[str, str], dict[str, str]] | None:
    claim_ids = set()
    behavior, formal = {}, {}
    for _, lines in groups:
        if not lines or not lines[0].startswith("### "):
            return None
        for heading, body in _blocks(lines, "### "):
            number = _NUMBERED_HEADING.fullmatch(heading)
            trace = _TRACE.fullmatch(body[-1]) if len(body) >= 2 else None
            if (
                number is None
                or number[1] != str(len(claim_ids) + 1)
                or trace is None
                or trace["claim"] in claim_ids
                or any(_TRACE.fullmatch(line) for line in body[:-1])
            ):
                return None
            claim_ids.add(trace["claim"])
            for label, entries in (("behavior", behavior), ("formal", formal)):
                if ids := trace[label]:
                    for code in ids.split(", "):
                        check = code[1:-1]
                        if check in behavior or check in formal:
                            return None
                        entries[check] = trace["claim"]
    if len(claim_ids) != count or claim_ids & (behavior.keys() | formal.keys()):
        return None
    return behavior, formal


def _behavior_inventory(lines: list[str]) -> tuple[dict[str, str], int] | None:
    checks = {}
    selectors = 0
    if not lines or not lines[0].startswith("Check "):
        return None
    for heading, body in _blocks(lines, "Check "):
        match = _BEHAVIOR_CHECK.fullmatch(heading)
        if (
            not match
            or match[1] in checks
            or bool(match[3]) != (not body)
            or any(
                not (
                    line.startswith(_SELECTOR)
                    and line[len(_SELECTOR) :]
                    or line.startswith(_HOST_ENVIRONMENT)
                    and (name := _selector_value(line[len(_HOST_ENVIRONMENT) :]))
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    or line.startswith(_REQUIRED_CASE)
                    and _selector_value(line[len(_REQUIRED_CASE) :])
                    or _control_summary(line)
                )
                for line in body
            )
        ):
            return None
        checks[match[1]] = match[2]
        selectors += sum(line.startswith(_SELECTOR) for line in body)
    return checks, selectors


def _formal_inventory(lines: list[str]) -> dict[str, str] | None:
    checks = {}
    if not lines or not lines[0].startswith("Formal check "):
        return None
    for heading, body in _blocks(lines, "Formal check "):
        match = _FORMAL_CHECK.fullmatch(heading)
        if (
            not match
            or match[1] in checks
            or any(
                not line.startswith(
                    (
                        "- Assumption ",
                        "- Comparator theorem · ",
                        "- Allowed imports · ",
                        "- Permitted axioms · ",
                    )
                )
                for line in body
            )
        ):
            return None
        policy = [line for line in body if not line.startswith("- Assumption ")]
        if match[3] == "Lean comparator":
            if (
                len(policy) != 3
                or not policy[0].startswith("- Comparator theorem · ")
                or not policy[0].removeprefix("- Comparator theorem · ")
                or not re.fullmatch(
                    r"- Allowed imports · (?:None|`(?:Init|Std|Lean)(?:\.[A-Za-z_][A-Za-z0-9_']*)*`(?:, `(?:Init|Std|Lean)(?:\.[A-Za-z_][A-Za-z0-9_']*)*`)*)",
                    policy[1],
                )
                or not re.fullmatch(
                    r"- Permitted axioms · (?:None|`(?:propext|Classical\.choice|Quot\.sound)`(?:, `(?:propext|Classical\.choice|Quot\.sound)`)*)",
                    policy[2],
                )
            ):
                return None
        elif policy:
            return None
        checks[match[1]] = match[2]
    return checks


def _has_grouped_layout(lines: list[str], count: int) -> bool:
    sections = list(_blocks(lines, "## "))
    names = [heading for heading, _ in sections]
    if (
        names[:2] != [_APP_HEADING, "## Product limits"]
        or names[-1] != _COVERAGE_HEADING
        or len({name.casefold() for name in names}) != len(names)
        or not all(body for _, body in sections)
    ):
        return False
    first_group = 3 if names[2:3] == ["## Examples"] else 2
    if not (claims := _grouped_claims(sections[first_group:-1], count)):
        return False

    coverage = sections[-1][1]
    inventory = _INVENTORY.fullmatch(coverage[-1])
    subsections = list(_blocks(coverage[:-1], "### "))
    headings = [heading for heading, _ in subsections]
    required = ["### Behavior checks", "### Formal checks and assumptions"]
    if (
        not inventory
        or not headings
        or coverage[0] != headings[0]
        or headings not in (required, ["### Coverage limits", *required])
    ):
        return False
    if len(subsections) == 3:
        limits = subsections[0][1]
        if not limits or any(not line.startswith("- ") for line in limits):
            return False

    behavior = _behavior_inventory(subsections[-2][1])
    formal = _formal_inventory(subsections[-1][1])
    if behavior is None or formal is None:
        return False
    checks, selectors = behavior
    return (checks, formal) == claims and inventory.groups() == (
        str(len(checks)),
        str(len(formal)),
        str(selectors),
    )


def _has_claims_layout(lines: list[str], count: int) -> bool:
    if lines and lines[0] == _APP_HEADING:
        return _has_grouped_layout(lines, count)
    if lines and lines[0] == "## Overview":
        sections = [(i, line) for i, line in enumerate(lines) if line.startswith("## ")]
        names = [line for _, line in sections]
        if names not in (
            ["## Overview", "## Scope limits", "## Claims"],
            ["## Overview", "## Scope limits", "## Examples", "## Claims"],
        ):
            return False
        claims_start = sections[-1][0]
        headings = [
            (i, match)
            for i, line in enumerate(lines)
            if (match := _NUMBERED_HEADING.fullmatch(line))
        ]
        if (
            len(headings) != count
            or headings[0][0] != claims_start + 1
            or any(int(match[1]) != n for n, (_, match) in enumerate(headings, 1))
        ):
            return False
        for (start, _), end in zip(
            headings, [*(i for i, _ in headings[1:]), len(lines)], strict=True
        ):
            if (
                end - start < 3
                or not _TRACE.fullmatch(lines[end - 1])
                or any(_TRACE.fullmatch(line) for line in lines[start + 1 : end - 1])
            ):
                return False
        return True
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
    source, *body = _normalized(section.removesuffix(_END))
    return bool(_SOURCE.fullmatch(source)) and body == _normalized(_body(claims))


def _publish_body(body: str) -> str:
    def selector_as_code(match: re.Match[str]) -> str:
        prefix, selector = match.groups()
        # Historical files store single-line selectors escaped as Markdown prose.
        value = re.sub(r"\\(.)", r"\1", selector)
        return prefix + code_span(value) if value and escape_prose(value) == selector else match[0]

    return "\n".join(_LEGACY_SELECTOR.sub(selector_as_code, line) for line in body.split("\n"))


def notion_claims(page: str, claims: bytes, base: str, *, publish: bool = False) -> bool:
    page_id = _page_id(page)
    current = _section(_request(page_id))
    if _matches(current, claims):
        return True
    if not publish:
        return False

    body = _publish_body(_body(claims))
    heading = (
        _NUMBERED_HEADING
        if body.startswith(("## Overview\n", _APP_HEADING + "\n"))
        else _CLAIM_HEADING
    )
    count = sum(bool(heading.fullmatch(line)) for line in _normalized(body))
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
