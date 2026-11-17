from typer.testing import CliRunner

from mentat.cli import app


def test_version_is_eager_and_does_not_create_state(tmp_path):
    state = tmp_path / "state"
    for args in (["--version"], ["--state", str(state), "--version"]):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0
        assert result.stdout == "mentat 0.1.1\n"
        assert not state.exists()
