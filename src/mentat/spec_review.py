"""Present the exact contract and compare it with an earlier review base."""

import shlex

import typer

from .evidence import digest, json_bytes
from .project import Project


def summary(project: Project) -> dict:
    contract = project.contract
    base_files = {
        path: {"staged": f"base/{path}", "sha256": digest(project.files[f"base/{path}"])}
        for path in contract.base_files
    }
    staged = {file["staged"] for file in base_files.values()}
    return {
        "contract": contract.model_dump(mode="json", exclude_none=True),
        "lean_runs_on_host": any(check.engine == "lean" for check in contract.formal),
        "approved_files": {
            name: digest(data) for name, data in project.files.items() if name not in staged
        },
        "base_files": base_files,
        "review": [
            {
                "id": claim.id,
                "title": claim.title,
                "statement": claim.statement,
                "assumptions": claim.assumptions,
                "formal": [
                    check.model_dump(mode="json", exclude_none=True)
                    for check in contract.formal
                    if check.claim == claim.id
                ],
                "behavior": [
                    {
                        **check.model_dump(mode="json", exclude_none=True),
                        "effective_argv": check.effective_argv(),
                    }
                    for check in contract.behavior
                    if check.claim == claim.id
                ],
            }
            for claim in contract.claims
        ],
    }


def _changes(before: dict, after: dict) -> dict:
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in sorted(before.keys() | after.keys())
        if before.get(key) != after.get(key)
    }


def compare(project: Project, current: dict, previous: Project) -> dict:
    previous_review = summary(previous)
    old, new = previous_review["contract"], current["contract"]
    groups = ("claims", "formal", "behavior")
    delta = {
        key: _changes(
            {item["id"]: item for item in old[key]},
            {item["id"]: item for item in new[key]},
        )
        for key in groups
    }
    orders = _changes(
        {group: [item["id"] for item in old[group]] for group in groups},
        {group: [item["id"] for item in new[group]] for group in groups},
    )
    settings = _changes(
        {key: value for key, value in old.items() if key not in groups},
        {key: value for key, value in new.items() if key not in groups},
    )
    old_files, new_files = previous_review["base_files"], current["base_files"]
    return {
        "against": previous.base,
        "against_digest": previous.fingerprint,
        "base": project.base,
        "contract_digest": project.fingerprint,
        **delta,
        "order": orders,
        "settings": settings,
        "approved_files": _changes(previous_review["approved_files"], current["approved_files"]),
        "base_files": _changes(old_files, new_files),
        "git_diff": shlex.join(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(project.root),
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                previous.base,
                project.base,
                "--",
                ".mentat",
                *(f":(literal){path}" for path in sorted(old_files.keys() | new_files.keys())),
            ]
        ),
    }


def _line(label: str, value) -> None:
    typer.echo(f"{label}  {json_bytes(value).decode().rstrip()}")


def show(review: dict) -> None:
    for key in ("repo", "base", "runner", "contract_digest", "lean_runs_on_host"):
        _line(key.replace("_", " "), review[key])
    _line(
        "contract settings",
        {
            key: value
            for key, value in review["contract"].items()
            if key not in ("claims", "formal", "behavior")
        },
    )
    for claim in review["review"]:
        typer.echo(f"Claim {claim['id']}")
        if claim["title"]:
            _line("  title", claim["title"])
        _line("  statement", claim["statement"])
        if claim["assumptions"]:
            _line("  assumptions", claim["assumptions"])
        for check in claim["formal"]:
            _line(f"  formal {check['id']}", check)
        for check in claim["behavior"]:
            _line(f"  behavior {check['id']}", check)
    _line("approved .mentat files", review["approved_files"])
    if review["base_files"]:
        _line("approved base files", review["base_files"])
    if "delta" in review:
        typer.echo("Changes since comparison base")
        for key, value in review["delta"].items():
            if value:
                _line(f"  {key}", value)
