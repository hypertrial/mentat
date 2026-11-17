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
