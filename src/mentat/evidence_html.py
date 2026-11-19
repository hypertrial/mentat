"""Offline, allowlisted HTML projection of an intact saved verification run."""

import html
import os
import re
import stat
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import evidence
from .evidence_report import claim_groups

_STATUSES = {"pass", "violation", "incomplete", "skipped"}
_CASE_STATUSES = {"pass", "fail", "skip", "error"}
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
}
_SAFE_Z3 = re.compile(r"Z3 returned (?:sat|unsat|unknown), expected (?:sat|unsat)\Z")


def _text(value):
    return html.escape(str(value), quote=True)


def _string(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid saved verification record")
    return value


def _strings(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("Invalid saved verification record")
    return value


def _check(check, claims, artifacts, kind):
    if not isinstance(check, dict):
        raise ValueError("Invalid saved check")
    _string(check.get("id"))
    if check.get("claim") not in claims or check.get("status") not in evidence.STATUSES:
        raise ValueError("Invalid saved check reference or status")
    if kind == "formal" and check.get("kind") not in {"z3", "lean"}:
        raise ValueError("Invalid saved formal engine")
    if not isinstance(check.get("detail"), str):
        raise ValueError("Invalid saved check detail")
    _references(check, artifacts)
    if kind == "behavior":
        _cases(check)
        if (
            "exit_code" in check
            and check["exit_code"] is not None
            and type(check["exit_code"]) is not int
        ):
            raise ValueError("Invalid saved exit code")
        if "command_status" in check and check["command_status"] not in evidence.STATUSES:
            raise ValueError("Invalid saved command status")
        if "negative_control" in check:
            control = check["negative_control"]
            if not isinstance(control, dict) or control.get("status") not in evidence.STATUSES:
                raise ValueError("Invalid saved negative control")
            if not isinstance(control.get("detail"), str):
                raise ValueError("Invalid saved negative control detail")
            if "qualified" in control and type(control["qualified"]) is not bool:
                raise ValueError("Invalid saved control qualification")
            _references(control, artifacts)
            _cases(control)
            if (
                "exit_code" in control
                and control["exit_code"] is not None
                and type(control["exit_code"]) is not int
            ):
                raise ValueError("Invalid saved control exit code")
    elif "evidence" in check and not isinstance(check["evidence"], dict):
        raise ValueError("Invalid saved formal evidence")


def _filename(name):
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Invalid saved evidence filename")
    return name


def _references(check, artifacts):
    refs = check.get("artifacts", {})
    if not isinstance(refs, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or v not in artifacts
        for k, v in refs.items()
    ):
        raise ValueError("Invalid saved artifact reference")
    for name in (*refs.keys(), *refs.values()):
        _filename(name)
    for key in ("stdout", "stderr"):
        if key in check and (not isinstance(check[key], str) or check[key] not in artifacts):
            raise ValueError("Invalid saved log reference")
    if "artifact_omissions" in check:
        for name in _strings(check["artifact_omissions"]):
            _filename(name)
    if "log_omissions" in check:
        streams = _strings(check["log_omissions"])
        if len(set(streams)) != len(streams) or any(
            name not in {"stdout", "stderr"} for name in streams
        ):
            raise ValueError("Invalid saved log omissions")


def _cases(check):
    if "required_cases" in check:
        names = _strings(check["required_cases"])
        if len(set(names)) != len(names) or any(not name for name in names):
            raise ValueError("Invalid saved required cases")
    if "case_results" in check:
        results = check["case_results"]
        if not isinstance(results, dict) or any(
            not isinstance(name, str) or not name or result not in _CASE_STATUSES
            for name, result in results.items()
        ):
            raise ValueError("Invalid saved case results")
        if "required_cases" in check and set(results) - set(check["required_cases"]):
            raise ValueError("Unexpected saved case result")


def _validate(record):
    if (
        not isinstance(record, dict)
        or record.get("kind") != "project-verification"
        or (type(record.get("schema_version")) is not int or record["schema_version"] != 2)
    ):
        raise ValueError("Unsupported saved verification record")
    for key in ("run_id", "revision", "base", "contract_digest", "runner"):
        _string(record.get(key))
    if any(
        not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", record[key])
        for key in ("revision", "base")
    ) or not re.fullmatch(r"[0-9a-f]{64}", record["contract_digest"]):
        raise ValueError("Invalid saved revision or contract digest")
    if record.get("status") not in evidence.STATUSES or record["runner"] not in {"host", "docker"}:
        raise ValueError("Invalid saved verification outcome")
    if "recorded_at" in record:
        timestamp = datetime.fromisoformat(_string(record["recorded_at"]))
        if timestamp.tzinfo is None:
            raise ValueError("Invalid saved timestamp")
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in artifacts.items()
    ):
        raise ValueError("Invalid saved artifact inventory")
    for name in artifacts:
        _filename(name)
    claims = record.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("Invalid saved claims")
    ids = set()
    for claim in claims:
        if not isinstance(claim, dict) or _string(claim.get("id")) in ids:
            raise ValueError("Invalid saved claims")
        ids.add(claim["id"])
        _string(claim.get("statement"))
        _strings(claim.get("assumptions", []))
    review = record.get("claim_review", {})
    if not isinstance(review, dict) or not isinstance(review.get("titles", {}), dict):
        raise ValueError("Invalid saved claim review")
    if any(k not in ids or not isinstance(v, str) for k, v in review.get("titles", {}).items()):
        raise ValueError("Invalid saved claim titles")
    _strings(review.get("verification_limits") or [])
    groups = review.get("groups", [])
    if not isinstance(groups, list):
        raise ValueError("Invalid saved claim groups")
    grouped = []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("Invalid saved claim groups")
        _string(group.get("title"))
        grouped.extend(_strings(group.get("claim_ids")))
    if groups and (len(grouped) != len(ids) or set(grouped) != ids):
        raise ValueError("Invalid saved claim group references")
    if (
        type(record.get("behavior_skipped")) is not bool
        or not isinstance(record.get("model_checks"), list)
        or not isinstance(record.get("cases"), list)
    ):
        raise ValueError("Invalid saved check inventory")
    seen = set()
    for check in record["model_checks"]:
        _check(check, ids, artifacts, "formal")
        key = check["id"]
        if key in seen:
            raise ValueError("Duplicate saved check")
        seen.add(key)
    checks = []
    for case in record["cases"]:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("checks"), list)
            or not case["checks"]
        ):
            raise ValueError("Invalid saved behavior cases")
        if "case_id" in case:
            _string(case["case_id"])
        if "status" in case and case["status"] not in evidence.STATUSES:
            raise ValueError("Invalid saved behavior case status")
        if len(case["checks"]) == 1:
            check = case["checks"][0]
            if (
                not isinstance(check, dict)
                or ("case_id" in case and case["case_id"] != check.get("id"))
                or ("status" in case and case["status"] != check.get("status"))
            ):
                raise ValueError("Invalid saved behavior case reference")
        checks.extend(case["checks"])
    if record["behavior_skipped"] and checks:
        raise ValueError("Invalid formal gate evidence")
    for check in checks:
        _check(check, ids, artifacts, "behavior")
        if check["id"] in seen:
            raise ValueError("Duplicate saved check")
        seen.add(check["id"])
    inventory = record.get("behavior_checks")
    if inventory is not None:
        if not isinstance(inventory, list):
            raise ValueError("Invalid saved behavior inventory")
        declared = {}
        for item in inventory:
            if (
                not isinstance(item, dict)
                or _string(item.get("id")) in declared
                or item.get("claim") not in ids
            ):
                raise ValueError("Invalid saved behavior inventory")
            declared[item["id"]] = item["claim"]
        if len(set(declared) & {check["id"] for check in record["model_checks"]}):
            raise ValueError("Duplicate saved check")
        if not record["behavior_skipped"] and declared != {
            check["id"]: check["claim"] for check in checks
        }:
            raise ValueError("Saved behavior inventory does not match results")
    return claim_groups(record)


