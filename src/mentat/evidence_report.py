"""Claim-by-claim views of saved project verification results."""

from collections import Counter

from .command import visible_text
from .evidence import acceptable, qualification_status, source_qualifications, status

_PRODUCT_VERDICT = {"pass": "passed", "violation": "failed", "incomplete": "incomplete"}
_QUALIFICATION_VERDICT = {**_PRODUCT_VERDICT, "skipped": "did not run"}
_DECISION_SOURCES = {"unverified_agent_relay": "unverified agent relay"}


def decision_source(approval) -> str | None:
    """Label how an approval was decided; an unrecognized recorded source is shown escaped."""
    source = approval.get("decision_source") if isinstance(approval, dict) else None
    if not isinstance(source, str) or not source:
        return None
    if source in _DECISION_SOURCES:
        return _DECISION_SOURCES[source]
    return visible_text(source)


def review_context(contract) -> dict:
    review = contract.review
    return {
        "titles": {claim.id: claim.title for claim in contract.claims if claim.title is not None},
        "groups": [group.model_dump(mode="json") for group in (review.groups or [])]
        if review
        else [],
        "verification_limits": review.verification_limits if review else [],
    }


def verdict_lines(record: dict) -> list[str]:
    """Report product and qualification verdicts apart; legacy records have no split."""
    if "source_qualification_status" not in record:
        return []
    product, qualification = record["status"], record["source_qualification_status"]
    lines = [f"Product checks  {product}", f"Source qualification  {qualification}"]
    if not acceptable(record):
        lines.append(
            f"Product checks {_PRODUCT_VERDICT[product]}; source qualification "
            f"{_QUALIFICATION_VERDICT[qualification]}. This run cannot be accepted."
        )
    return lines


def _verdict(checks: list[dict], skipped) -> str:
    executed = [check for check in checks if check["status"] != "skipped"]
    result = status(executed, incomplete_first=True) if executed else "skipped"
    if skipped and result == "pass":
        result = "skipped"
    return result if checks or skipped else "incomplete"


def claim_groups(record: dict) -> list[tuple[str, list[dict]]]:
    claims = {
        claim["id"]: {**claim, "formal": [], "behavior": [], "qualification": []}
        for claim in record["claims"]
    }
    for check in record["model_checks"]:
        claims[check["claim"]]["formal"].append(check)
    for case in record["cases"]:
        for check in case["checks"]:
            claims[check["claim"]]["behavior"].append(check)
    for check in source_qualifications(record):
        claims[check["claim"]]["qualification"].append(check)
    skipped = record.get("behavior_skipped")
    if skipped:
        for check in record.get("behavior_checks", []):
            claims[check["claim"]]["behavior"].append(
                {**check, "status": "skipped", "detail": "Formal checks did not pass"}
            )
    split = "source_qualification_status" in record
    for claim in claims.values():
        if split:
            claim["status"] = _verdict(claim["formal"] + claim["behavior"], skipped)
            if claim["qualification"]:
                claim["qualification_status"] = qualification_status(claim["qualification"])
        else:
            claim["status"] = _verdict(
                claim["formal"] + claim["behavior"] + claim["qualification"], skipped
            )
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


def terminal_overview(record: dict) -> list[str]:
    groups = claim_groups(record)
    titles = record.get("claim_review", {}).get("titles", {})
    source = decision_source(record.get("approval"))
    lines = [
        *verdict_lines(record),
        *([f"Approval source  {source}"] if source else []),
        f"Claims  {claim_summary(groups)}",
    ]
    for group, claims in groups:
        lines.append(group)
        for claim in claims:
            claim_id = claim["id"]
            title = f" · {titles[claim_id]}" if claim_id in titles else ""
            row = f"  {claim_id}{title}  {claim['status']}"
            for kind in ("formal", "behavior", "qualification"):
                counts = Counter(check["status"] for check in claim[kind])
                if counts:
                    outcomes = ", ".join(f"{count} {outcome}" for outcome, count in counts.items())
                    row += f"  {kind.capitalize()} {outcomes}"
                elif kind == "behavior" and record.get("behavior_skipped"):
                    row += "  Behavior skipped"
            lines.append(row)
            for check in claim["formal"] + claim["behavior"] + claim["qualification"]:
                if check in claim["qualification"]:
                    lines.append(
                        f"    Source mutation {check['id']}  {check.get('outcome', 'skipped')}  {check['status']}  {check['detail']}"
                    )
                elif check["status"] in {"violation", "incomplete"}:
                    lines.append(f"    {check['id']}  {check['detail']}")
    return lines
