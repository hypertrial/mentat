"""Founder-run evaluation against frozen fixtures and known controls."""

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from time import monotonic

from mentat import evidence

from . import docker_runtime
from .regression import render_regression
from .replay import Snapshot, run_case, snapshot

BEFORE = "7bb319e281225b2b63c78f1cbc87726a1c9a7ece"
FIXED = "ba152c04f7f985bbaeb487a55ef442df52321afe"

_CONTROLS = {
    "drop-namespace": """target = max(entries, key=lambda entry: (len(entry["name"]), entry["name"]))
    return [entry for entry in entries if entry is not target]""",
    "change-property": """eligible = [entry for entry in entries if any(key in entry.get("properties", {}) for key in keys)]
    if not eligible:
        return entries
    target = max(eligible, key=lambda entry: (len(entry["name"]), entry["name"]))
    properties = target["properties"]
    prop = next(key for key in keys if key in properties)
    properties[prop] = "mentat-altered" if properties.get(prop) != "mentat-altered" else "mentat-changed"
    return entries""",
    "reorder": "return list(reversed(entries))",
}


def _score(rows: list[dict], method: str) -> dict[str, int]:
    counts = dict.fromkeys(
        ("detected", "missed", "false_positive", "passed_control", "incomplete", "ambiguous_alert"),
        0,
    )
    unmutated = {
        row.get("case"): row["methods"][method]["status"]
        for row in rows
        if row["variant"] == "candidate"
    }
    for row in rows:
        observed = row["methods"][method]["status"]
        label = (
            "incomplete"
            if observed == "incomplete"
            else {
                ("pass", "pass"): "passed_control",
                ("violation", "pass"): "missed",
                ("pass", "violation"): "false_positive",
                ("violation", "violation"): "detected",
            }[(row["expected"], observed)]
        )
        if label == "detected" and unmutated[row.get("case")] != "pass":
            label = "ambiguous_alert"
        counts[label] += 1
    return counts


def _control(base: Snapshot, control: str, keys: tuple[str, ...]) -> Snapshot:
    code = f"""

_mentat_original_export = SetupCommand._export_namespaces_for_catalog

def _mentat_control(command, *args, **kwargs):
    keys = {keys!r}
    entries = _mentat_original_export(command, *args, **kwargs)
    if not entries:
        return entries
    {_CONTROLS[control]}

SetupCommand._export_namespaces_for_catalog = _mentat_control
""".encode()
    files = tuple(
        (name, source + code if name == "command/setup.py" else source)
        for name, source in base.files
    )
    return replace(
        base,
        digest=evidence.digest(
            evidence.json_bytes([[name, evidence.digest(source)] for name, source in files])
        ),
        files=files,
    )


def _upstream_test(repo: Path, name: str) -> str:
    return subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repo),
            "show",
            f"{BEFORE}:client/python/tests/{name}",
        ],
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout.decode()


def _hypothesis(public, keys: tuple[str, ...]) -> str:
    source = render_regression(public, keys)
    paths = [(list(ns.path), dict(ns.properties)) for ns in public.namespaces]
    generated = f"""    prop = {keys[0]!r}
    source = [([left], {{}}), ([left, right], {{prop: value}}),
              ([left + "." + right], {{prop: value + "-literal"}})]
    if reverse:
        source.reverse()
"""
    return source.replace(
        "def test_setup_namespace_round_trip():",
        "from hypothesis import given, settings, strategies as st\n\n"
        "@settings(max_examples=12, derandomize=True, database=None, deadline=None)\n"
        "@given(left=st.text('abc', min_size=1, max_size=6),\n"
        "       right=st.text('xyz', min_size=1, max_size=6),\n"
        "       value=st.text('def', max_size=8), reverse=st.booleans())\n"
        "def test_setup_namespace_round_trip(left, right, value, reverse):",
    ).replace(f"    source = {paths!r}\n", generated, 1)


def evaluate(
    repo: Path, candidate_sha: str, context: str, spec, cases: list, public, run_id: str
) -> dict:
    before = snapshot(repo, BEFORE)
    fixed = snapshot(repo, candidate_sha)
    frozen = _upstream_test(repo, "test_setup_command.py")
    support = {"cli_test_utils.py": _upstream_test(repo, "cli_test_utils.py")}
    native_source = render_regression(public, spec.selected_properties)
    property_test = _hypothesis(public, spec.selected_properties)
    preflight = docker_runtime.doctor(context)

    def timed_check(check, *args):
        if not preflight["ok"]:
            return {
                "status": "incomplete",
                "seconds": 0.0,
                "detail": "Docker preflight failed; check the selected Colima context and run polaris doctor --prepare.",
            }
        start = monotonic()
        result = check(*args)
        return {"status": result["status"], "seconds": round(monotonic() - start, 3)}

    native = [
        docker_runtime.run_pytest(case, context, native_source, f"{run_id}-native-{index}")
        if preflight["ok"]
        else {"status": "incomplete"}
        for index, case in enumerate((before, fixed))
    ]
    rows = []
    baseline_rows = []
    candidates = [("historical", before, "violation"), ("candidate", fixed, "pass")]
    candidates.extend(
        (
            control,
            _control(fixed, control, spec.selected_properties),
            "pass" if control == "reorder" else "violation",
        )
        for control in _CONTROLS
    )
    for name, variant, expected in candidates:
        row = {"variant": name, "expected": expected, "snapshot_digest": variant.digest}
        methods = {}
        for method, test, extras in (
            ("upstream-pytest", frozen, support),
            ("hypothesis", property_test, None),
        ):
            methods[method] = timed_check(
                docker_runtime.run_pytest,
                variant,
                context,
                test,
                f"{run_id}-{name}-{method}",
                extras,
            )
        baseline_rows.append({**row, "methods": methods})
        for index, fixture in enumerate(cases, start=1):
            method_result = timed_check(
                run_case,
                variant,
                context,
                fixture,
                spec.selected_properties,
                f"{run_id}-{index}-{name}-m",
            )
            rows.append(
                {
                    **row,
                    "case": index,
                    "methods": {"mentat": method_result},
                }
            )
    checks = {method: _score(baseline_rows, method) for method in ("upstream-pytest", "hypothesis")}
    checks["mentat"] = _score(rows, "mentat")
    native_outcomes = [item["status"] for item in native]
    expected_native = ["violation", "pass"]
    outcome = "pass"
    if "incomplete" in native_outcomes or any(counts["incomplete"] for counts in checks.values()):
        outcome = "incomplete"
    elif (
        native_outcomes != expected_native
        or checks["mentat"]["false_positive"]
        or checks["mentat"]["missed"]
    ):
        outcome = "violation"
    return {
        "status": outcome,
        "native_regression": native_outcomes,
        "rows": rows,
        "baseline_rows": baseline_rows,
        "counts": checks,
    }
