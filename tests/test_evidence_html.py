import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mentat import evidence
from mentat.cli import app

RUN = "a" * 32
REV = "b" * 40
CONTRACT = "c" * 64


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.parts = []
        self.scripts = []
        self.in_script = False
        self.definition = None
        self.label = ""
        self.definitions = {}
        self.summary = None
        self.summaries = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "script":
            self.in_script = True
        if tag in {"dt", "dd"}:
            self.definition = []
        if tag == "summary":
            self.summary = []

    def handle_endtag(self, tag):
        if tag == "summary" and self.summary is not None:
            self.summaries.append(" ".join(self.summary).lower())
            self.summary = None
        if tag == "dt" and self.definition is not None:
            self.label = " ".join(self.definition).strip()
            self.definition = None
        if tag == "dd" and self.definition is not None:
            self.definitions[self.label] = " ".join(self.definition).strip()
            self.definition = None
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.definition is not None:
            self.definition.append(data)
        if self.summary is not None:
            self.summary.append(data)
        if self.in_script:
            self.scripts.append(data)
        else:
            self.parts.append(data)

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())


def record(status="pass"):
    return {
        "schema_version": 2,
        "kind": "project-verification",
        "run_id": RUN,
        "revision": REV,
        "base": "d" * 40,
        "contract_digest": CONTRACT,
        "recorded_at": "2026-01-02T03:04:05Z",
        "repo": "/private/secret-repository",
        "runner": "host",
        "status": status,
        "reproduce": "SECRET_REPRODUCE /private/secret-repository",
        "host_reproduce": "SECRET_HOST_REPRODUCE",
        "runtime": {"secret": "SECRET_ENV_RUNTIME"},
        "claims": [
            {"id": "alpha", "statement": "Alpha requirement"},
            {"id": "beta", "statement": "Beta requirement"},
        ],
        "claim_review": {
            "titles": {"alpha": "Alpha title", "beta": "Beta title"},
            "groups": [
                {"title": "Second group", "claim_ids": ["beta"]},
                {"title": "First group", "claim_ids": ["alpha"]},
            ],
            "verification_limits": ["A bounded fixture limit"],
        },
        "model_checks": [
            {"id": "model", "claim": "alpha", "kind": "z3", "status": "pass", "detail": "ok"}
        ],
        "behavior_checks": [{"id": "native", "claim": "alpha"}, {"id": "other", "claim": "beta"}],
        "behavior_skipped": False,
        "cases": [{"case_id": "suite", "status": status, "checks": [
            {"id": "native", "claim": "alpha", "status": status, "detail": "unrecognized SECRET_STDERR /private/hidden",
             "required_cases": ["case one", "case two"], "case_results": {"case one": "pass", "case two": {"pass": "pass", "violation": "fail", "incomplete": "error"}[status]},
             "negative_control": {"status": "violation", "qualified": True, "detail": "Check reported a violation"}},
            {"id": "other", "claim": "beta", "status": "pass", "detail": "ok"},
        ]}],
    }


@pytest.fixture
def saved(tmp_path):
    state = evidence.state_directory(tmp_path / "state")

    def save(value=None, artifacts=None):
        return evidence.save_run(state, value or record(), artifacts or {})

    def render(output=None, run=RUN):
        output = output or tmp_path / "report.html"
        result = CliRunner().invoke(app, ["--state", str(state), "evidence", "render", run, "--out", str(output)])
        return result, output

    return state, save, render


def snapshot(path):
    return {str(p.relative_to(path)): ("link", os.readlink(p)) if p.is_symlink() else
            ("file", p.read_bytes()) if p.is_file() else ("dir",)
            for p in path.rglob("*")}


def read(result, output):
    assert result.exit_code == 0, (result.output, result.exception)
    assert output.is_file()
    return Document(output.read_text(encoding="utf-8"))


