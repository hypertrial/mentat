from mentat import evidence


def _record():
    return {
        "kind": "project-verification",
        "run_id": "a" * 32,
        "revision": "b" * 40,
        "base": "a" * 40,
        "contract_digest": "c" * 64,
        "runner": "host",
        "status": "incomplete",
        "reproduce": "mentat verify",
        "claim_review": {
            "titles": {"other": "Second claim", "one": "First claim"},
            "groups": [
                {"title": "Authoring", "claim_ids": ["one"]},
                {"title": "Reviewing", "claim_ids": ["other"]},
            ],
            "verification_limits": ["This fixture covers one case."],
        },
        "claims": [
            {"id": "other", "statement": "Observe the other case"},
            {"id": "one", "statement": "Reject <secret> and ![image](https://example.invalid)"},
        ],
        "model_checks": [
            {
                "id": "model",
                "claim": "one",
                "kind": "z3",
                "status": "pass",
                "detail": "Model passed",
            }
        ],
        "behavior_checks": [{"id": "native", "claim": "one"}, {"id": "missing", "claim": "other"}],
        "behavior_skipped": False,
        "cases": [
            {
                "checks": [
                    {
                        "id": "native",
                        "claim": "one",
                        "status": "violation",
                        "detail": "Bad output",
                        "expected": 2,
                        "observed": 1,
                        "artifacts": {"witness.json": "witness.json"},
                    },
                    {
                        "id": "missing",
                        "claim": "other",
                        "status": "incomplete",
                        "detail": "Missing tool",
                    },
                ]
            }
        ],
    }


def test_report_groups_claims_and_preserves_contradiction_in_incomplete_run():
    report = evidence.report(_record()).decode()
    assert "- Result **incomplete**" in report
    assert "0 pass, 1 violation, 1 incomplete, 0 skipped" in report
    assert report.index("## Counterexample") < report.index("## Evidence gap")
    assert report.index("## Authoring") < report.index("## Reviewing")
    assert "### First claim · violation" in report
    assert "### Second claim · incomplete" in report
    assert "Formal (z3) `model` · **pass**" in report
    assert "Behavior `native` · **violation**" in report
    assert "[witness.json](./witness.json)" in report
    assert "Expected `2`" in report and "Observed `1`" in report
    assert r"\<secret\>" in report
    assert "![image]" not in report


def test_report_shows_formal_gate_and_supports_legacy_ungrouped_runs():
    record = _record()
    record.pop("claim_review")
    record.update(status="violation", behavior_skipped=True, cases=[])
    record["model_checks"][0].update(status="violation", detail="Counterexample")
    report = evidence.report(record).decode()
    assert "## Claims" in report
    assert "0 pass, 1 violation, 0 incomplete, 1 skipped" in report
    assert "Behavior `native` · **skipped**" in report
    assert "Behavior `missing` · **skipped**" in report
    record.pop("behavior_checks")
    legacy = evidence.report(record).decode()
    assert "Behavior checks were skipped" in legacy
    assert "0 pass, 1 violation, 0 incomplete, 1 skipped" in legacy


def test_terminal_verification_groups_claims_and_keeps_failure_details(capsys):
    from mentat.command import emit

    record = _record()
    emit(record, False)
    output = capsys.readouterr().out
    assert output.index("Mentat incomplete") < output.index("Authoring")
    assert output.index("Authoring") < output.index("Reviewing")
    assert "one · First claim  violation  Formal 1 pass  Behavior 1 violation" in output
    assert "other · Second claim  incomplete  Behavior 1 incomplete" in output
    assert "native  Bad output" in output
    assert "missing  Missing tool" in output

    record["behavior_skipped"] = True
    record["cases"] = []
    emit(record, False)
    skipped = capsys.readouterr().out
    assert "one · First claim  skipped  Formal 1 pass  Behavior 1 skipped" in skipped
    assert "other · Second claim  skipped  Behavior 1 skipped" in skipped


def test_native_regressions_render_diagnostics_and_accept_legacy_without_checks(capsys):
    from mentat.command import emit

    record = _record()
    record.update(kind="verification", cases=[], model_checks=[], advisory_results=["regressions"], regressions=[
        {"case_id": "current", "status": "incomplete", "checks": [
            {"name": "pytest", "status": "incomplete", "stage": "source copy",
             "detail": "Candidate source copy failed"},
            {"name": "cleanup", "status": "incomplete", "stage": "cleanup",
             "detail": "Container could not be removed"},
        ]},
        {"case_id": "legacy", "status": "pass"},
    ])
    emit(record, False)
    terminal = capsys.readouterr().out
    markdown = evidence.report(record).decode()
    for output in (terminal, markdown):
        assert "current" in output and "legacy" in output
        assert "advisory; candidate code may influence it" in output
        assert "Candidate source copy failed" in output
        assert "Container could not be removed" in output


def test_polaris_labels_follow_advisory_results_and_legacy_records_render_unchanged(capsys):
    from mentat.command import emit

    evaluation = {
        "kind": "evaluation", "run_id": "a" * 32, "revision": "b" * 40,
        "contract_digest": "c" * 64, "status": "pass", "reproduce": "example",
        "formal_seconds": 1.0, "native_regression": ["violation", "pass"],
        "counts": {"mentat": {"detected": 1}, "hypothesis": {"incomplete": 0}},
    }
    legacy = evidence.report(evaluation).decode()
    assert "- Native historical and repaired regression · violation / pass" in legacy
    assert "- hypothesis · 0 incomplete" in legacy
    assert "advisory" not in legacy and "candidate" not in legacy
    emit(evaluation, False)
    terminal = capsys.readouterr().out
    assert 'hypothesis  {"incomplete": 0}' in terminal
    assert "advisory" not in terminal and "candidate" not in terminal

    current = {**evaluation, "advisory_results": ["native_regression"]}
    markdown = evidence.report(current).decode()
    assert (
        "- Native historical and repaired regression (advisory; candidate code may influence it) "
        "· violation / pass" in markdown
    )
    assert "- hypothesis (baseline; candidate code may influence it) · 0 incomplete" in markdown
    assert "- mentat · 1 detected" in markdown
    emit(current, False)
    terminal = capsys.readouterr().out
    assert 'hypothesis (baseline; candidate code may influence it)  {"incomplete": 0}' in terminal
    assert "mentat  {" in terminal and "mentat (" not in terminal

    verification = {"kind": "verification", "regressions": [{"case_id": "legacy", "status": "pass"}]}
    emit(verification, False)
    assert "Native pytest  legacy  pass" in capsys.readouterr().out
    emit({**verification, "advisory_results": ["regressions"]}, False)
    assert "Native pytest (advisory; candidate code may influence it)  legacy  pass" in capsys.readouterr().out
