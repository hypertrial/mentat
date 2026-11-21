"""Offline, allowlisted HTML projection of an intact saved verification run."""

import base64
import hashlib
import html
import os
import re
import stat
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from . import evidence, records
from .evidence_report import claim_groups, decision_source, verdict_lines

# The run report's only script. The page policy allows exactly this text by hash.
_SCRIPT = r"""const controls=document.getElementById('controls'),search=document.getElementById('search'),filter=document.getElementById('filter'),count=document.getElementById('count'),empty=document.getElementById('empty'),claims=[...document.querySelectorAll('.claim')];controls.hidden=false;count.hidden=false;function update(){let n=0;for(const item of claims){const show=item.dataset.search.includes(search.value.toLowerCase())&&(!filter.value||item.dataset.status===filter.value);item.hidden=!show;if(show)n++;}count.textContent=n+' of '+claims.length+' claims';empty.hidden=n!==0;}search.addEventListener('input',update);filter.addEventListener('change',update);function reveal(hash){if(!/^#claim-[1-9][0-9]*$/.test(hash))return;const target=document.getElementById(hash.slice(1));if(!claims.includes(target))return;search.value='';filter.value='';update();target.open=true;target.scrollIntoView();}document.getElementById('findings').addEventListener('click',event=>{const link=event.target.closest('a');if(link)reveal(link.getAttribute('href')||'');});window.addEventListener('hashchange',()=>reveal(location.hash));update();reveal(location.hash);"""
_SCRIPT_SHA256 = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
_STATUSES = {"pass", "violation", "incomplete", "skipped"}
_SAFE_DETAILS = {
    "Check passed",
    "Check reported a violation",
    "Check exited unexpectedly",
    "Check terminated abnormally",
    "Check did not complete",
    "Case report is inconclusive or contradicts exit code",
    "Missing or invalid case report",
    "Negative control did not demonstrate the expected violation",
    "Formal checks did not pass",
    "Z3 did not run",
    "Z3 returned unknown",
    "Z3 returned sat",
    "Z3 returned unsat",
    "The base model must be satisfiable before checking a counterexample",
    "Z3 worker version differs from detected version",
    "Lean did not run",
    "Lean source must import only Lean standard library modules",
    "Lean source did not compile",
    "Lean theorem audit failed",
    "Lean did not produce an unambiguous theorem axiom audit",
    "Lean produced an unrecognized theorem axiom audit",
    "The theorem uses disallowed axioms",
    "Lean checked the theorem and its axioms",
    "A checked proof campaign is required",
    "Expected case failed",
    "Mutation survived",
    "Baseline check did not pass completely",
    "Mutation must match exactly once",
    "Mutation target is missing or not a regular file",
    "Mutation check did not establish the expected outcome",
    "Latest proof submission is not accepted",
    "Proof campaign attempt or time limit reached",
    "Pinned comparator and kernel accepted the frozen target proof",
    "Lean target, definition or axiom policy did not match",
    "Protected checker timed out or could not complete",
    "Protected checker cleanup did not complete",
    "Proof checker returned mismatched evidence",
}
_SAFE_Z3 = re.compile(r"Z3 returned (?:sat|unsat|unknown), expected (?:sat|unsat)\Z")


def _text(value):
    return html.escape(str(value), quote=True)


