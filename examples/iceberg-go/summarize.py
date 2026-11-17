#!/usr/bin/env python3
"""Validate and export the paired Iceberg compaction evidence."""

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
from pathlib import Path

from mentat.evidence import compare_runs, decode_json, digest, read_run

BEFORE = "410b789ed781852bbb4376e4f5cb57e76da12c56"
FIXED = "3fb910983b9674efc6092418af90a1c2b821dd42"
PRIMARY = "split=true/partial=true/filtered=false"
CASES = {
    f"split={str(s).lower()}/partial={str(p).lower()}/filtered={str(f).lower()}"
    for s in (False, True)
    for p in (False, True)
    for f in (False, True)
}
EXPECTED = [[n, "a"] for n in range(1, 13)]
BUNDLE = "iceberg-go-compaction-evidence.tar.gz"
SUMMARY = "iceberg-go-compaction-summary.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_artifact(files, check, name):
    return decode_json(files[check["artifacts"][name]])


def checked_run(state, run_id, revision, outcome):
    bundle, record = read_run(state, run_id)
    require(
        record["revision"] == revision
        and record["status"] == outcome
        and not record["behavior_skipped"],
        "wrong revision or incomplete outcome",
    )
    require([c["id"] for c in record["claims"]] == ["compaction.rows"], "wrong claim scope")
    checks = record["model_checks"]
    require(
        len(checks) == 2
        and {(c["id"], c["kind"]) for c in checks}
        == {("row-preservation-z3", "z3"), ("row-preservation-lean", "lean")}
        and all(c["claim"] == "compaction.rows" and c["status"] == "pass" for c in checks),
        "formal checks did not pass",
    )
    checks = [c for case in record["cases"] for c in case["checks"]]
    require(
        len(checks) == 1
        and checks[0]["id"] == "compaction-rows"
        and checks[0]["claim"] == "compaction.rows",
        "wrong behavior scope",
    )
    check = checks[0]
    require(
        check["status"] == outcome
        and check["command_status"] == outcome
        and check["exit_code"] == (10 if outcome == "violation" else 0),
        "incomplete native check",
    )
    require(
        set(check["required_cases"]) == CASES and len(check["required_cases"]) == len(CASES),
        "wrong native cases",
    )
    manifest = (bundle / "manifest.json").read_bytes()
    require(digest(manifest) == (state / "receipts" / run_id).read_text(), "manifest changed")
    files = {"manifest.json": manifest}
    for name, expected in record["artifacts"].items():
        require(
            Path(name).name == name and name not in (".", "..", "manifest.json"),
            "invalid artifact name",
        )
        path = bundle / name
        require(not path.is_symlink() and path.is_file(), "invalid artifact path")
        data = path.read_bytes()
        require(digest(data) == expected, "artifact changed")
        files[name] = data
    require(
        set(check["artifacts"]) == {"case-report.json", "witness.json", "native-observations.json"},
        "native artifacts missing",
    )
    report = load_artifact(files, check, "case-report.json")
    witness = load_artifact(files, check, "witness.json")
    require(
        set(report) == {"schema_version", "cases"}
        and type(report["schema_version"]) is int
        and report["schema_version"] == 1
        and set(report["cases"]) == CASES
        and set(report["cases"].values()) <= {"pass", "fail"}
        and report["cases"] == check["case_results"],
        "invalid native case report",
    )
    require(
        set(witness) == {"schema_version", "cases"}
        and type(witness["schema_version"]) is int
        and witness["schema_version"] == 1
        and set(witness["cases"]) == CASES,
        "invalid witness",
    )
    raw = load_artifact(files, check, "native-observations.json")
    require(
        isinstance(raw, dict) and raw.get("error") is None and isinstance(raw.get("output"), str),
        "invalid raw observations",
    )
    observations = raw["observations"]
    require(
        set(observations) == {"schema_version", "go_version", "cases"}
        and type(observations["schema_version"]) is int
        and observations["schema_version"] == 1
        and observations["go_version"] == "go1.26.3"
        and set(observations["cases"]) == CASES,
        "invalid Go observations",
    )
    for name, item in witness["cases"].items():
        require(
            set(item)
            == {"expected_rows", "committed_rows", "actual_rows", "operation_failed", "status"}
            and item["expected_rows"] == EXPECTED
            and type(item["operation_failed"]) is bool
            and isinstance(item["committed_rows"], list),
            "invalid witness case",
        )

        def rows(value):
            require(
                isinstance(value, list)
                and all(
                    isinstance(row, list)
                    and len(row) == 2
                    and type(row[0]) is int
                    and isinstance(row[1], str)
                    for row in value
                ),
                "invalid rows",
            )
            return sorted(value)

        actual = rows(item["actual_rows"])
        snapshots = [rows(snapshot) for snapshot in item["committed_rows"]]
        observed = observations["cases"][name]
        require(
            set(observed) == {"initial_rows", "committed_rows", "final_rows", "operation_error"}
            and isinstance(observed["operation_error"], str)
            and isinstance(observed["committed_rows"], list),
            "invalid Go case",
        )

        def observed_rows(value):
            require(
                isinstance(value, list)
                and all(
                    isinstance(row, dict)
                    and set(row) == {"id", "data"}
                    and type(row["id"]) is int
                    and isinstance(row["data"], str)
                    for row in value
                ),
                "invalid observed rows",
            )
            return sorted([[row["id"], row["data"]] for row in value])

        require(
            observed_rows(observed["initial_rows"]) == EXPECTED
            and observed_rows(observed["final_rows"]) == actual
            and [observed_rows(snapshot) for snapshot in observed["committed_rows"]] == snapshots
            and bool(observed["operation_error"]) == item["operation_failed"],
            "raw observations disagree with witness",
        )
        status = (
            "pass"
            if actual == EXPECTED and all(snapshot == EXPECTED for snapshot in snapshots)
            else "fail"
        )
        require(
            item["status"] == report["cases"][name] == status, "witness and case report disagree"
        )
    primary = witness["cases"][PRIMARY]
    require(
        (
            len(primary["actual_rows"]) == 1
            and primary["actual_rows"][0] in EXPECTED
            and primary["status"] == "fail"
        )
        if outcome == "violation"
        else (
            primary["actual_rows"] == EXPECTED
            and all(x == "pass" for x in report["cases"].values())
        ),
        "unexpected primary rows",
    )
    if outcome == "pass":
        control = check["negative_control"]
        require(
            control["qualified"] is True
            and control["status"] == "violation"
            and control["command_status"] == "violation"
            and control["exit_code"] == 10,
            "negative control did not qualify",
        )
        control_report = load_artifact(files, control, "case-report.json")
        control_witness = load_artifact(files, control, "witness.json")
        expected_cases = {name: "fail" if name == PRIMARY else "pass" for name in CASES}
        require(
            control_report == {"schema_version": 1, "cases": expected_cases}
            and control["case_results"] == expected_cases,
            "invalid negative control report",
        )
        expected_witness = json.loads(json.dumps(witness))
        expected_witness["cases"][PRIMARY]["actual_rows"] = [EXPECTED[0]]
        expected_witness["cases"][PRIMARY]["status"] = "fail"
        require(control_witness == expected_witness, "invalid negative control witness")
    result = {
        "revision": revision,
        "status": outcome,
        "rows": primary["actual_rows"],
        "operation_failed": primary["operation_failed"],
        "operation_error": observations["cases"][PRIMARY]["operation_error"],
        "operation_errors": {
            name: item["operation_error"]
            for name, item in observations["cases"].items()
            if item["operation_error"]
        },
        "native_cases": {
            "passed": list(report["cases"].values()).count("pass"),
            "failed": list(report["cases"].values()).count("fail"),
        },
        "models": {"lean": "pass", "z3": "pass"},
        "manifest_sha256": digest(manifest),
        "witness_sha256": digest(files[check["artifacts"]["witness.json"]]),
    }
    return record, files, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("state", "before", "fixed", "out"):
        parser.add_argument("--" + option, required=True)
    args = parser.parse_args()
    state, out = Path(args.state), Path(args.out)
    try:
        require(not out.exists() and not out.is_symlink(), "output already exists")
        comparison = compare_runs(state, args.before, args.fixed)
        require(
            comparison["status"] == "pass"
            and [c["id"] for c in comparison["claims"]] == ["compaction.rows"],
            "incomplete comparison",
        )
        before, before_files, before_summary = checked_run(state, args.before, BEFORE, "violation")
        fixed, fixed_files, fixed_summary = checked_run(state, args.fixed, FIXED, "pass")
        require(
            before["contract_digest"] == fixed["contract_digest"] == comparison["contract_digest"],
            "contract mismatch",
        )
        summary = {
            "schema_version": 1,
            "project": "apache/iceberg-go",
            "upstream_pr": "https://github.com/apache/iceberg-go/pull/2288",
            "claim": "compaction.rows",
            "contract_digest": comparison["contract_digest"],
            "expected_rows": EXPECTED,
            "before": before_summary,
            "fixed": fixed_summary,
            "evidence": {
                "bundle_file": BUNDLE,
                "walkthrough_url": "https://github.com/hypertrial/mentat/blob/v0.1.1/docs/iceberg-go.md",
                "download_url": "https://github.com/hypertrial/mentat/releases/download/v0.1.1/iceberg-go-compaction-evidence.tar.gz",
            },
        }
        out.mkdir()
        try:
            for side, files in (("before", before_files), ("fixed", fixed_files)):
                (out / side).mkdir()
                for name, data in files.items():
                    (out / side / name).write_bytes(data)
            for name in ("LICENSE", "NOTICE"):
                (out / name).write_bytes((Path(__file__).resolve().parent / name).read_bytes())
            with tarfile.open(out / BUNDLE, "w:gz") as archive:
                archive.add(out / "before", arcname="before")
                archive.add(out / "fixed", arcname="fixed")
                for name in ("LICENSE", "NOTICE"):
                    archive.add(out / name, arcname=name)
            summary["evidence"]["bundle_sha256"] = hashlib.sha256(
                (out / BUNDLE).read_bytes()
            ).hexdigest()
            (out / SUMMARY).write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
            (out / "SHA256SUMS").write_text(
                "".join(
                    f"{digest((out / name).read_bytes())}  {name}\n"
                    for name in (SUMMARY, BUNDLE, "LICENSE", "NOTICE")
                )
            )
        except Exception:
            shutil.rmtree(out)
            raise
        print(out)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(f"summary failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
