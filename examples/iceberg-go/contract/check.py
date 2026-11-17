#!/usr/bin/env python3
"""Run the approved Go probe against the staged candidate."""

import argparse
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

NAMES = [
    f"split={str(s).lower()}/partial={str(p).lower()}/filtered={str(f).lower()}"
    for s in (False, True)
    for p in (False, True)
    for f in (False, True)
]
PRIMARY = "split=true/partial=true/filtered=false"
EXPECTED = [(i, "a") for i in range(1, 13)]


def run_probe(env, raw):
    command = [
        "go",
        "test",
        "-json",
        "-mod=readonly",
        "-count=1",
        "-p=2",
        "-run",
        "^TestMentatCompactionRows$",
        "-v",
        "./table",
    ]
    limit = 65536
    deadline = time.monotonic() + 540
    output = bytearray()
    process = subprocess.Popen(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError("Go probe timed out")
                chunk = os.read(process.stdout.fileno(), min(8192, limit + 1 - len(output)))
                if not chunk:
                    selector.unregister(process.stdout)
                    break
                output.extend(chunk)
                if len(output) > limit:
                    del output[limit:]
                    raise ValueError("Go output exceeds limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Go probe timed out")
        return process.wait(timeout=remaining)
    finally:
        raw["output"] = output.decode("utf-8", errors="replace")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        process.stdout.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mod-cache", required=True)
    parser.add_argument("--build-cache", required=True)
    parser.add_argument("--negative-control", action="store_true")
    args = parser.parse_args()
    artifacts = Path(os.environ["MENTAT_ARTIFACTS_DIR"])
    report = {name: "error" for name in NAMES}
    witness = {}
    raw = {"output": "", "error": None}
    outcome = 2
    staged = Path("table/mentat_compaction_replay_test.go")
    created = False
    try:
        for cache in (args.mod_cache, args.build_cache):
            if not os.path.isabs(cache) or not Path(cache).is_dir():
                raise ValueError("cache paths must be existing absolute directories")
        if staged.exists() or staged.is_symlink():
            raise ValueError("probe destination already exists")
        with staged.open("x") as dest:
            created = True
            dest.write((Path(os.environ["MENTAT_CHECKS_DIR"]) / "probe_test.go").read_text())
        env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT")
            if key in os.environ
        }
        env.update(
            GOENV="off",
            GOTOOLCHAIN="local",
            GOWORK="off",
            GOPROXY="off",
            GOMODCACHE=args.mod_cache,
            GOCACHE=args.build_cache,
            GOMAXPROCS="2",
        )
        returncode = run_probe(env, raw)
        events = [json.loads(line) for line in raw["output"].splitlines()]
        if returncode != 0:
            raise ValueError("Go probe did not complete successfully")
        parent = "TestMentatCompactionRows"
        expected_tests = {parent} | {parent + "/" + name for name in NAMES}
        for test in expected_tests:
            actions = [
                e.get("Action")
                for e in events
                if e.get("Test") == test and e.get("Action") in ("run", "pass", "fail", "skip")
            ]
            if actions != ["run", "pass"]:
                raise ValueError("missing, duplicate, failed or skipped Go test: " + test)
        if any(
            e.get("Action") in ("run", "pass", "fail", "skip")
            and e.get("Test")
            and e["Test"] not in expected_tests
            for e in events
        ):
            raise ValueError("unexpected Go test")
        output = "".join(e.get("Output", "") for e in events if e.get("Action") == "output")
        lines = [
            line.removeprefix("MENTAT_OBSERVATIONS=")
            for line in output.splitlines()
            if line.startswith("MENTAT_OBSERVATIONS=")
        ]
        if len(lines) != 1:
            raise ValueError("missing or duplicate observations")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        observations = json.loads(lines[0], object_pairs_hook=unique)
        if (
            not isinstance(observations, dict)
            or set(observations) != {"schema_version", "go_version", "cases"}
            or type(observations["schema_version"]) is not int
            or observations["schema_version"] != 1
            or observations["go_version"] != "go1.26.3"
            or not isinstance(observations["cases"], dict)
            or set(observations["cases"]) != set(NAMES)
        ):
            raise ValueError("invalid observations")
        raw["observations"] = observations

        def rows(value):
            if not isinstance(value, list):
                raise ValueError("invalid rows")
            result = []
            for row in value:
                if (
                    not isinstance(row, dict)
                    or set(row) != {"id", "data"}
                    or type(row["id"]) is not int
                    or not isinstance(row["data"], str)
                ):
                    raise ValueError("invalid row")
                result.append((row["id"], row["data"]))
            return sorted(result)

        for name in NAMES:
            item = observations["cases"][name]
            if (
                not isinstance(item, dict)
                or set(item) != {"initial_rows", "committed_rows", "final_rows", "operation_error"}
                or not isinstance(item["committed_rows"], list)
                or not isinstance(item["operation_error"], str)
            ):
                raise ValueError("invalid observation")
            initial = rows(item["initial_rows"])
            if initial != EXPECTED:
                raise ValueError("fixture does not contain expected rows")
            snapshots = [rows(v) for v in item["committed_rows"]]
            final = rows(item["final_rows"])
            witness[name] = {
                "expected_rows": EXPECTED,
                "committed_rows": snapshots,
                "actual_rows": final,
                "operation_failed": bool(item["operation_error"]),
            }
        if args.negative_control:
            if any(
                v["actual_rows"] != EXPECTED or any(s != EXPECTED for s in v["committed_rows"])
                for v in witness.values()
            ):
                raise ValueError("negative control requires a passing native run")
            witness[PRIMARY]["actual_rows"] = [EXPECTED[0]]
        for name, item in witness.items():
            mismatch = item["actual_rows"] != EXPECTED or any(
                s != EXPECTED for s in item["committed_rows"]
            )
            report[name] = "fail" if mismatch else "pass"
            item["status"] = report[name]
        outcome = 10 if "fail" in report.values() else 2 if "error" in report.values() else 0
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        KeyError,
        json.JSONDecodeError,
    ) as error:
        raw["error"] = str(error)
        witness.clear()
        report = {name: "error" for name in NAMES}
    finally:
        if created:
            staged.unlink(missing_ok=True)
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "case-report.json").write_text(
            json.dumps({"schema_version": 1, "cases": report}, sort_keys=True) + "\n"
        )
        (artifacts / "witness.json").write_text(
            json.dumps({"schema_version": 1, "cases": witness}, sort_keys=True) + "\n"
        )
        (artifacts / "native-observations.json").write_text(json.dumps(raw, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "outcome": outcome,
                "failing_case": next((k for k, v in report.items() if v == "fail"), None),
                "error": raw["error"],
            }
        )
    )
    return outcome


if __name__ == "__main__":
    sys.exit(main())
