"""Present the exact contract and compare it with an earlier review base."""

import shlex

import typer

from . import gitio
from .evidence import digest, json_bytes
from .project import CHECKS_PREFIX, Project


def summary(project: Project) -> dict:
    contract = project.contract
    base_files = {
        path: {"staged": f"base/{path}", "sha256": digest(project.files[f"base/{path}"])}
        for path in contract.base_files
    }
    staged = {file["staged"] for file in base_files.values()}
    return {
        "contract": contract.model_dump(mode="json", exclude_none=True),
        "lean_runs_on_host": any(
            check.engine in ("lean", "lean-comparator") for check in contract.formal
        ),
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
                **({"provenance": claim.provenance.model_dump()} if claim.provenance else {}),
                **(
                    {"assurance": claim.assurance.model_dump(exclude_none=True)}
                    if claim.assurance is not None
                    else {}
                ),
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


def comparison_inputs(project: Project) -> dict:
    contract = project.contract
    files = {name: digest(data) for name, data in project.files.items()}
    definitions = {}
    check_index = {check.id: check for check in [*contract.formal, *contract.behavior]}
    for claim in contract.claims:
        formal = [item for item in contract.formal if item.claim == claim.id]
        behavior = [item for item in contract.behavior if item.claim == claim.id]
        assurance = claim.assurance
        refs = (
            [
                *assurance.implementation_checks,
                *assurance.witness_checks,
                *(ref for bound in assurance.bounds for ref in bound.checks),
            ]
            if assurance
            else []
        )
        supporting = [
            check_index[ident]
            for ident in dict.fromkeys(ref.check_id for ref in refs)
            if check_index[ident].claim != claim.id
        ]
        supporting_formal = [
            check for check in supporting if check.id in {item.id for item in contract.formal}
        ]
        supporting_behavior = [
            check for check in supporting if check.id in {item.id for item in contract.behavior}
        ]
        direct_paths = {
            path
            for check in [*formal, *supporting_formal]
            for path in (check.source, check.counterexample)
            if path is not None
        }
        for check in [*behavior, *supporting_behavior]:
            argv = check.effective_argv()
            if check.negative_control:
                argv = [*argv, *check.negative_control.args]
            direct_paths.update(
                arg.removeprefix(CHECKS_PREFIX) for arg in argv if arg.startswith(CHECKS_PREFIX)
            )
        definitions[claim.id] = digest(
            json_bytes(
                {
                    "statement": claim.statement,
                    "assumptions": claim.assumptions,
                    **(
                        {"assurance": claim.assurance.model_dump(exclude_none=True)}
                        if claim.assurance is not None
                        else {}
                    ),
                    **(
                        {
                            "supporting_checks": [
                                check.model_dump(mode="json", exclude_none=True)
                                for check in supporting
                            ]
                        }
                        if supporting
                        else {}
                    ),
                    "formal": [
                        check.model_dump(mode="json", exclude_none=True) for check in formal
                    ],
                    "behavior": [
                        check.model_dump(mode="json", exclude_none=True) for check in behavior
                    ],
                    "files": {name: files[name] for name in direct_paths},
                }
            )
        )
    return {
        "claims": definitions,
        "supporting_files": digest(
            json_bytes(
                {
                    name: value
                    for name, value in files.items()
                    if name not in {"contract.json", "claims.md"}
                }
            )
        ),
        "runner_settings": digest(
            json_bytes(
                contract.model_dump(
                    mode="json",
                    include={"docker_image", "allow_host", "host_requirements", "source_mutations"},
                )
            )
        ),
        "verifier": project.verifier_digest,
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
            gitio.argv(  # pyright: ignore[reportArgumentType]  # gitio.argv is typed list[str | bytes]; shlex.join needs str, and no bytes are passed here
                project.root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                previous.base,
                project.base,
                "--",
                ".mentat",
                *(f":(literal){path}" for path in sorted(old_files.keys() | new_files.keys())),
            )
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
        if "provenance" in claim:
            _line("  provenance", claim["provenance"])
        if "assurance" in claim:
            _line("  assurance", claim["assurance"])
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
