"""Read OpenSpec requirements from an immutable Git revision as draft claims."""

import hashlib
import subprocess
import time
from contextlib import closing
from itertools import pairwise
from pathlib import Path

from markdown_it import MarkdownIt

from .gitio import MAX_FILES, MAX_TOTAL, ls_tree, read_blobs, repo_root, verify_revision
from .project import safe_git_path


def _read_failure(exc: Exception) -> str:
    # Only ValueError messages are fixed text; OS and subprocess errors may carry paths or argv.
    return str(exc) if isinstance(exc, ValueError) else "git blob read failed"


def import_spec(repo: Path, base: str) -> dict:
    root = repo_root(repo)
    revision = verify_revision(root, base)
    diagnostics = []
    claims = []
    entries = {}
    deadline = time.monotonic() + 60

    def diagnostic(path, line, detail):
        if len(diagnostics) < MAX_FILES:
            diagnostics.append({"path": path, "line": line, "detail": detail})

    with closing(
        ls_tree(root, revision, "-rtz", [b":(literal)openspec/specs"], deadline)
    ) as records:
        for record in records:
            meta, raw_path = record.split(b"\t", 1)
            mode, kind, oid = meta.split(b" ")
            linked = mode in (b"120000", b"160000")
            is_spec = raw_path.endswith(b"/spec.md")
            if not (linked or is_spec or (raw_path == b"openspec/specs" and kind != b"tree")):
                continue
            try:
                path = safe_git_path(raw_path)
            except ValueError:
                diagnostic(raw_path.decode("utf-8", "backslashreplace"), 1, "unsafe spec path")
                continue
            if (
                len(raw_path.split(b"/")) < 4
                or mode not in (b"100644", b"100755")
                or kind != b"blob"
            ):
                diagnostic(path, 1, "unsupported spec entry")
                continue
            if len(entries) >= MAX_FILES:
                diagnostic(path, 1, "too many spec files")
                break
            entries[path] = (int(mode, 8) & 0o777, oid)

    if not entries and not diagnostics:
        diagnostic("openspec/specs", 1, "no capability specs found")

    def read_specs():
        ordered = sorted(entries.items())
        for start in range(0, len(ordered), 32):
            batch = ordered[start : start + 32]
            if time.monotonic() >= deadline:
                yield batch[0][0], None, None, "spec import exceeded time limit", True
                return
            try:
                blobs = read_blobs(root, dict(batch))
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                if len(batch) == 1:
                    yield batch[0][0], None, None, _read_failure(exc), False
                    continue
                # Retry one file at a time. If no file reads, the failure is not file-specific.
                outcomes = []
                for path, entry in batch:
                    if time.monotonic() >= deadline:
                        outcomes.append((path, None, None, "spec import exceeded time limit", True))
                        break
                    try:
                        data = read_blobs(root, {path: entry})[path][1]
                    except (ValueError, OSError, subprocess.SubprocessError) as file_exc:
                        outcomes.append((path, None, None, _read_failure(file_exc), False))
                    else:
                        outcomes.append((path, entry, data, None, False))
                stopped = outcomes[-1][4]
                if not stopped and all(item[3] is not None for item in outcomes):
                    yield batch[0][0], None, None, _read_failure(exc), True
                    return
                yield from outcomes
                if stopped:
                    return
                continue
            for path, entry in batch:
                yield path, entry, blobs.pop(path)[1], None, False

    parser = MarkdownIt("commonmark")
    total = 0
    for path, entry, data, error, stop in read_specs():
        if error is not None:
            diagnostic(path, 1, error)
            if stop:
                break
            continue
        if time.monotonic() >= deadline:
            diagnostic(path, 1, "spec import exceeded time limit")
            break
        try:
            total += len(data)
            if total > MAX_TOTAL:
                diagnostic(path, 1, "spec files exceed total size limit")
                break
            source = data.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            diagnostic(path, 1, str(exc))
            continue
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        lines = source.removesuffix("\n").split("\n")
        tokens = parser.parse(source)
        headings = []
        for index, token in enumerate(tokens):
            if token.type == "heading_open" and token.level == 0:
                level = int(token.tag[1:])
                heading = tokens[index + 1].content
                if (
                    heading.startswith("Requirement:")
                    and level != 3
                    or heading.startswith("Scenario:")
                    and level != 4
                ):
                    diagnostic(path, token.map[0] + 1, "unsupported OpenSpec heading level")
                if level <= 4:
                    headings.append((level, heading, token.map[0]))

        def nonempty(start, end):
            return any(
                token.type in ("inline", "fence", "code_block") and token.content.strip()
                for token in parser.parse("\n".join(lines[start:end]))
            )

        seen = set()
        requirements = 0
        boundaries = [-1, *(i for i, item in enumerate(headings) if item[0] <= 3), len(headings)]
        for i, end_index in pairwise(boundaries):
            subsections = headings[i + 1 : end_index]
            scenarios = [
                (j, heading, line)
                for j, (level, heading, line) in enumerate(subsections)
                if level == 4
            ]
            level, heading, start = headings[i] if i >= 0 else (0, "", 0)
            if level != 3 or not heading.startswith("Requirement"):
                for _, label, line in scenarios:
                    if label.startswith("Scenario:"):
                        diagnostic(path, line + 1, "orphan scenario")
                continue
            requirements += 1
            end = headings[end_index][2] if end_index < len(headings) else len(lines)
            name = (
                heading.removeprefix("Requirement:").strip()
                if heading.startswith("Requirement:")
                else ""
            )
            valid = True
            if not name:
                diagnostic(path, start + 1, "malformed requirement heading")
                valid = False
            elif name in seen:
                diagnostic(path, start + 1, "duplicate requirement name")
                valid = False
            seen.add(name)
            first = subsections[0][2] if subsections else end
            if not nonempty(start + 1, first):
                diagnostic(path, start + 1, "missing requirement body")
                valid = False
            if not scenarios:
                diagnostic(path, start + 1, "missing scenario")
            scenario_names = set()
            for section_index, label, line in scenarios:
                scenario_name = label.removeprefix("Scenario:").strip()
                scenario_valid = valid
                if not scenario_name:
                    diagnostic(path, line + 1, "malformed scenario heading")
                    scenario_valid = False
                elif scenario_name in scenario_names:
                    diagnostic(path, line + 1, "duplicate scenario name")
                    scenario_valid = False
                scenario_names.add(scenario_name)
                if section_index + 1 < len(subsections):
                    scenario_end = subsections[section_index + 1][2]
                else:
                    scenario_end = end
                if not nonempty(line + 1, scenario_end):
                    diagnostic(path, line + 1, "missing scenario body")
                    scenario_valid = False
                title = name + ": " + scenario_name
                statement = (
                    "\n".join(lines[start + 1 : first]).strip()
                    + "\n\n"
                    + "\n".join(lines[line:scenario_end]).strip()
                )
                if (
                    len(title) > 256
                    or len(title.splitlines()) != 1
                    or any(ord(c) < 32 for c in title)
                    or len(statement) > 4096
                ):
                    diagnostic(path, line + 1, "claim field exceeds limits")
                    scenario_valid = False
                if scenario_valid:
                    if len(claims) >= 128:
                        diagnostic(path, line + 1, "too many claims")
                        continue
                    identity = hashlib.sha256(
                        (path + "\0" + name + "\0" + scenario_name).encode()
                    ).hexdigest()

                    def citation(begin, finish):
                        return {
                            "commit": revision,
                            "blob": entry[1].decode("ascii"),
                            "path": path,
                            "start_line": begin + 1,
                            "end_line": finish,
                        }

                    claims.append(
                        {
                            "claim": {
                                "id": "openspec." + identity,
                                "title": title,
                                "statement": statement,
                                "provenance": {
                                    "origin": "declared",
                                    "citations": [
                                        citation(start, first),
                                        citation(line, scenario_end),
                                    ],
                                },
                            },
                            "requires_behavior_check": True,
                        }
                    )
        if not requirements:
            diagnostic(path, 1, "no requirements found")
    return {
        "schema_version": 1,
        "status": "incomplete" if diagnostics else "pass",
        "revision": revision,
        "claims": claims,
        "diagnostics": diagnostics,
    }
