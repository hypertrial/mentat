"""The required hosted check stays lightweight; verification runs locally."""

from pathlib import Path


def test_ci_contains_only_the_lightweight_required_check():
    workflows = Path(__file__).parents[1] / ".github" / "workflows"
    assert sorted(path.name for path in workflows.iterdir()) == ["ci.yml"]
    text = (workflows / "ci.yml").read_text()
    assert "name: Mentat checks" in text
    assert "timeout-minutes: 2" in text
    assert "contents: read" in text
    assert "persist-credentials: false" in text
    assert "fetch-depth: 2" in text
    assert "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803" in text
    assert "compile(" in text and "dont_inherit=True" in text
    assert "git diff --check HEAD^1 HEAD" in text
    for forbidden in ("setup-uv", "uv sync", "pytest", "docker", "curl", "workflow_dispatch"):
        assert forbidden not in text
