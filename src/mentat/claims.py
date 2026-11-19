"""Render and check review copies of a project contract."""

import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from .evidence import decode_json
from .project import _MAX_FILE, Claim, Contract, Review, _root, contract_inputs

_FILENAME = "claims.md"
_MARKUP = re.compile(r"([\\`*_\[\]<>~$&|])")
_LINE_MARKUP = re.compile(r"^([ \t]*)(?=(?:#{1,6}|[+-])(?:[ \t]|$)|[-=]{2,}[ \t]*$)", re.MULTILINE)
_ORDERED_LIST = re.compile(r"^([ \t]*\d+)([.)])(?=[ \t]|$)", re.MULTILINE)
_HEADING_END = re.compile(r"(?<!\S)#+$")


def _prose(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _MARKUP.sub(r"\\\1", value)
    value = _LINE_MARKUP.sub(r"\1\\", value)
    return _ORDERED_LIST.sub(r"\1\\\2", value)


def _review_prose(value: str) -> str:
    return re.sub(r"^[ \t]+", "", _prose(value), flags=re.MULTILINE).strip("\n")


def _inline_prose(value: str) -> str:
    # Grouped list entries represent one paragraph in both Git and Notion.
    paragraphs = re.split(r"\r\n?|\n", value)
    return _prose(" ".join(line.strip(" \t") for line in paragraphs if line.strip(" \t")))


def _code_span(value: str) -> str:
    delimiter = "`" * (max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
    if (
        value.startswith("`")
        or value.endswith("`")
        or (value.startswith(" ") and value.endswith(" ") and value.strip(" "))
    ):
        value = " " + value + " "
    return delimiter + value + delimiter


def provenance_lines(provenance: dict) -> list[str]:
    lines = [f"- Origin · {provenance['origin']}"]
    for citation in provenance["citations"]:
        location = f"{citation['path']}:{citation['start_line']}-{citation['end_line']}"
        lines.append(
            f"- Source {_code_span(location)} · commit {_code_span(citation['commit'])}"
            f" · blob {_code_span(citation['blob'])}"
        )
    return lines


def _append_selectors(lines: list[str], contract: Contract, claim_id: str) -> None:
    selectors = [
        f"- Check `{check.id}` selects {_prose(selector)}"
        for check in contract.behavior
        if check.claim == claim_id
        for selector in (check.selectors or [])
    ]
    environment = [
        f"- Check `{check.id}` inherits host environment {_code_span(name)}"
        for check in contract.behavior
        if check.claim == claim_id
        for name in check.host_environment
    ]
    if selectors or environment:
        lines.extend(("", *selectors, *environment))


def _review_intro(review: Review) -> list[str]:
    lines = [
        "# Claims Spec",
        "",
        "## What the app does" if review.groups else "## Overview",
        "",
        _review_prose(review.overview),
        "",
        "## Product limits" if review.groups else "## Scope limits",
        "",
    ]
    for limit in review.scope_limits:
        text = _inline_prose(limit) if review.groups else _review_prose(limit).replace("\n", "\n  ")
        lines.append("- " + text)
    if review.examples:
        lines.extend(("", "## Examples", ""))
        for example in review.examples:
            url = quote(example.url, safe=":/?#[]@!$&'()*+,;=%-._~")
            url = url.replace("&", "&amp;").replace("(", r"\(").replace(")", r"\)")
            summary = (
                _inline_prose(example.summary)
                if review.groups
                else _review_prose(example.summary).replace("\n", "\n  ")
            )
            lines.append(f"- [{_prose(example.label)}]({url}) — {summary}")
    return lines


def _append_review_claim(
    lines: list[str], contract: Contract, number: int, claim: Claim, *, grouped: bool
) -> None:
    title = _HEADING_END.sub(r"\\\g<0>", _prose(claim.title.rstrip()))
    lines.extend(("", f"### {number}. {title}", "", _prose(claim.statement)))
    behavior = ", ".join(f"`{check.id}`" for check in contract.behavior if check.claim == claim.id)
    formal = ", ".join(f"`{check.id}`" for check in contract.formal if check.claim == claim.id)
    if claim.assumptions and not (grouped and formal):
        lines.extend(("", "Assumptions", ""))
        for item in claim.assumptions:
            text = _inline_prose(item) if grouped else _prose(item).replace("\n", "\n  ")
            lines.append("- " + text)
    if not grouped:
        _append_selectors(lines, contract, claim.id)
    trace = f"Claim `{claim.id}` · Behavior {behavior}"
    if formal:
        trace += f" · Formal {formal}"
    if claim.provenance:
        lines.extend(("", *provenance_lines(claim.provenance.model_dump())))
    lines.extend(("", trace))


def _grouped_claims(contract: Contract) -> bytes:
    review = contract.review
    lines = _review_intro(review)
    claims = {claim.id: claim for claim in contract.claims}
    number = 0
    for group in review.groups:
        title = _HEADING_END.sub(r"\\\g<0>", _prose(group.title))
        lines.extend(("", f"## {title}"))
        for claim_id in group.claim_ids:
            number += 1
            _append_review_claim(lines, contract, number, claims[claim_id], grouped=True)
    lines.extend(("", "## Verification coverage and limits"))
    if review.verification_limits:
        lines.extend(("", "### Coverage limits", ""))
        lines.extend("- " + _inline_prose(item) for item in review.verification_limits)
    lines.extend(("", "### Behavior checks"))
    for check in contract.behavior:
        row = f"Check {_code_span(check.id)} · Claim {_code_span(check.claim)}"
        if not (check.selectors or check.case_report or check.negative_control):
            row += " · Standalone check"
        lines.extend(("", row))
        details = [f"- Selector {_code_span(selector)}" for selector in check.selectors or []]
        details.extend(f"- Host environment {_code_span(name)}" for name in check.host_environment)
        if check.case_report:
            details.extend(
                f"- Required case {_code_span(case)}" for case in check.case_report.required_cases
            )
        if check.negative_control:
            control = check.negative_control
            requirement = (
                f"- Negative control requires exit {_code_span(str(control.violation_exit_code))}"
            )
            if control.failing_case:
                requirement += f" and failure of {_code_span(control.failing_case)}"
            details.append(requirement)
        if details:
            lines.extend(("", *details))
    lines.extend(("", "### Formal checks and assumptions"))
    shown = set()
    for check in contract.formal:
        lines.extend(
            (
                "",
                f"Formal check {_code_span(check.id)} · Claim {_code_span(check.claim)} · "
                + ("Z3" if check.engine == "z3" else "Lean"),
            )
        )
        if check.claim not in shown and (assumptions := claims[check.claim].assumptions):
            lines.append("")
            lines.extend("- Assumption " + _inline_prose(item) for item in assumptions)
            shown.add(check.claim)
    counts = (
        (len(contract.behavior), "behavior check"),
        (len(contract.formal), "formal check"),
        (sum(len(check.selectors or []) for check in contract.behavior), "declared selector"),
    )
    lines.extend(
        (
            "",
            "Inventory · "
            + " · ".join(f"{count} {name}{'' if count == 1 else 's'}" for count, name in counts),
        )
    )
    return ("\n".join(lines) + "\n").encode()


def render_claims(contract: Contract) -> bytes:
    if contract.review is not None and contract.review.groups is not None:
        return _grouped_claims(contract)
    if contract.review is not None:
        lines = _review_intro(contract.review)
        lines.extend(("", "## Claims"))
        for number, claim in enumerate(contract.claims, 1):
            _append_review_claim(lines, contract, number, claim, grouped=False)
        return ("\n".join(lines) + "\n").encode()
    lines = [
        "# Claims Spec",
        "",
        "Generated from `contract.json`. Edit the contract and regenerate.",
    ]
    for claim in contract.claims:
        lines.extend(("", f"## {_prose(claim.id)}", "", _prose(claim.statement)))
        if claim.assumptions:
            lines.extend(("", "Assumptions", ""))
            for assumption in claim.assumptions:
                lines.append("- " + _prose(assumption).replace("\n", "\n  "))
            lines.extend(("", "Checks"))
        if claim.provenance:
            lines.extend(("", *provenance_lines(claim.provenance.model_dump())))
        _append_selectors(lines, contract, claim.id)
        lines.append("")
        for label, checks in (("Formal", contract.formal), ("Behavior", contract.behavior)):
            ids = ", ".join(f"`{check.id}`" for check in checks if check.claim == claim.id)
            lines.append(f"- {label} checks: {ids or 'None'}")
    return ("\n".join(lines) + "\n").encode()


def _read(directory: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"expected a regular .mentat/{name} file")
        data = stream.read(_MAX_FILE + 1)
    if len(data) > _MAX_FILE:
        raise ValueError(f".mentat/{name} exceeds the file size limit")
    return data


@contextmanager
def _worktree_contract(repo: Path):
    directory = os.open(_root(repo) / ".mentat", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        contract = Contract.model_validate(decode_json(_read(directory, "contract.json")))
        yield directory, render_claims(contract)
    finally:
        os.close(directory)


def _matches_local(directory: int, expected: bytes) -> bool:
    try:
        return _read(directory, _FILENAME) == expected
    except FileNotFoundError:
        return False


def checked_claims(repo: Path, base: str | None) -> tuple[bytes, bool]:
    if base is not None:
        _, _, contract, files = contract_inputs(repo, base)
        expected = render_claims(contract)
        return expected, files.get(_FILENAME) == expected
    with _worktree_contract(repo) as (directory, expected):
        return expected, _matches_local(directory, expected)


def write_claims(repo: Path) -> None:
    with _worktree_contract(repo) as (directory, expected):
        if len(expected) > _MAX_FILE:
            raise ValueError("generated .mentat/claims.md exceeds the contract file size limit")
        if _matches_local(directory, expected):
            return
        temporary = f".claims-{uuid4().hex}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(expected)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, _FILENAME, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
