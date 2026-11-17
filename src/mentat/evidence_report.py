"""Claim-by-claim views of saved project verification results."""

import json
from urllib.parse import quote

from .claims import _code_span, _inline_prose
from .evidence import status


def review_context(contract) -> dict:
    review = contract.review
    return {
        "titles": {claim.id: claim.title for claim in contract.claims if claim.title is not None},
        "groups": [group.model_dump(mode="json") for group in (review.groups or [])]
        if review
        else [],
        "verification_limits": review.verification_limits if review else [],
    }


def claim_groups(record: dict) -> list[tuple[str, list[dict]]]:
    claims = {claim["id"]: {**claim, "formal": [], "behavior": []} for claim in record["claims"]}
    for check in record["model_checks"]:
        claims[check["claim"]]["formal"].append(check)
    for case in record["cases"]:
        for check in case["checks"]:
            claims[check["claim"]]["behavior"].append(check)
    skipped = record.get("behavior_skipped")
    if skipped:
        for check in record.get("behavior_checks", []):
            claims[check["claim"]]["behavior"].append(
                {**check, "status": "skipped", "detail": "Formal checks did not pass"}
            )
    for claim in claims.values():
        checks = claim["formal"] + claim["behavior"]
        executed = [check for check in checks if check["status"] != "skipped"]
        result = status(executed, incomplete_first=True) if executed else "skipped"
        if skipped and result == "pass":
            result = "skipped"
        claim["status"] = result if checks or skipped else "incomplete"
    groups = record.get("claim_review", {}).get("groups") or [
        {"title": "Claims", "claim_ids": list(claims)}
    ]
    return [
        (group["title"], [claims[claim_id] for claim_id in group["claim_ids"]]) for group in groups
    ]


def claim_summary(groups: list[tuple[str, list[dict]]]) -> str:
    counts = dict.fromkeys(("pass", "violation", "incomplete", "skipped"), 0)
    for _, claims in groups:
        for claim in claims:
            counts[claim["status"]] += 1
    return ", ".join(f"{count} {outcome}" for outcome, count in counts.items())


def _artifact_links(check: dict) -> list[str]:
    refs = {stream: check[stream] for stream in ("stdout", "stderr") if stream in check}
    refs.update(check.get("artifacts", {}))
    return [
        f"[{_inline_prose(label)}](./{quote(filename, safe='')})"
        for label, filename in refs.items()
        if filename not in {".", ".."} and "/" not in filename and "\\" not in filename
    ]


def _check_lines(check: dict, label: str) -> list[str]:
    text = f"{label} {_code_span(check['id'])} · **{check['status']}**"
    if check.get("detail"):
        text += f" · {_inline_prose(check['detail'])}"
    lines = [f"- {text}"]
    observations = {}
    formal = check.get("evidence", {})
    if "expected" in formal:
        observations["expected"] = formal["expected"]
    if "result" in formal:
        observations["observed"] = formal["result"]
    if "model" in formal:
        observations["witness"] = formal["model"]
    observations.update(
        {key: check[key] for key in ("expected", "observed", "witness") if key in check}
    )
    for key, observation in observations.items():
        value = json.dumps(observation, ensure_ascii=True, sort_keys=True)
        if len(value) > 512:
            value = value[:512] + "…"
        lines.append(f"  - {key.capitalize()} {_code_span(value)}")
    if links := _artifact_links(check):
        lines.append("  - " + " · ".join(links))
    return lines


def project_report(record: dict) -> list[str]:
    groups = claim_groups(record)
    review = record.get("claim_review", {})
    titles = review.get("titles", {})
    lines = [
        f"- Contract base `{record['base']}`",
        f"- Runner `{record['runner']}`",
        f"- Claim outcomes · {claim_summary(groups)}",
    ]
    checks = [
        (claim, check)
        for _, claims in groups
        for claim in claims
        for check in claim["formal"] + claim["behavior"]
    ]
    for outcome, heading in (("violation", "Counterexample"), ("incomplete", "Evidence gap")):
        finding = next(
            ((claim, check) for claim, check in checks if check["status"] == outcome), None
        )
        if finding:
            claim, check = finding
            lines.extend(("", f"## {heading}", "", f"Claim {_code_span(claim['id'])}"))
            lines.extend(_check_lines(check, "Check"))
    if record.get("behavior_skipped"):
        lines.extend(("", "Behavior checks were skipped because formal checks did not pass."))
    if limits := review.get("verification_limits"):
        lines.extend(("", "## Verification limits", ""))
        lines.extend(f"- {_inline_prose(limit)}" for limit in limits)
    for title, claims in groups:
        lines.extend(("", f"## {_inline_prose(title)}"))
        for claim in claims:
            claim_id = claim["id"]
            heading = (
                _inline_prose(titles[claim_id]) if claim_id in titles else _code_span(claim_id)
            )
            lines.extend(("", f"### {heading} · {claim['status']}", ""))
            if claim_id in titles:
                lines.extend((f"Claim {_code_span(claim_id)}", ""))
            lines.append(_inline_prose(claim["statement"]))
            lines.extend(
                f"- Assumption · {_inline_prose(item)}" for item in claim.get("assumptions", [])
            )
            lines.append("")
            for check in claim["formal"]:
                lines.extend(_check_lines(check, f"Formal ({check['kind']})"))
            for check in claim["behavior"]:
                lines.extend(_check_lines(check, "Behavior"))
    return lines
