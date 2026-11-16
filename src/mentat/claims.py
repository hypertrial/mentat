"""Render and check review copies of a project contract."""

import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from .evidence import decode_json
from .project import _MAX_FILE, Contract, _root, contract_inputs

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


def render_claims(contract: Contract) -> bytes:
    if contract.review is not None:
        review = contract.review
        lines = [
            "# Claims Spec",
            "",
            "## Overview",
            "",
            _review_prose(review.overview),
            "",
            "## Scope limits",
            "",
        ]
        for limit in review.scope_limits:
            lines.append("- " + _review_prose(limit).replace("\n", "\n  "))
        if review.examples:
            lines.extend(("", "## Examples", ""))
            for example in review.examples:
                url = quote(example.url, safe=":/?#[]@!$&'()*+,;=%-._~")
                url = url.replace("&", "&amp;").replace("(", r"\(").replace(")", r"\)")
                lines.append(
                    f"- [{_prose(example.label)}]({url}) — "
                    + _review_prose(example.summary).replace("\n", "\n  ")
                )
        lines.extend(("", "## Claims"))
        for number, claim in enumerate(contract.claims, 1):
            title = _HEADING_END.sub(r"\\\g<0>", _prose(claim.title.rstrip()))
            lines.extend(("", f"### {number}. {title}", "", _prose(claim.statement)))
            if claim.assumptions:
                lines.extend(("", "Assumptions", ""))
                for assumption in claim.assumptions:
                    lines.append("- " + _prose(assumption).replace("\n", "\n  "))
            behavior = ", ".join(
                f"`{check.id}`" for check in contract.behavior if check.claim == claim.id
            )
            formal = ", ".join(
                f"`{check.id}`" for check in contract.formal if check.claim == claim.id
            )
            trace = f"Claim `{claim.id}` · Behavior {behavior}"
            if formal:
                trace += f" · Formal {formal}"
            lines.extend(("", trace))
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
