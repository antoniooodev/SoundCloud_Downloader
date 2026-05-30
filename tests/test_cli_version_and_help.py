from collections.abc import Iterator
from pathlib import Path
import re

import pytest
from typer.testing import CliRunner

import soundcloud_downloader.cli.doctor as doctor_cli
from soundcloud_downloader.cli.main import app

SECRET_TERMS = (
    "access_token",
    "refresh_token",
)
CLIENT_SECRET_REAL_VALUE_PATTERNS = (
    "client_secret=",
    "client_secret:",
    "client-secret=",
)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
DETERMINISTIC_CLI_ENV = {
    "NO_COLOR": "1",
    "TERM": "dumb",
    "COLUMNS": "160",
}


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def forbid_network_and_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("help/version smoke tests must not execute I/O")

    monkeypatch.setattr(doctor_cli.shutil, "which", fail_if_called)


@pytest.fixture(autouse=True)
def no_file_writes(tmp_path: Path) -> Iterator[None]:
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    yield
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert after == before


def invoke(runner: CliRunner, *args: str) -> str:
    result = runner.invoke(app, list(args), color=False, env=DETERMINISTIC_CLI_ENV)
    assert result.exit_code == 0, result.output
    return result.output


def assert_no_secret_terms(output: str) -> None:
    normalized = output.lower()
    for term in SECRET_TERMS:
        assert term not in normalized
    for pattern in CLIENT_SECRET_REAL_VALUE_PATTERNS:
        assert pattern not in normalized


def command_exists(help_output: str, command_name: str) -> bool:
    return command_name in normalize_help_output(help_output)


def normalize_help_output(output: str) -> str:
    output = ANSI_RE.sub("", output)
    for character in ("│", "╭", "╮", "╰", "╯"):
        output = output.replace(character, " ")
    return " ".join(output.split())


def test_root_help_exits_zero_and_lists_core_commands(runner: CliRunner) -> None:
    output = invoke(runner, "--help")

    assert command_exists(output, "doctor")
    assert command_exists(output, "download")
    assert_no_secret_terms(output)


def test_root_version_exits_zero_and_prints_package_name(runner: CliRunner) -> None:
    output = invoke(runner, "--version")

    assert "soundcloud-downloader" in output
    assert_no_secret_terms(output)


@pytest.mark.parametrize(
    "args",
    [
        ("download", "--help"),
        ("download", "track", "--help"),
        ("doctor", "--help"),
    ],
)
def test_required_command_help_exits_zero(
    runner: CliRunner,
    args: tuple[str, ...],
) -> None:
    output = invoke(runner, *args)

    assert_no_secret_terms(output)


@pytest.mark.parametrize("command_name", ["oauth", "resolver"])
def test_optional_command_group_help_exits_zero_if_present(
    runner: CliRunner,
    command_name: str,
) -> None:
    root_help = invoke(runner, "--help")
    if not command_exists(root_help, command_name):
        pytest.skip(f"{command_name} command group is not registered")

    output = invoke(runner, command_name, "--help")

    assert_no_secret_terms(output)
