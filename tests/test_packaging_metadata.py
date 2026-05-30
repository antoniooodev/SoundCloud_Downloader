import builtins
import importlib
import socket
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import typer
from importlib.metadata import PackageNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


@pytest.fixture(autouse=True)
def forbid_network_and_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("packaging metadata tests must not perform network calls")

    def fail_subprocess(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("packaging metadata tests must not execute subprocesses")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    monkeypatch.setattr(subprocess, "run", fail_subprocess)
    monkeypatch.setattr(subprocess, "Popen", fail_subprocess)


@pytest.fixture(autouse=True)
def forbid_file_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    original_open = builtins.open
    original_path_open = Path.open
    original_write_text = Path.write_text
    original_write_bytes = Path.write_bytes

    def guarded_open(
        file: str | bytes | Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> Any:
        if _is_write_mode(mode):
            raise AssertionError("packaging metadata tests must not write files")
        return original_open(file, mode, *args, **kwargs)

    def guarded_path_open(
        self: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> Any:
        if _is_write_mode(mode):
            raise AssertionError("packaging metadata tests must not write files")
        return original_path_open(self, mode, *args, **kwargs)

    def fail_write(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("packaging metadata tests must not write files")

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(Path, "write_text", fail_write)
    monkeypatch.setattr(Path, "write_bytes", fail_write)
    yield
    monkeypatch.setattr(builtins, "open", original_open)
    monkeypatch.setattr(Path, "open", original_path_open)
    monkeypatch.setattr(Path, "write_text", original_write_text)
    monkeypatch.setattr(Path, "write_bytes", original_write_bytes)


@pytest.fixture()
def pyproject_data() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))


def test_pyproject_exists() -> None:
    assert PYPROJECT_PATH.is_file()


def test_project_metadata_is_present(pyproject_data: dict[str, Any]) -> None:
    project = pyproject_data["project"]

    assert project["name"] == "soundcloud-downloader"
    assert project["version"]
    assert project["description"]
    assert project["requires-python"]
    assert project["readme"] == "README.md"
    assert (PROJECT_ROOT / project["readme"]).is_file()


def test_cli_script_entrypoint_exists_and_is_importable(
    pyproject_data: dict[str, Any],
) -> None:
    scripts = pyproject_data["project"]["scripts"]
    entrypoint = scripts["soundcloud-downloader"]

    imported = _import_entrypoint(entrypoint)

    assert isinstance(imported, typer.Typer)


def test_package_imports_successfully() -> None:
    package = importlib.import_module("soundcloud_downloader")

    assert package.__name__ == "soundcloud_downloader"


def test_cli_app_imports_successfully() -> None:
    cli_main = importlib.import_module("soundcloud_downloader.cli.main")

    assert isinstance(cli_main.app, typer.Typer)


def test_cli_version_falls_back_when_package_metadata_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_main = importlib.import_module("soundcloud_downloader.cli.main")

    def missing_version(_package_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(cli_main, "version", missing_version)

    assert cli_main.get_package_version() == cli_main.UNKNOWN_VERSION
    assert cli_main.format_version() == f"{cli_main.PACKAGE_NAME} {cli_main.UNKNOWN_VERSION}"


def _import_entrypoint(entrypoint: str) -> object:
    module_name, separator, attribute_name = entrypoint.partition(":")
    assert module_name
    assert separator == ":"
    assert attribute_name

    module = importlib.import_module(module_name)
    imported: object = module
    for attribute_part in attribute_name.split("."):
        imported = getattr(imported, attribute_part)
    return imported


def _is_write_mode(mode: str) -> bool:
    return any(flag in mode for flag in ("w", "a", "x", "+"))
