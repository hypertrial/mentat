#!/usr/bin/env python3
"""Build and replay bounded, exactly representable mixed-type sums."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
TYPED = re.compile(
    r'"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"\^\^<http://www\.w3\.org/2001/XMLSchema#(float|double)>'
)
INTEGER = re.compile(r"[+-]?\d+")


def validate(case):
    if not isinstance(case, dict) or set(case) != {"name", "grouped", "groups"}:
        raise ValueError("invalid case fields")
    if not isinstance(case["name"], str) or not re.fullmatch(r"[a-z0-9-]{1,40}", case["name"]):
        raise ValueError("invalid case name")
    if (
        type(case["grouped"]) is not bool
        or not isinstance(case["groups"], list)
        or not 1 <= len(case["groups"]) <= 3
    ):
        raise ValueError("invalid groups")
    if not case["grouped"] and len(case["groups"]) != 1:
        raise ValueError("ungrouped case needs exactly one group")
    ids = set()
    for group in case["groups"]:
        if (
            not isinstance(group, dict)
            or set(group) != {"id", "values"}
            or type(group["id"]) is not int
            or not 0 <= group["id"] <= 99
            or group["id"] in ids
        ):
            raise ValueError("invalid group id")
        ids.add(group["id"])
        values = group["values"]
        if not isinstance(values, list) or not 1 <= len(values) <= 3:
            raise ValueError("expected one to three values per group")
        seen = set()
        for value in values:
            if (
                not isinstance(value, dict)
                or set(value) != {"type", "half_units"}
                or value["type"] not in ("integer", "float", "double")
                or type(value["half_units"]) is not int
                or not -20 <= value["half_units"] <= 20
                or (value["type"] == "integer" and value["half_units"] % 2)
            ):
                raise ValueError("invalid value")
            key = (value["type"], value["half_units"])
            if key in seen:
                raise ValueError("values must be distinct within a group")
            seen.add(key)


def number(half):
    return str(Decimal(half) / 2)


def parse_value(text):
    if INTEGER.fullmatch(text):
        return ("integer", Decimal(text))
    match = TYPED.fullmatch(text)
    if not match:
        raise ValueError("malformed typed value")
    value = Decimal(match.group(1))
    if not value.is_finite():
        raise ValueError("nonfinite value")
    return (match.group(2), value)


def row_data(group, value, grouped):
    kind, num = value
    row = {"type": kind, "value": str(num) if num else "0"}
    if grouped:
        row["group"] = group
    return row


def run_case(nmo, case, root):
    work = root / case["name"]
    work.mkdir()
    lines = []
    expected = []
    for group in case["groups"]:
        gid = group["id"]
        kinds = set()
        total = 0
        for value in group["values"]:
            kind, half = value["type"], value["half_units"]
            kinds.add(kind)
            total += half
            if kind == "float":
                lines.append(f"raw({gid}, {number(half)}E0).")
            else:
                literal = str(half // 2) if kind == "integer" else number(half) + "E0"
                lines.append(f"v({gid}, {literal}).")
        result_type = "double" if "double" in kinds or len(kinds) > 1 else next(iter(kinds))
        expected.append((gid if case["grouped"] else None, result_type, Decimal(total) / 2))
    lines.append("v(?g, ?x) :- raw(?g, ?r), ?x = FLOAT(?r).")
    lines.append(
        "out(?g, #sum(?x)) :- v(?g, ?x)." if case["grouped"] else "out(#sum(?x)) :- v(?g, ?x)."
    )
    (work / "rules.rls").write_text("\n".join(lines) + "\n")
    result = {
        "case": case,
        "expected": [row_data(g, (t, n), case["grouped"]) for g, t, n in expected],
    }
    try:
        proc = subprocess.run(
            [str(nmo), "rules.rls", "--report", "none", "--export", "none", "--print-facts", "idb"],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        result.update(status="incomplete", exit=None, reason=str(error)[:4000])
        return result
    result["exit"] = proc.returncode
    if proc.returncode:
        result.update(status="incomplete", reason="unexpected Nemo exit", stderr=proc.stderr[:4000])
        return result
    if len(proc.stdout) > 16_384:
        result.update(status="incomplete", reason="Nemo output exceeds replay limit")
        return result
    actual = []
    try:
        for line in proc.stdout.splitlines():
            match = re.fullmatch(r"(v|out)\((.*)\)\.", line)
            if not match:
                raise ValueError("malformed Nemo output")
            predicate, contents = match.groups()
            if predicate == "v" or case["grouped"]:
                parts = contents.split(", ", 1)
                if len(parts) != 2 or not INTEGER.fullmatch(parts[0]):
                    raise ValueError("malformed grouped row")
                gid = int(parts[0])
                value = parse_value(parts[1])
            else:
                gid, value = None, parse_value(contents)
            if predicate == "out":
                actual.append((gid, *value))
    except (ValueError, InvalidOperation) as error:
        result.update(status="incomplete", reason=str(error)[:4000])
        return result
    result["actual"] = [row_data(g, (t, n), case["grouped"]) for g, t, n in actual]
    result["status"] = "pass" if Counter(actual) == Counter(expected) else "violation"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nmo", type=Path)
    parser.add_argument("--cases", type=Path, default=HERE / "cases.json")
    args = parser.parse_args()
    artifact_dir = os.environ.get("MENTAT_ARTIFACTS_DIR")
    results = []
    try:
        if artifact_dir:
            for source, name in ((Path(__file__), "replay.py"), (args.cases, "cases.json")):
                shutil.copyfile(source, Path(artifact_dir) / name)
        cases = json.loads(args.cases.read_text())
        if not isinstance(cases, list) or not 1 <= len(cases) <= 12:
            raise ValueError("cases must be a list of 1 to 12 cases")
        for case in cases:
            validate(case)
        if len({case["name"] for case in cases}) != len(cases):
            raise ValueError("case names must be unique")
        with tempfile.TemporaryDirectory(prefix="mentat-nemo-") as temp:
            root = Path(temp)
            if args.nmo:
                nmo = args.nmo.resolve(strict=True)
            else:
                env = os.environ.copy()
                env.update(
                    CARGO_HOME=str(root / "cargo"),
                    CARGO_TARGET_DIR=str(root / "target"),
                    CARGO_INCREMENTAL="0",
                    CARGO_PROFILE_DEV_DEBUG="0",
                    CARGO_NET_OFFLINE="true",
                )
                subprocess.run(
                    [
                        "cargo",
                        "--config",
                        "/opt/mentat-cargo/config.toml",
                        "build",
                        "--frozen",
                        "--jobs",
                        "1",
                        "-p",
                        "nemo-cli",
                        "--bin",
                        "nmo",
                    ],
                    env=env,
                    check=True,
                    timeout=500,
                )
                nmo = root / "target" / "debug" / "nmo"
            for case in cases:
                results.append(run_case(nmo, case, root))
        status = (
            2
            if any(r["status"] == "incomplete" for r in results)
            else int(any(r["status"] == "violation" for r in results))
        )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        InvalidOperation,
        subprocess.SubprocessError,
    ) as error:
        results.append({"status": "incomplete", "reason": str(error)[:4000]})
        status = 2
    output = (
        json.dumps(
            {"status": ("pass", "violation", "incomplete")[status], "results": results}, indent=2
        )
        + "\n"
    )
    if artifact_dir:
        try:
            (Path(artifact_dir) / "witness.json").write_text(output)
        except OSError as error:
            print(f"cannot write witness: {error}", file=sys.stderr)
            return 2
    print(output, end="")
    return status


if __name__ == "__main__":
    sys.exit(main())