@pytest.mark.parametrize("outcome", ["pass", "violation", "incomplete"])
def test_render_intact_outcomes_without_touching_state(saved, outcome):
    state, save, render = saved
    save(record(outcome))
    before = snapshot(state)
    doc = read(*render())
    assert outcome in doc.text.lower()
    assert RUN in doc.text and REV in doc.text and CONTRACT in doc.text
    assert "Alpha requirement" in doc.text and "Beta requirement" in doc.text
    assert doc.text.index("Second group") < doc.text.index("First group")
    assert "case one" in doc.text and "case two" in doc.text
    assert "qualified" in doc.text.lower()
    assert snapshot(state) == before


def test_mixed_same_claim_incomplete_and_violation_remain_visible(saved):
    _, save, render = saved
    value = record("incomplete")
    value["cases"][0]["checks"][0]["status"] = "violation"
    value["cases"][0]["checks"].append(
        {"id": "missing-check", "claim": "alpha", "status": "incomplete", "detail": "missing"}
    )
    value["behavior_checks"].append({"id": "missing-check", "claim": "alpha"})
    save(value)
    doc = read(*render())
    text = doc.text.lower()
    assert "incomplete" in text and "violation" in text
    assert "native" in text and "missing-check" in text
    assert any("alpha title" in summary and "incomplete" in summary for summary in doc.summaries)


@pytest.mark.parametrize("qualified", [True, False])
def test_control_qualification_is_shown(saved, qualified):
    _, save, render = saved
    value = record()
    control = value["cases"][0]["checks"][0]["negative_control"]
    control["qualified"] = qualified
    control["status"] = "violation" if qualified else "pass"
    save(value)
    text = read(*render()).text.lower()
    assert "negative control" in text
    assert ("qualified" if qualified else "not qualified") in text


def test_missing_case_inventory_is_unavailable_instead_of_zero(saved):
    _, save, render = saved
    value = record()
    for check in value["cases"][0]["checks"]:
        check.pop("required_cases", None)
        check.pop("case_results", None)
    save(value)
    text = read(*render()).text.lower()
    assert "unavailable" in text
    assert "0 required cases" not in text


def test_formal_gate_and_legacy_missing_inventory_do_not_invent_behavior(saved):
    _, save, render = saved
    value = record("violation")
    value["model_checks"][0]["status"] = "violation"
    value.update(behavior_skipped=True, cases=[])
    save(value)
    text = read(*render()).text.lower()
    assert "skipped" in text and "native" in text and "other" in text


def test_formal_gate_missing_inventory_reports_unknown_behavior_count(saved):
    _, save, render = saved
    value = record("violation")
    value["model_checks"][0]["status"] = "violation"
    value.update(behavior_skipped=True, cases=[])
    value.pop("behavior_checks")
    save(value)
    text = read(*render()).text.lower()
    counts = re.search(r"primary checks:[^.]*", text)
    assert counts is not None
    assert "behavior" in counts.group() and "unavailable" in counts.group()
    assert not re.search(r"\b0\s+behavior\b", counts.group())


def test_formal_gate_known_inventory_counts_skipped_checks(saved):
    _, save, render = saved
    value = record("violation")
    value["model_checks"][0]["status"] = "violation"
    value.update(behavior_skipped=True, cases=[])
    save(value)
    text = read(*render()).text.lower()
    counts = re.search(r"primary checks:[^.]*", text)
    assert counts is not None and re.search(r"\b2\s+(?:\(skipped\)\s+)?behavior\b", counts.group())
    assert re.search(r"native\s*[·:\-]\s*skipped", text)
    assert re.search(r"other\s*[·:\-]\s*skipped", text)


