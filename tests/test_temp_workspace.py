import socket
from pathlib import Path

import pytest

from soundcloud_downloader.application import TemporaryWorkspacePort
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.infrastructure.storage import (
    LocalTemporaryWorkspace,
    TemporaryWorkspaceError,
)


def test_workspace_init_does_not_create_directories(tmp_path: Path) -> None:
    root = tmp_path / "tmp"

    LocalTemporaryWorkspace(_settings(root))

    assert root.exists() is False


def test_create_workspace_requires_allow_filesystem_writes_true(tmp_path: Path) -> None:
    workspace = LocalTemporaryWorkspace(_settings(tmp_path / "tmp", allow_filesystem_writes=False))

    with pytest.raises(TemporaryWorkspaceError):
        workspace.create_workspace()


def test_create_workspace_creates_temp_root_only_when_called(tmp_path: Path) -> None:
    root = tmp_path / "tmp"
    workspace = LocalTemporaryWorkspace(_settings(root))

    assert root.exists() is False

    workspace_path = workspace.create_workspace()

    assert root.is_dir()
    assert workspace_path.is_dir()


def test_create_workspace_returns_unique_directory(tmp_path: Path) -> None:
    workspace = LocalTemporaryWorkspace(_settings(tmp_path / "tmp"))

    first = workspace.create_workspace()
    second = workspace.create_workspace()

    assert first != second
    assert first.is_dir()
    assert second.is_dir()


def test_prefix_accepts_safe_value(tmp_path: Path) -> None:
    workspace = LocalTemporaryWorkspace(_settings(tmp_path / "tmp"))

    path = workspace.create_workspace(prefix="track_01-work")

    assert path.name.startswith("track_01-work-")


def test_prefix_rejects_slash(tmp_path: Path) -> None:
    with pytest.raises(TemporaryWorkspaceError):
        LocalTemporaryWorkspace(_settings(tmp_path / "tmp")).create_workspace(prefix="bad/prefix")


def test_prefix_rejects_backslash(tmp_path: Path) -> None:
    with pytest.raises(TemporaryWorkspaceError):
        LocalTemporaryWorkspace(_settings(tmp_path / "tmp")).create_workspace(prefix=r"bad\prefix")


def test_prefix_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(TemporaryWorkspaceError):
        LocalTemporaryWorkspace(_settings(tmp_path / "tmp")).create_workspace(prefix="..")


def test_cleanup_removes_workspace_directory(tmp_path: Path) -> None:
    workspace = LocalTemporaryWorkspace(_settings(tmp_path / "tmp"))
    path = workspace.create_workspace()

    workspace.cleanup_workspace(path)

    assert path.exists() is False


def test_cleanup_missing_workspace_under_root_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "tmp"
    workspace = LocalTemporaryWorkspace(_settings(root))
    missing = root / "missing-workspace"

    workspace.cleanup_workspace(missing)

    assert missing.exists() is False


def test_cleanup_requires_allow_filesystem_writes_true(tmp_path: Path) -> None:
    workspace = LocalTemporaryWorkspace(_settings(tmp_path / "tmp", allow_filesystem_writes=False))

    with pytest.raises(TemporaryWorkspaceError):
        workspace.cleanup_workspace(tmp_path / "tmp" / "work")


def test_cleanup_rejects_path_outside_temp_root(tmp_path: Path) -> None:
    workspace = LocalTemporaryWorkspace(_settings(tmp_path / "tmp"))
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(TemporaryWorkspaceError):
        workspace.cleanup_workspace(outside)

    assert outside.exists() is True


def test_workspace_satisfies_temporary_workspace_port(tmp_path: Path) -> None:
    workspace = LocalTemporaryWorkspace(_settings(tmp_path / "tmp"))

    assert isinstance(workspace, TemporaryWorkspacePort)


def test_tests_perform_no_network_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    path = LocalTemporaryWorkspace(_settings(tmp_path / "tmp")).create_workspace()
    assert path.is_dir()


def test_tests_write_only_inside_pytest_tmp_path(tmp_path: Path) -> None:
    root = tmp_path / "tmp"
    workspace = LocalTemporaryWorkspace(_settings(root))

    path = workspace.create_workspace()

    assert path.resolve().is_relative_to(tmp_path.resolve())


def _settings(root: Path, allow_filesystem_writes: bool = True) -> AppSettings:
    return AppSettings(
        allow_filesystem_writes=allow_filesystem_writes,
        artifact_temp_root=root,
    )
