from typer.testing import CliRunner

from soundcloud_downloader.cli.main import app


def test_oauth_command_group_is_available() -> None:
    result = CliRunner().invoke(app, ["oauth", "--help"])

    assert result.exit_code == 0
    assert "OAuth helper commands." in result.output