@pytest.mark.parametrize("control", [False, True], ids=["primary", "negative-control"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_log_omissions_are_explicitly_identified(saved, control, stream):
    _, save, render = saved
    value = record()
    check = value["cases"][0]["checks"][0]
    if control:
        check = check["negative_control"]
    check["log_omissions"] = [stream]
    check["stderr" if stream == "stdout" else "stdout"] = "raw.log"
    save(value, {"raw.log": b"SECRET_RAW_LOG_CONTENT"})
    text = read(*render()).text.lower()
    assert re.search(rf"(?:omitted|omission)\w*[^.]*\b{stream}\b|\b{stream}\b[^.]*(?:omitted|omission)", text)
    assert "secret_stderr" not in text and "secret_raw_log_content" not in text


@pytest.mark.parametrize("control", [False, True], ids=["primary", "negative-control"])
@pytest.mark.parametrize("bad", ["mystery", "../private.log", '<img onerror="alert(1)">'])
def test_invalid_log_omissions_are_rejected(saved, control, bad):
    state, save, render = saved
    value = record()
    check = value["cases"][0]["checks"][0]
    if control:
        check = check["negative_control"]
    check["log_omissions"] = [bad]
    save(value)
    before = snapshot(state)
    result, output = render()
    assert result.exit_code == 2, (result.output, result.exception)
    assert not output.exists()
    assert snapshot(state) == before


def test_legacy_missing_optional_fields_are_unavailable(saved):
    _, save, render = saved
    value = record()
    value.pop("recorded_at")
    value.pop("claim_review")
    value.pop("behavior_checks")
    value["behavior_skipped"] = True
    value["cases"] = []
    save(value)
    text = read(*render()).text.lower()
    assert "unavailable" in text and "skipped" in text
    assert "review area metadata unavailable" in text
    assert "claim title metadata unavailable" in text


def receipt(state, **overrides):
    data = {"run_id": RUN, "revision": REV, "contract_digest": CONTRACT,
            "manifest_digest": (state / "receipts" / RUN).read_text(),
            "accepted_at": "2026-01-02T03:04:05Z", **overrides}
    path = state / "acceptances" / f"{RUN}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def acceptance(doc):
    values = [value.lower() for label, value in doc.definitions.items() if "acceptance" in label.lower()]
    assert len(values) == 1
    return values[0]


def test_matching_acceptance_and_raw_manifest_bytes_survive_removed_repository(saved, tmp_path):
    state, save, render = saved
    repo = tmp_path / "removed-repository"
    repo.mkdir()
    value = record()
    value["repo"] = str(repo)
    bundle = save(value)
    repo.rmdir()
    approvals = state / "approvals"
    approvals.mkdir()
    (approvals / "changed.json").write_text('{"contract_digest":"new"}')
    manifest = bundle / "manifest.json"
    manifest.write_text(json.dumps(json.loads(manifest.read_text()), indent=2) + "\n")
    (state / "receipts" / RUN).write_text(evidence.digest(manifest.read_bytes()))
    receipt(state)
    before = snapshot(state)
    doc = read(*render())
    assert "recorded" in acceptance(doc) and "2026-01-02t03:04:05z" in acceptance(doc)
    assert str(repo) not in doc.text
    assert snapshot(state) == before


@pytest.mark.parametrize("field", ["run_id", "revision", "contract_digest", "manifest_digest", "accepted_at"])
def test_invalid_acceptance_binding_is_visible_but_does_not_block_render(saved, field):
    state, save, render = saved
    save()
    receipt(state, **{field: "invalid"})
    before = snapshot(state)
    status = acceptance(read(*render()))
    assert "invalid" in status or "unavailable" in status
    assert snapshot(state) == before


@pytest.mark.parametrize("case", ["missing", "malformed", "directory", "symlink", "nonpassing"])
def test_acceptance_problems_do_not_make_report_accepted(saved, case, tmp_path):
    state, save, render = saved
    save(record("violation" if case == "nonpassing" else "pass"))
    if case != "missing":
        path = receipt(state)
        if case == "malformed":
            path.write_text("{")
        if case == "directory":
            path.unlink()
            path.mkdir()
        if case == "symlink":
            target = tmp_path / "outside-receipt"
            target.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(target)
    before = snapshot(state)
    status = acceptance(read(*render()))
    if case == "missing":
        assert "missing" in status or "not recorded" in status or "unavailable" in status
    else:
        assert "invalid" in status or "unavailable" in status
    assert snapshot(state) == before


@pytest.mark.parametrize("damage", ["manifest", "artifact", "extra", "artifact-symlink", "manifest-symlink"])
def test_tampered_bundle_fails_without_publishing(saved, damage, tmp_path):
    state, save, render = saved
    bundle = save(artifacts={"result.json": b"original"})
    if damage == "manifest":
        (bundle / "manifest.json").write_text("{}")
    elif damage == "artifact":
        (bundle / "result.json").write_text("changed")
    elif damage == "extra":
        (bundle / "extra").write_text("extra")
    else:
        path = bundle / ("result.json" if damage == "artifact-symlink" else "manifest.json")
        target = tmp_path / "replacement"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    before = snapshot(state)
    result, output = render()
    assert result.exit_code == 2, (result.output, result.exception)
    assert not output.exists()
    assert snapshot(state) == before


@pytest.mark.parametrize("case", ["existing", "symlink", "missing-parent", "inside-state", "linked-parent"])
def test_rejects_unsafe_output_paths_without_overwriting(saved, tmp_path, case):
    state, save, render = saved
    save()
    output = tmp_path / "report.html"
    if case == "existing":
        output.write_text("keep")
    elif case == "symlink":
        target = tmp_path / "target"
        target.write_text("keep")
        output.symlink_to(target)
    elif case == "missing-parent":
        output = tmp_path / "missing" / "report.html"
    elif case == "inside-state":
        output = state / "report.html"
    else:
        link = tmp_path / "linked"
        link.symlink_to(state, target_is_directory=True)
        output = link / "report.html"
    before = snapshot(state)
    result, _ = render(output)
    assert result.exit_code == 2, (result.output, result.exception)
    if case == "existing":
        assert output.read_text() == "keep"
    elif case == "symlink":
        assert output.is_symlink() and target.read_text() == "keep"
    else:
        assert not output.exists()
    assert snapshot(state) == before


def test_hostile_values_are_text_and_private_fields_are_not_rendered(saved):
    _, save, render = saved
    value = record("violation")
    value["claims"][0]["statement"] = '<script>alert("x")</script><img src="https://evil.invalid/x">'
    value["claim_review"]["titles"]["alpha"] = '<svg onload="alert(1)">'
    value["cases"][0]["checks"][0].update(
        required_cases=['case <img src="https://evil.invalid">'],
        case_results={'case <img src="https://evil.invalid">': "fail"},
        witness={"secret": "SECRET_WITNESS"},
        artifacts={'<img onerror="alert(2)">': 'payload"><img onerror="alert(3)">.txt'},
    )
    save(value, {'payload"><img onerror="alert(3)">.txt': b"SECRET_ARTIFACT_LOG"})
    result, output = render()
    doc = read(result, output)
    assert '<script>alert("x")</script>' in doc.text
    assert '<svg onload="alert(1)">' in doc.text
    assert 'payload"><img onerror="alert(3)">.txt' in doc.text
    assert not any(tag in {"img", "svg", "iframe", "object"} for tag, _ in doc.tags)
    assert all("alert(" not in script and "evil.invalid" not in script for script in doc.scripts)
    for _, attrs in doc.tags:
        for name in ("href", "src", "action"):
            assert name not in attrs or attrs[name].startswith("#")
    for secret in ("SECRET_REPRODUCE", "SECRET_HOST_REPRODUCE", "SECRET_ENV_RUNTIME", "SECRET_STDERR", "SECRET_WITNESS", "SECRET_ARTIFACT_LOG", "/private/secret-repository", "/private/hidden"):
        assert secret not in output.read_text()


def change_intact_manifest(state, mutate):
    path = state / "runs" / RUN / "manifest.json"
    value = json.loads(path.read_text())
    mutate(value)
    path.write_bytes(evidence.json_bytes(value))
    (state / "receipts" / RUN).write_text(evidence.digest(path.read_bytes()))


@pytest.mark.parametrize("damage", ["kind", "revision", "revision-path", "base-path", "digest-path", "claim", "group", "artifact", "omission-path"])
def test_invalid_intact_metadata_is_rejected(saved, damage):
    state, save, render = saved
    save()

    def mutate(value):
        if damage == "kind":
            value["kind"] = "evaluation"
        elif damage == "revision":
            value.pop("revision")
        elif damage == "revision-path":
            value["revision"] = "/private/revision"
        elif damage == "base-path":
            value["base"] = "/private/base"
        elif damage == "digest-path":
            value["contract_digest"] = "/private/digest"
        elif damage == "omission-path":
            value["cases"][0]["checks"][0]["artifact_omissions"] = ["/private/not-an-artifact"]
        elif damage == "claim":
            value["model_checks"][0]["claim"] = "unknown"
        elif damage == "group":
            value["claim_review"]["groups"] = [{"title": "Incomplete", "claim_ids": ["alpha"]}]
        else:
            value["cases"][0]["checks"][0]["artifacts"] = {"witness": "missing.json"}

    change_intact_manifest(state, mutate)
    before = snapshot(state)
    result, output = render()
    assert result.exit_code == 2, (result.output, result.exception)
    assert not output.exists()
    assert snapshot(state) == before


def test_link_failure_does_not_leave_partial_report(saved, monkeypatch, tmp_path):
    state, save, render = saved
    save()
    before = snapshot(state)
    files = {p.name for p in tmp_path.iterdir()}

    def denied(*args, **kwargs):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(os, "link", denied)
    result, output = render()
    assert result.exit_code == 2, (result.output, result.exception)
    assert not output.exists()
    assert {p.name for p in tmp_path.iterdir()} == files
    assert snapshot(state) == before


def test_candidate_selfcheck_covers_saved_claim_results():
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-I", str(root / ".mentat/selfcheck.py"), "evidence.claim-results"],
        env={**os.environ, "MENTAT_CANDIDATE_DIR": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_omitted_artifact_is_not_presented_as_verified_file(saved):
    _, save, render = saved
    value = record()
    value["cases"][0]["checks"][0].update(
        artifacts={"present.txt": "present.txt"}, artifact_omissions=["not-written.txt"]
    )
    save(value, {"present.txt": b"verified"})
    text = read(*render()).text.lower()
    assert "present.txt" in text
    if "not-written.txt" in text:
        before = text[max(0, text.index("not-written.txt") - 80) : text.index("not-written.txt")]
        assert "omitted" in before or "unavailable" in before


def test_publish_race_does_not_overwrite_competing_file(saved, monkeypatch, tmp_path):
    state, save, render = saved
    save()
    before = snapshot(state)
    files = {p.name for p in tmp_path.iterdir()}

    def raced(source, destination, **kwargs):
        Path(destination).write_text("competitor")
        raise FileExistsError("synthetic race")

    monkeypatch.setattr(os, "link", raced)
    result, output = render()
    assert result.exit_code == 2, (result.output, result.exception)
    assert output.read_text() == "competitor"
    assert {p.name for p in tmp_path.iterdir()} == files | {output.name}
    assert snapshot(state) == before


def test_unreadable_acceptance_is_invalid_without_blocking_report(saved, monkeypatch):
    state, save, render = saved
    save()
    path = receipt(state)
    original = os.open

    def denied(name, *args, **kwargs):
        if Path(name) == path:
            raise PermissionError("synthetic unreadable receipt")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    before = snapshot(state)
    assert "invalid" in acceptance(read(*render()))
    assert snapshot(state) == before