def _string(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid saved verification record")
    return value


def acceptance_status(state, record, digest):
    path = state / "acceptances" / (record["run_id"] + ".json")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return "Not recorded"
    except OSError:
        return "Invalid or mismatched receipt"
    try:
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            receipt = evidence.decode_json(stream.read(1_000_001))
        try:
            records.check(records.Acceptance, receipt)
        except ValidationError as exc:
            raise ValueError("Invalid or mismatched receipt") from exc
        if any(
            receipt[k] != v
            for k, v in {
                "run_id": record["run_id"],
                "revision": record["revision"],
                "contract_digest": record["contract_digest"],
                "manifest_digest": digest,
            }.items()
        ) or not evidence.acceptable(record):
            raise ValueError
        accepted = datetime.fromisoformat(_string(receipt["accepted_at"]))
        if accepted.tzinfo is None:
            raise ValueError
        source = decision_source(receipt)
        return "Recorded at " + receipt["accepted_at"] + (f" ({source})" if source else "")
    except (OSError, ValueError, TypeError, KeyError):
        return "Invalid or mismatched receipt"


def _diagnostic(check):
    detail = check.get("detail", "")
    return (
        detail
        if detail in _SAFE_DETAILS or _SAFE_Z3.fullmatch(detail)
        else "Diagnostic omitted; inspect the original evidence."
    )


def _status(value):
    return f'<span class="status {_text(value)}">{_text(value.title())}</span>'


def _check_html(check, kind):
    status = check["status"]
    name = _text(check["id"])
    title = f"{kind.title()} · {name}"
    if kind == "formal":
        title += f" ({_text(check.get('kind', 'Unavailable'))})"
    elif kind == "qualification":
        title = f"Source mutation · {name} · {_text(check.get('outcome', 'skipped'))}"
    parts = [
        f'<section class="check"><h5>{title} · {_status(status)}</h5>',
        f"<p>{_text(_diagnostic(check))}</p>",
    ]
    if kind == "qualification":
        parts.append(
            f"<p>Qualification of the check; this outcome alone does not demonstrate a product defect. Expected failing case: {_text(check['expected_case'])}</p>"
        )
    if kind == "formal":
        info = check.get("evidence", {})
        if check.get("kind") == "lean-comparator":
            parts.append(
                "<p>Model proof only; implementation correspondence is recorded separately.</p>"
            )
            for label, key in (
                ("Campaign", "campaign_id"),
                ("Theorem", "theorem"),
                ("Proof source SHA-256", "source_sha256"),
                ("Approved source SHA-256", "approved_sha256"),
            ):
                value = info.get(key)
                parts.append(
                    f"<p>{label}: {_text(value if isinstance(value, str) else 'Unavailable')}</p>"
                )
            environment = info.get("environment", {})
            for label, key in (
                ("Lean", "version"),
                ("Toolchain SHA-256", "toolchain_sha256"),
                ("Checker policy SHA-256", "policy_sha256"),
            ):
                value = environment.get(key) if isinstance(environment, dict) else None
                parts.append(
                    f"<p>{label}: {_text(value if isinstance(value, str) else 'Unavailable')}</p>"
                )
            if status != "pass":
                parts.append(
                    "<p>Outstanding proof obligation. A missing or rejected proof does not establish a product defect.</p>"
                )
        for label, key in (("Expected", "expected"), ("Observed", "result")):
            value = info.get(key, check.get("expected" if key == "expected" else "observed"))
            if value in ("sat", "unsat", "unknown"):
                parts.append(f"<p>{label}: {_text(value)}</p>")
    else:
        if "exit_code" in check:
            parts.append(
                f"<p>Exit code: {_text(check['exit_code'] if check['exit_code'] is not None else 'Unavailable')}</p>"
            )
        parts.append(_cases_html(check))
    files = list(check.get("artifacts", {}).values()) + [
        check[key] for key in ("stdout", "stderr") if key in check
    ]
    if files:
        parts.append("<p>Evidence filenames: " + ", ".join(_text(name) for name in files) + "</p>")
    if omissions := check.get("artifact_omissions", []):
        parts.append(
            "<p>Omitted artifacts: " + ", ".join(_text(name) for name in omissions) + "</p>"
        )
    if omissions := check.get("log_omissions", []):
        parts.append(
            "<p>Omitted log streams: " + ", ".join(_text(name) for name in omissions) + "</p>"
        )
    if control := check.get("negative_control"):
        qualification = (
            "Unavailable"
            if "qualified" not in control
            else "Qualified"
            if control["qualified"]
            else "Not qualified"
        )
        parts.append(
            f'<div class="control"><h6>Negative control · {_status(control["status"])} · {_text(qualification)}</h6>'
        )
        parts.append(f"<p>{_text(_diagnostic(control))}</p>")
        if "exit_code" in control:
            parts.append(
                f"<p>Exit code: {_text(control['exit_code'] if control['exit_code'] is not None else 'Unavailable')}</p>"
            )
        parts.append(_cases_html(control))
        filenames = list(control.get("artifacts", {}).values()) + [
            control[key] for key in ("stdout", "stderr") if key in control
        ]
        if filenames:
            parts.append(
                "<p>Evidence filenames: " + ", ".join(_text(name) for name in filenames) + "</p>"
            )
        if omissions := control.get("artifact_omissions", []):
            parts.append(
                "<p>Omitted artifacts: " + ", ".join(_text(name) for name in omissions) + "</p>"
            )
        if omissions := control.get("log_omissions", []):
            parts.append(
                "<p>Omitted log streams: " + ", ".join(_text(name) for name in omissions) + "</p>"
            )
        parts.append("</div>")
    parts.append("</section>")
    return "".join(parts)


def _cases_html(check):
    if "required_cases" not in check and "case_results" not in check:
        return "<p>Cases: Unavailable</p>"
    results = check.get("case_results", {})
    names = check.get("required_cases", list(results))
    prefix = "<p>Required case inventory: Unavailable</p>" if "required_cases" not in check else ""
    return (
        prefix
        + "<ul>"
        + "".join(
            f"<li>{_text(name)} · {_text(results.get(name, 'missing').title())}</li>"
            for name in names
        )
        + "</ul>"
    )


def _assurance_html(claim, checks):
    assurance = claim.get("assurance")
    if assurance is None:
        return "<p>Assurance metadata: Unavailable</p>"

    def references(items):
        if not items:
            return "<p>Unavailable</p>"
        rows = []
        for item in items:
            check = checks.get(item["check_id"], {})
            owner = check.get("claim", "Unavailable")
            outcome = check.get("status", "Unavailable")
            cases = item.get("case_ids", [])
            detail = "; cases: " + ", ".join(cases) if cases else ""
            rows.append(
                f"<li>{_text(item['check_id'])} · {_text(outcome)} · supporting claim {_text(owner)}{_text(detail)}</li>"
            )
        return "<ul>" + "".join(rows) + "</ul>"

    parts = [
        "<h5>Assurance</h5><p>Declarations require semantic review; their presence alone does not establish qualification.</p>"
    ]
    for label, key in (
        ("Tested implementation correspondence", "implementation_checks"),
        ("Reachability witnesses", "witness_checks"),
    ):
        parts.append(f"<h6>{label}</h6>" + references(assurance.get(key, [])))
    parts.append("<h6>Operational bounds</h6>")
    bounds = assurance.get("bounds", [])
    if not bounds:
        parts.append("<p>Unavailable</p>")
    for bound in bounds:
        parts.append(
            f"<p>{_text(bound['id'])} · {_text(bound['kind'])}: {_text(bound['statement'])}</p>"
        )
        if bound.get("assumption"):
            parts.append(f"<p>Assumption: {_text(bound['assumption'])}</p>")
        else:
            parts.append(references(bound.get("checks", [])))
    for label, key in (
        ("Excluded scenarios", "exclusions"),
        ("Deviations from cited requirements", "deviations"),
    ):
        parts.append(f"<h6>{label}</h6>")
        items = assurance.get(key, [])
        if not items:
            parts.append("<p>Unavailable</p>")
        for item in items:
            if key == "exclusions":
                parts.append(
                    f"<p>{_text(item['scenario'])}: {_text(item['reason'])}</p><p>Affected claims: {_text(', '.join(item['affected_claims']))}</p>"
                )
            else:
                citation = item["citation"]
                parts.append(
                    f"<p>Cited source: {_text(citation['path'])} · {_text(citation['commit'])} · line {_text(citation['start_line'])}</p><p>{_text(item['rationale'])}</p>"
                )
            parts.append(f"<p>Guarantee impact: {_text(item['effect'])}</p>")
    return "".join(parts)


def _render(record, groups, manifest_digest, acceptance):
    review = record.get("claim_review", {})
    titles = review.get("titles", {})
    all_claims = [claim for _, claims in groups for claim in claims]
    metadata_notes = []
    if not review.get("groups"):
        metadata_notes.append("Review area metadata unavailable; claims are shown together.")
    if any(claim["id"] not in titles for claim in all_claims):
        metadata_notes.append("Claim title metadata unavailable; claim IDs are shown instead.")
    metadata_html = "".join(f"<p>{note}</p>" for note in metadata_notes)
    all_checks = [
        (claim, kind, check)
        for claim in all_claims
        for kind in ("formal", "behavior", "qualification")
        for check in claim[kind]
    ]
    counts = Counter(kind for _, kind, _ in all_checks)
    primary_checks = {
        check["id"]: check for _, kind, check in all_checks if kind != "qualification"
    }
    behavior_count = f"{counts['behavior']} behavior"
    if record["behavior_skipped"]:
        behavior_count = (
            "behavior: Unavailable"
            if "behavior_checks" not in record
            else f"{behavior_count} (skipped)"
        )
    case_count = sum(
        len(check.get("required_cases", []))
        for _, kind, check in all_checks
        if kind == "behavior" and check["status"] != "skipped"
    )
    behaviors = [check for _, kind, check in all_checks if kind == "behavior"]
    if behaviors and any("required_cases" not in check for check in behaviors):
        case_count = (
            f"{case_count} (partial; some inventories unavailable)"
            if any("required_cases" in check for check in behaviors)
            else "Unavailable"
        )
    elif record["behavior_skipped"] and not behaviors:
        case_count = "Unavailable"
    source = decision_source(record.get("approval"))
    rows = [
        ("Original verdict", record["status"]),
        ("Candidate", record["revision"]),
        ("Contract base", record["base"]),
        ("Contract digest", record["contract_digest"]),
        *([("Approval source", source)] if source else []),
        ("Run ID", record["run_id"]),
        ("Runner", record["runner"]),
        ("Recorded at", record.get("recorded_at", "Unavailable")),
        ("Verified manifest SHA-256", manifest_digest),
        ("Local operator acceptance", acceptance),
    ]
    if "source_qualification_status" in record:
        rows.insert(1, ("Source qualification", record["source_qualification_status"]))
    overview = "".join(
        f'<div><dt>{_text(label)}</dt><dd class="{"hash" if label in {"Candidate", "Contract base", "Contract digest", "Run ID", "Verified manifest SHA-256"} else ""}">{_text(value)}</dd></div>'
        for label, value in rows
    )
    findings = []
    anchors = {claim["id"]: index for index, claim in enumerate(all_claims, start=1)}
    for claim, kind, check in all_checks:
        if check["status"] in {"violation", "incomplete"}:
            findings.append(
                f"<li><a href='#claim-{anchors[claim['id']]}'>{_text(claim['id'])}</a> · {_text(kind)} · {_text(check['id'])} · {_status(check['status'])} · {_text(_diagnostic(check))}</li>"
            )
    areas = []
    number = 0
    for area, claims in groups:
        parts = [f'<section class="area"><h3>{_text(area)}</h3>']
        for claim in claims:
            number += 1
            title = titles.get(claim["id"], claim["id"])
            search = _text(f"{title} {claim['id']}".lower())
            parts.append(
                f'<details open class="claim" id="claim-{number}" data-search="{search}" data-status="{_text(claim["status"])}"><summary><span>{_text(title)}</span> {_status(claim["status"])}</summary>'
            )
            parts.append(f"<p>Claim ID: {_text(claim['id'])}</p><p>{_text(claim['statement'])}</p>")
            if claim.get("assumptions"):
                parts.append(
                    "<h5>Assumptions</h5><ul>"
                    + "".join(f"<li>{_text(item)}</li>" for item in claim["assumptions"])
                    + "</ul>"
                )
            parts.append(_assurance_html(claim, primary_checks))
            parts.extend(
                _check_html(check, kind)
                for kind in ("formal", "behavior", "qualification")
                for check in claim[kind]
            )
            if "source_mutation_checks" not in record:
                parts.append("<p>Source mutation qualification: Unavailable</p>")
            elif "qualification_status" in claim:
                parts.append(
                    f"<p>Source mutation qualification: {_text(claim['qualification_status'])}</p>"
                )
            parts.append("</details>")
        parts.append("</section>")
        areas.append("".join(parts))
    limitations = review.get("verification_limits")
    limit_html = (
        "<ul>" + "".join(f"<li>{_text(item)}</li>" for item in limitations) + "</ul>"
        if limitations
        else "<p>Unavailable</p>"
    )
    gate = (
        "<p>Behavior checks were skipped because formal checks did not pass.</p>"
        if record["behavior_skipped"]
        else ""
    )
    verdict = "".join(f"<p>{_text(line)}</p>" for line in verdict_lines(record))
    findings_html = (
        "<ul>" + "".join(findings) + "</ul>"
        if findings
        else "<p>No primary violations or incomplete checks recorded.</p>"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{_SCRIPT_SHA256}'; base-uri 'none'; form-action 'none'">
<title>Mentat verification report</title><style>
:root {{ color-scheme: light; font-family: Aptos, "Segoe UI", "Helvetica Neue", sans-serif; color: #25324b; background: #f5f7fb; }}
* {{ box-sizing: border-box; }} body {{ margin: 0; line-height: 1.5; }} main {{ max-width: 1120px; margin: auto; padding: 2rem 1rem 4rem; }}
section.panel {{ background: #fff; border: 1px solid #dce3ee; padding: 1.25rem; margin: 1rem 0; }} h1,h2,h3,h4,h5,h6 {{ line-height: 1.25; }} h1 {{ margin-top: 0; }} p,li {{ max-width: 80ch; overflow-wrap: anywhere; }} a {{ color: #174e9e; }}
dl {{ display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: .9rem; }} dt {{ font-weight: 700; }} dd {{ margin: 0; overflow-wrap: anywhere; }} .hash {{ font-family: ui-monospace, monospace; font-size: .88em; }}
.status {{ font-weight: 700; color: #25324b; }} .pass {{ color: #176447; }} .violation {{ color: #aa2540; }} details {{ background: #fff; border: 1px solid #dce3ee; padding: 1rem; margin: .7rem 0; }} summary {{ cursor: pointer; font-weight: 700; }} .check {{ border-top: 1px solid #dce3ee; }} .control {{ border-left: 3px solid #174e9e; padding-left: .8rem; }}
.controls {{ display: flex; flex-wrap: wrap; gap: 1rem; }} label {{ font-weight: 700; }} input,select {{ display: block; font: inherit; padding: .4rem; }} [hidden] {{ display: none !important; }} @media(max-width: 640px) {{ dl {{ grid-template-columns: 1fr; }} }}
</style></head><body><main><h1>Verification report</h1><p>Saved evidence for a single verification run.</p>
<p>Claims and diagnostic filenames may contain sensitive information. Review this report before sharing.</p>
<nav><a href="#overview">Overview</a> · <a href="#findings">Findings</a> · <a href="#claims">Claims</a> · <a href="#limits">Limits</a></nav>
<section class="panel" id="overview"><h2>Overview</h2><dl>{overview}</dl><p>Acceptance records a local decision for the saved revision. It does not establish identity or current approval.</p>
<p>Primary checks: {counts["formal"]} formal, {behavior_count}; {case_count} required cases. Negative controls are excluded.</p>{gate}{verdict}</section>
<section class="panel" id="findings"><h2>Primary findings</h2>{findings_html}</section>
<section class="panel" id="limits"><h2>Verification limits</h2>{limit_html}</section>
<section id="claims"><h2>Claims</h2>{metadata_html}<div class="controls" id="controls" hidden><label>Search title or ID<input id="search" type="search"></label><label>Status<select id="filter"><option value="">All</option><option value="pass">Pass</option><option value="violation">Violation</option><option value="incomplete">Incomplete</option><option value="skipped">Skipped</option></select></label></div><p id="count" hidden></p><p id="empty" hidden>No matching claims.</p>{"".join(areas)}</section>
<script>{_SCRIPT}</script></main></body></html>"""


def _write_html(state, output, content):
    target = output.expanduser().absolute()
    parent = target.parent.resolve(strict=True)
    if not parent.is_dir() or parent.is_relative_to(state):
        raise ValueError("Output must be outside the state directory with an existing parent")
    target = parent / target.name
    if target.exists() or target.is_symlink():
        raise ValueError("Output already exists")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=parent, prefix=".mentat-report-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target, follow_symlinks=False)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def render_run(state: Path, run_id: str, output: Path) -> None:
    try:
        state = evidence.state_directory(state, create=False)
        _, record, manifest_digest = evidence.read_run_snapshot(state, run_id)
        evidence.validate_run_record(record)
        groups = claim_groups(record)
        content = _render(
            record, groups, manifest_digest, acceptance_status(state, record, manifest_digest)
        )
        _write_html(state, output, content)
    except (KeyError, TypeError, IndexError, AttributeError, UnicodeError) as exc:
        raise ValueError("Invalid saved verification record") from exc


def render_comparison(
    state: Path, before_id: str, after_id: str, output: Path, *, across_contracts=False
):
    """Render two validated snapshots without exposing runtime paths or raw evidence."""
    try:
        state = evidence.state_directory(state, create=False)
        comparison = evidence.compare_runs(
            state, before_id, after_id, across_contracts=across_contracts
        )
        runs, claims, contexts, overviews = [], [], [], []
        for label, run_id in (("Before", before_id), ("After", after_id)):
            _, record, manifest = evidence.read_run_snapshot(state, run_id)
            evidence.validate_run_record(record)
            groups = claim_groups(record)
            items = {claim["id"]: claim for _, area in groups for claim in area}
            checks = {
                check["id"]: check
                for claim in items.values()
                for kind in ("formal", "behavior")
                for check in claim[kind]
            }
            runs.append(record)
            claims.append(items)
            contexts.append(checks)
            source = decision_source(record.get("approval"))
            fields = [
                ("Revision", record["revision"]),
                ("Run", run_id),
                ("Contract base", record["base"]),
                ("Contract digest", record["contract_digest"]),
                *([("Approval source", source)] if source else []),
                ("Manifest SHA-256", manifest),
                ("Operator acceptance", acceptance_status(state, record, manifest)),
            ]
            if "source_qualification_status" in record:
                fields.insert(
                    1, ("Source qualification", record["source_qualification_status"])
                )
            overviews.append(
                f'<section class="panel"><h2>{label} · {_status(record["status"])}</h2><dl>'
                + "".join(f"<dt>{key}</dt><dd>{_text(value)}</dd>" for key, value in fields)
                + "</dl></section>"
            )

        def signature(claim):
            if claim is None:
                return None
            return [
                (
                    kind,
                    {
                        key: check.get(key)
                        for key in (
                            "id",
                            "kind",
                            "status",
                            "case_results",
                            "required_cases",
                            "qualified",
                            "outcome",
                            "assertion_failed",
                            "evidence",
                        )
                    },
                )
                for kind in ("formal", "behavior", "qualification")
                for check in claim[kind]
            ]

        rows = []
        for ident in dict.fromkeys((*claims[0], *claims[1])):
            left, right = claims[0].get(ident), claims[1].get(ident)
            fingerprints = [
                record.get("comparison_inputs", {}).get("claims", {}).get(ident)
                for record in runs
            ]
            definition = (
                "added"
                if left is None
                else "removed"
                if right is None
                else "unavailable"
                if None in fingerprints
                else "changed"
                if fingerprints[0] != fingerprints[1]
                else "unchanged"
            )
            available_definition_changed = (
                left is not None
                and right is not None
                and any(
                    left.get(key) != right.get(key)
                    for key in ("statement", "assumptions", "assurance", "provenance")
                )
            )
            changed = (
                definition in {"added", "removed", "changed"}
                or available_definition_changed
                or signature(left) != signature(right)
            )
            rows.append((not changed, ident, definition, left, right))
        rows.sort(key=lambda row: row[0])
        sections = []
        for unchanged, ident, definition, left, right in rows:
            checks_by_side = [
                {
                    (kind, check["id"]): check
                    for kind in ("formal", "behavior", "qualification")
                    for check in (claim[kind] if claim else [])
                }
                for claim in (left, right)
            ]
            changed_checks = {
                key
                for key in checks_by_side[0].keys() | checks_by_side[1].keys()
                if checks_by_side[0].get(key) != checks_by_side[1].get(key)
            }
            title = next(
                (
                    record.get("claim_review", {}).get("titles", {}).get(ident)
                    for record in reversed(runs)
                    if record.get("claim_review", {}).get("titles", {}).get(ident)
                ),
                ident,
            )
            verdict = (
                "Changed"
                if not unchanged
                else "Definition unavailable"
                if definition == "unavailable"
                else "Unchanged"
            )
            parts = [
                f"<details><summary>{_text(title)} · {_text(ident)} · {verdict}</summary>",
                f'<p>Claim definition: {_text(definition)}</p><div class="pair">',
            ]
            for side, claim in enumerate((left, right)):
                label = "Before" if side == 0 else "After"
                if claim is None:
                    parts.append(f"<section><h3>{label}: Absent</h3></section>")
                    continue
                parts += [
                    f"<section><h3>{label} · {_status(claim['status'])}</h3><p>{_text(claim['statement'])}</p>",
                    "<h4>Assumptions</h4><ul>"
                    + "".join(f"<li>{_text(item)}</li>" for item in claim.get("assumptions", []))
                    + "</ul>",
                    _assurance_html(claim, contexts[side]),
                ]
                ordered_checks = sorted(
                    checks_by_side[side].items(), key=lambda item: item[0] not in changed_checks
                )
                parts += [_check_html(check, kind) for (kind, _), check in ordered_checks]
                parts.append("</section>")
            sections.append("".join(parts) + "</div></details>")
        environment = "Changed" if comparison["environment_changed"] else "Unchanged"
        content = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Mentat run comparison</title><style>body{font-family:system-ui,sans-serif;background:#f5f7fb;color:#25324b;line-height:1.5;margin:0}main{max-width:1120px;margin:auto;padding:1rem}.panel,details{background:white;border:1px solid #dce3ee;padding:1rem;margin:1rem 0}.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}p,li,dd,summary{overflow-wrap:anywhere}dd{margin:0 0 .5rem}dt,summary,.status{font-weight:700}summary{cursor:pointer}.pass{color:#176447}.violation{color:#aa2540}.check{border-top:1px solid #dce3ee}@media(max-width:640px){.pair{grid-template-columns:1fr}}</style></head><body><main><h1>Run comparison</h1>
<p>Changed claims and check evidence appear first. Both original verdicts and operator decisions describe their saved revisions.</p>
<p>Claim text and diagnostic filenames may be sensitive. Inspect this report before sharing. Raw evidence remains in the original exports.</p>"""
        content += '<div class="pair">' + "".join(overviews) + "</div>"
        content += f"<p>Recorded tools or runner metadata: {environment}. Legacy definition metadata is labeled unavailable.</p>"
        if across_contracts:
            content += "<p>Explicit cross-contract history: changed definitions can explain a changed outcome.</p>"
        content += "".join(sections) + "</main></body></html>"
        _write_html(state, output, content)
    except (KeyError, TypeError, IndexError, AttributeError, UnicodeError) as exc:
        raise ValueError("Invalid saved verification comparison") from exc
