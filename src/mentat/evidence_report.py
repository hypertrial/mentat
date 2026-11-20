"""Claim-by-claim views of saved project verification results."""

import json
from collections import Counter
from urllib.parse import quote, urlsplit

from .claims import _code_span, _inline_prose, provenance_lines
from .evidence import source_qualifications, status


def pr_summary(record, head, report_url, acceptance, ci_status="unavailable"):
    """Format a host-supplied report link for the exact saved revision."""
    if head != record["revision"]:
        raise ValueError("PR head does not match the saved verification revision")
    url = urlsplit(report_url)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or any(char.isspace() or not char.isprintable() for char in report_url)
    ):
        raise ValueError("Provide an authorized HTTPS report URL without credentials or whitespace")
    if ci_status not in {"pass", "fail", "incomplete", "unavailable"}:
        raise ValueError("Invalid lightweight CI result")
    link = quote(report_url, safe=":/?=&%#@+;,~!$*[]")
    return (
        f"Mentat review for `{head}`\n\n"
        f"- Lightweight CI (host reported): **{ci_status}**.\n"
        f"- Full saved verification: **{record['status']}**, run `{record['run_id']}`.\n"
        f"- Acceptance for this saved revision: **{acceptance}**.\n"
        f"- [Review the evidence report]({link}).\n\n"
        "Model proofs, tested correspondence and qualification remain separate evidence.\n"
    )


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
    for claim in claims.values():
        checks = claim["formal"] + claim["behavior"] + claim["qualification"]
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


def terminal_overview(record: dict) -> list[str]:
    groups = claim_groups(record)
    titles = record.get("claim_review", {}).get("titles", {})
    lines = [f"Claims  {claim_summary(groups)}"]
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


def _artifact_links(check: dict) -> list[str]:
    refs = [(stream, check[stream]) for stream in ("stdout", "stderr") if stream in check]
    refs.extend(check.get("artifacts", {}).items())
    return [
        f"[{_inline_prose(label)}](./{quote(filename, safe='')})"
        for label, filename in refs
        if filename not in {".", ".."} and "/" not in filename and "\\" not in filename
    ]


def _check_lines(check: dict, label: str) -> list[str]:
    text = f"{label} {_code_span(check['id'])} · **{check['status']}**"
    if check.get("detail"):
        text += f" · {_inline_prose(check['detail'])}"
    lines = [f"- {text}"]
    observations = {}
    formal = check.get("evidence", {})
    if check.get("kind") == "lean-comparator":
        lines.append("  - Model proof; empirical implementation correspondence is separate.")
        for label, key in (
            ("Campaign", "campaign_id"),
            ("Theorem", "theorem"),
            ("Proof source SHA-256", "source_sha256"),
            ("Approved source SHA-256", "approved_sha256"),
        ):
            value = formal.get(key)
            lines.append(
                f"  - {label} {_code_span(value if isinstance(value, str) else 'unavailable')}"
            )
        if check["status"] != "pass":
            lines.append(
                "  - Outstanding proof obligation; this does not demonstrate a product defect."
            )
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
    if "command_status" in check:
        lines.append(f"  - Command · {check['command_status']} · exit {check['exit_code']}")
    cases = check.get("case_results", {})
    for case in check.get("required_cases", sorted(cases)):
        lines.append(f"  - Case {_code_span(case)} · {cases.get(case, 'missing')}")
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
        for check in claim["formal"] + claim["behavior"] + claim["qualification"]
    ]
    for outcome, heading in (("violation", "Counterexample"), ("incomplete", "Evidence gap")):
        finding = next(
            ((claim, check) for claim, check in checks if check["status"] == outcome), None
        )
        if finding:
            claim, check = finding
            if check in claim["qualification"]:
                heading = "Qualification failure" if outcome == "violation" else "Qualification gap"
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
            if provenance := claim.get("provenance"):
                lines.extend(("", *provenance_lines(provenance)))
            lines.extend(
                f"- Assumption · {_inline_prose(item)}" for item in claim.get("assumptions", [])
            )
            assurance = claim.get("assurance")
            if assurance is not None:
                lines.append(
                    "- Assurance declarations require semantic review; metadata alone is not qualification."
                )
                for label, key in (
                    ("Implementation correspondence", "implementation_checks"),
                    ("Reachability witness", "witness_checks"),
                ):
                    for ref in assurance.get(key, []):
                        cases = ", ".join(_code_span(case) for case in ref.get("case_ids", []))
                        lines.append(
                            f"- {label} · {_code_span(ref['check_id'])}"
                            + (f" · Cases {cases}" if cases else "")
                        )
                for bound in assurance.get("bounds", []):
                    lines.append(
                        f"- Bound {_code_span(bound['id'])} · {bound['kind']} · {_inline_prose(bound['statement'])}"
                    )
                    if bound.get("assumption"):
                        lines.append(f"  Assumption · {_inline_prose(bound['assumption'])}")
                    else:
                        lines.append(
                            "  Checks · "
                            + ", ".join(_code_span(ref["check_id"]) for ref in bound["checks"])
                        )
                for exclusion in assurance.get("exclusions", []):
                    lines.append(
                        f"- Exclusion · {_inline_prose(exclusion['scenario'])}: {_inline_prose(exclusion['reason'])}"
                    )
                    lines.append(
                        f"  Impact · {_inline_prose(exclusion['effect'])} · Claims "
                        + ", ".join(_code_span(ident) for ident in exclusion["affected_claims"])
                    )
                for deviation in assurance.get("deviations", []):
                    citation = deviation["citation"]
                    lines.append(
                        f"- Deviation · {_inline_prose(deviation['rationale'])} · {_inline_prose(deviation['effect'])}"
                    )
                    lines.append(
                        f"  Cited source · {_code_span(citation['path'])} · {_code_span(citation['commit'])} · line {citation['start_line']}"
                    )
            lines.append("")
            for check in claim["formal"]:
                lines.extend(_check_lines(check, f"Formal ({check['kind']})"))
            for check in claim["behavior"]:
                lines.extend(_check_lines(check, "Behavior"))
                if control := check.get("negative_control"):
                    outcome = "qualified" if control.get("qualified") else "did not qualify"
                    lines.extend(
                        _check_lines(
                            {**control, "id": check["id"]}, f"Negative control ({outcome})"
                        )
                    )
            for check in claim["qualification"]:
                lines.extend(
                    _check_lines(check, f"Source mutation ({check.get('outcome', 'skipped')})")
                )
                lines.append(f"- Expected failing case: {_code_span(check['expected_case'])}")
    return lines
