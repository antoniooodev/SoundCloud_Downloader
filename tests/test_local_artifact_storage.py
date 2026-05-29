import socket
from pathlib import Path

import pytest

from soundcloud_downloader.application import ArtifactStoragePort
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ArtifactFormat,
    ArtifactRelativePath,
    ChecksumAlgorithm,
    ErrorCode,
)
from soundcloud_downloader.infrastructure.storage import (
    LocalArtifactStorage,
    LocalArtifactStorageError,
    compute_sha256_bytes,
)
from soundcloud_downloader.infrastructure.storage import local_storage as local_storage_module


def test_storage_init_does_not_create_directories(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"

    LocalArtifactStorage(_settings(root, allow_filesystem_writes=True))

    assert root.exists() is False


def test_write_bytes_requires_allow_filesystem_writes_true(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(_settings(tmp_path / "artifacts", allow_filesystem_writes=False))

    with pytest.raises(LocalArtifactStorageError) as exc_info:
        storage.write_bytes(relative_path=_path(), data=b"data")

    assert exc_info.value.code is ErrorCode.STORAGE_FAILED


def test_write_bytes_creates_parent_directories_only_when_writing(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    storage = LocalArtifactStorage(_settings(root, allow_filesystem_writes=True))

    assert root.exists() is False

    storage.write_bytes(relative_path=_path("tracks/one/audio.m4a"), data=b"data")

    assert (root / "tracks" / "one").is_dir()


def test_write_bytes_writes_data_under_artifact_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    storage = LocalArtifactStorage(_settings(root, allow_filesystem_writes=True))

    storage.write_bytes(relative_path=_path("tracks/one/audio.m4a"), data=b"data")

    stored_path = root / "tracks" / "one" / "audio.m4a"
    assert stored_path.read_bytes() == b"data"
    assert stored_path.resolve().is_relative_to(root.resolve())


def test_write_bytes_returns_metadata_with_size_bytes(tmp_path: Path) -> None:
    metadata = LocalArtifactStorage(_settings(tmp_path / "artifacts")).write_bytes(
        relative_path=_path(),
        data=b"data",
    )

    assert metadata.size_bytes == 4


def test_write_bytes_returns_sha256_checksum(tmp_path: Path) -> None:
    metadata = LocalArtifactStorage(_settings(tmp_path / "artifacts")).write_bytes(
        relative_path=_path(),
        data=b"data",
    )

    assert metadata.checksum == compute_sha256_bytes(b"data")
    assert metadata.checksum is not None
    assert metadata.checksum.algorithm is ChecksumAlgorithm.SHA256


def test_write_bytes_infers_simple_format(tmp_path: Path) -> None:
    metadata = LocalArtifactStorage(_settings(tmp_path / "artifacts")).write_bytes(
        relative_path=_path("tracks/one/audio.mp3"),
        data=b"data",
    )

    assert metadata.format is ArtifactFormat.MP3


def test_read_bytes_returns_stored_data(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(_settings(tmp_path / "artifacts"))
    storage.write_bytes(relative_path=_path(), data=b"data")

    assert storage.read_bytes(relative_path=_path()) == b"data"


def test_exists_returns_true_for_stored_data(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(_settings(tmp_path / "artifacts"))
    storage.write_bytes(relative_path=_path(), data=b"data")

    assert storage.exists(relative_path=_path()) is True


def test_exists_returns_false_for_missing_path(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(_settings(tmp_path / "artifacts"))

    assert storage.exists(relative_path=_path()) is False


def test_delete_removes_stored_data(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(_settings(tmp_path / "artifacts"))
    storage.write_bytes(relative_path=_path(), data=b"data")

    storage.delete(relative_path=_path())

    assert storage.exists(relative_path=_path()) is False


def test_delete_missing_path_is_idempotent(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(_settings(tmp_path / "artifacts"))

    storage.delete(relative_path=_path())

    assert storage.exists(relative_path=_path()) is False


def test_delete_requires_allow_filesystem_writes_true(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(_settings(tmp_path / "artifacts", allow_filesystem_writes=False))

    with pytest.raises(LocalArtifactStorageError):
        storage.delete(relative_path=_path())


def test_read_bytes_does_not_require_allow_filesystem_writes_true(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    LocalArtifactStorage(_settings(root)).write_bytes(relative_path=_path(), data=b"data")
    read_only_storage = LocalArtifactStorage(_settings(root, allow_filesystem_writes=False))

    assert read_only_storage.read_bytes(relative_path=_path()) == b"data"


def test_exists_does_not_require_allow_filesystem_writes_true(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    LocalArtifactStorage(_settings(root)).write_bytes(relative_path=_path(), data=b"data")
    read_only_storage = LocalArtifactStorage(_settings(root, allow_filesystem_writes=False))

    assert read_only_storage.exists(relative_path=_path()) is True


def test_path_traversal_is_rejected_before_filesystem_access(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    storage = LocalArtifactStorage(_settings(root))
    unsafe_path = ArtifactRelativePath.model_construct(value="../escape.bin")

    with pytest.raises(LocalArtifactStorageError) as exc_info:
        storage.exists(relative_path=unsafe_path)

    assert exc_info.value.code is ErrorCode.UNKNOWN_UNSAFE
    assert root.exists() is False


def test_absolute_paths_are_rejected_before_filesystem_access(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    storage = LocalArtifactStorage(_settings(root))
    unsafe_path = ArtifactRelativePath.model_construct(value="/escape.bin")

    with pytest.raises(LocalArtifactStorageError):
        storage.exists(relative_path=unsafe_path)

    assert root.exists() is False


def test_atomic_rewrite_replaces_previous_content(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(_settings(tmp_path / "artifacts"))
    storage.write_bytes(relative_path=_path(), data=b"old")

    storage.write_bytes(relative_path=_path(), data=b"new")

    assert storage.read_bytes(relative_path=_path()) == b"new"


def test_failed_write_does_not_leave_plaintext_temp_data_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "artifacts"
    storage = LocalArtifactStorage(_settings(root))

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(local_storage_module.os, "replace", fail_replace)

    with pytest.raises(LocalArtifactStorageError):
        storage.write_bytes(relative_path=_path(), data=b"plaintext")

    assert not (tmp_path / "plaintext").exists()
    for file_path in tmp_path.rglob("*"):
        if file_path.is_file() and file_path.is_relative_to(root):
            assert b"plaintext" not in file_path.read_bytes()


def test_storage_satisfies_artifact_storage_port(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(_settings(tmp_path / "artifacts"))

    assert isinstance(storage, ArtifactStoragePort)


def test_tests_perform_no_network_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    storage = LocalArtifactStorage(_settings(tmp_path / "artifacts"))
    storage.write_bytes(relative_path=_path(), data=b"data")
    assert storage.read_bytes(relative_path=_path()) == b"data"


def test_tests_write_only_inside_pytest_tmp_path(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    storage = LocalArtifactStorage(_settings(root))

    storage.write_bytes(relative_path=_path("tracks/one/audio.bin"), data=b"data")

    for file_path in root.rglob("*"):
        assert file_path.resolve().is_relative_to(tmp_path.resolve())


def _settings(root: Path, allow_filesystem_writes: bool = True) -> AppSettings:
    return AppSettings(
        allow_filesystem_writes=allow_filesystem_writes,
        artifact_storage_root=root,
    )


def _path(value: str = "tracks/one/audio.bin") -> ArtifactRelativePath:
    return ArtifactRelativePath(value=value)