def _accepted(state, record, digest):
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
        if (
            not isinstance(receipt, dict)
            or any(
                receipt.get(k) != v
                for k, v in {
                    "run_id": record["run_id"],
                    "revision": record["revision"],
                    "contract_digest": record["contract_digest"],
                    "manifest_digest": digest,
                }.items()
            )
            or record["status"] != "pass"
        ):
            raise ValueError
        accepted = datetime.fromisoformat(_string(receipt.get("accepted_at")))
        if accepted.tzinfo is None:
            raise ValueError
        return "Recorded at " + receipt["accepted_at"]
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
    parts = [
        f'<section class="check"><h5>{title} · {_status(status)}</h5>',
        f"<p>{_text(_diagnostic(check))}</p>",
    ]
    if kind == "formal":
        info = check.get("evidence", {})
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
        for kind in ("formal", "behavior")
        for check in claim[kind]
    ]
    counts = Counter(kind for _, kind, _ in all_checks)
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
    rows = [
        ("Original verdict", record["status"]),
        ("Candidate", record["revision"]),
        ("Contract base", record["base"]),
        ("Contract digest", record["contract_digest"]),
        ("Run ID", record["run_id"]),
        ("Runner", record["runner"]),
        ("Recorded at", record.get("recorded_at", "Unavailable")),
        ("Verified manifest SHA-256", manifest_digest),
        ("Local operator acceptance", acceptance),
    ]
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
            parts.extend(
                _check_html(check, kind) for kind in ("formal", "behavior") for check in claim[kind]
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
    findings_html = (
        "<ul>" + "".join(findings) + "</ul>"
        if findings
        else "<p>No primary violations or incomplete checks recorded.</p>"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
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
<section class="panel" id="overview"><h2>Overview</h2><dl>{overview}</dl><p>Acceptance records a local operator decision for the saved revision. It does not establish identity or current approval.</p>
<p>Primary checks: {counts["formal"]} formal, {behavior_count}; {case_count} required cases. Negative controls are excluded.</p>{gate}</section>
<section class="panel" id="findings"><h2>Primary findings</h2>{findings_html}</section>
<section class="panel" id="limits"><h2>Verification limits</h2>{limit_html}</section>
<section id="claims"><h2>Claims</h2>{metadata_html}<div class="controls" id="controls" hidden><label>Search title or ID<input id="search" type="search"></label><label>Status<select id="filter"><option value="">All</option><option value="pass">Pass</option><option value="violation">Violation</option><option value="incomplete">Incomplete</option><option value="skipped">Skipped</option></select></label></div><p id="count" hidden></p><p id="empty" hidden>No matching claims.</p>{"".join(areas)}</section>
<script>const controls=document.getElementById('controls'),search=document.getElementById('search'),filter=document.getElementById('filter'),count=document.getElementById('count'),empty=document.getElementById('empty'),claims=[...document.querySelectorAll('.claim')];controls.hidden=false;count.hidden=false;function update(){{let n=0;for(const item of claims){{const show=item.dataset.search.includes(search.value.toLowerCase())&&(!filter.value||item.dataset.status===filter.value);item.hidden=!show;if(show)n++;}}count.textContent=n+' of '+claims.length+' claims';empty.hidden=n!==0;}}search.addEventListener('input',update);filter.addEventListener('change',update);function reveal(hash){{if(!/^#claim-[1-9][0-9]*$/.test(hash))return;const target=document.getElementById(hash.slice(1));if(!claims.includes(target))return;search.value='';filter.value='';update();target.open=true;target.scrollIntoView();}}document.getElementById('findings').addEventListener('click',event=>{{const link=event.target.closest('a');if(link)reveal(link.getAttribute('href')||'');}});window.addEventListener('hashchange',()=>reveal(location.hash));update();reveal(location.hash);</script></main></body></html>"""


def render_run(state: Path, run_id: str, output: Path) -> None:
    try:
        state = evidence.state_directory(state, create=False)
        _, record, manifest_digest = evidence.read_run_snapshot(state, run_id)
        groups = _validate(record)
        content = _render(
            record, groups, manifest_digest, _accepted(state, record, manifest_digest)
        ).encode("utf-8")
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
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target, follow_symlinks=False)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except (KeyError, TypeError, IndexError, AttributeError, UnicodeError) as exc:
        raise ValueError("Invalid saved verification record") from exc
