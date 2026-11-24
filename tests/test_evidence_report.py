from mentat.evidence_report import claim_groups, claim_summary


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
    for output in (terminal,):
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
    emit(evaluation, False)
    terminal = capsys.readouterr().out
    assert 'hypothesis  {"incomplete": 0}' in terminal
    assert "advisory" not in terminal and "candidate" not in terminal

    current = {**evaluation, "advisory_results": ["native_regression"]}
    emit(current, False)
    terminal = capsys.readouterr().out
    assert 'hypothesis (baseline; candidate code may influence it)  {"incomplete": 0}' in terminal
    assert "mentat  {" in terminal and "mentat (" not in terminal

    verification = {"kind": "verification", "regressions": [{"case_id": "legacy", "status": "pass"}]}
    emit(verification, False)
    assert "Native pytest  legacy  pass" in capsys.readouterr().out
    emit({**verification, "advisory_results": ["regressions"]}, False)
    assert "Native pytest (advisory; candidate code may influence it)  legacy  pass" in capsys.readouterr().out


def test_claim_groups_preserve_formal_and_behavior_outcomes():
    groups = claim_groups(_record())
    assert [title for title, _ in groups] == ["Authoring", "Reviewing"]
    first, second = groups[0][1][0], groups[1][1][0]
    assert first["status"] == "violation" and second["status"] == "incomplete"
    assert first["formal"][0]["status"] == "pass"
    assert first["behavior"][0]["expected"] == 2
    assert first["behavior"][0]["observed"] == 1
    assert first["behavior"][0]["artifacts"] == {"witness.json": "witness.json"}
    assert claim_summary(groups) == "0 pass, 1 violation, 1 incomplete, 0 skipped"


def test_formal_gate_preserves_skipped_behavior_inventory():
    record = _record()
    record.pop("claim_review")
    record.update(status="violation", behavior_skipped=True, cases=[])
    record["model_checks"][0]["status"] = "violation"
    groups = claim_groups(record)
    assert groups[0][0] == "Claims"
    checks = [check for claim in groups[0][1] for check in claim["behavior"]]
    assert {check["id"] for check in checks} == {"native", "missing"}
    assert all(check["status"] == "skipped" for check in checks)
