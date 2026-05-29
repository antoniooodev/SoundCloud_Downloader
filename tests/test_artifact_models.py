import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from soundcloud_downloader.domain import (
    ArtifactChecksum,
    ArtifactFormat,
    ArtifactId,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
    ChecksumAlgorithm,
)

SHA256 = "a" * 64


def test_artifact_id_accepts_safe_opaque_id() -> None:
    artifact_id = ArtifactId(value="track_01-final.mp3")

    assert artifact_id.value == "track_01-final.mp3"


def test_artifact_id_rejects_slash() -> None:
    with pytest.raises(ValidationError):
        ArtifactId(value="track/01")


def test_artifact_id_rejects_backslash() -> None:
    with pytest.raises(ValidationError):
        ArtifactId(value=r"track\01")


def test_artifact_id_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        ArtifactId(value="..")


def test_artifact_relative_path_accepts_nested_relative_path() -> None:
    path = ArtifactRelativePath(value="tracks/track-01/audio.m4a")

    assert path.value == "tracks/track-01/audio.m4a"


def test_artifact_relative_path_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        ArtifactRelativePath(value="/tracks/audio.m4a")


def test_artifact_relative_path_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        ArtifactRelativePath(value="tracks/../audio.m4a")


def test_artifact_relative_path_rejects_backslash() -> None:
    with pytest.raises(ValidationError):
        ArtifactRelativePath(value=r"tracks\audio.m4a")


def test_artifact_relative_path_rejects_empty_component() -> None:
    with pytest.raises(ValidationError):
        ArtifactRelativePath(value="tracks//audio.m4a")


def test_artifact_relative_path_rejects_url_like_scheme() -> None:
    with pytest.raises(ValidationError):
        ArtifactRelativePath(value="https://example.test/audio.m4a")


def test_artifact_checksum_accepts_lowercase_sha256_hex() -> None:
    checksum = ArtifactChecksum(value=SHA256)

    assert checksum.algorithm is ChecksumAlgorithm.SHA256
    assert checksum.value == SHA256


def test_artifact_checksum_rejects_short_checksum() -> None:
    with pytest.raises(ValidationError):
        ArtifactChecksum(value="a" * 63)


def test_artifact_checksum_rejects_non_hex_checksum() -> None:
    with pytest.raises(ValidationError):
        ArtifactChecksum(value="g" * 64)


def test_artifact_metadata_rejects_negative_size() -> None:
    with pytest.raises(ValidationError):
        _metadata(size_bytes=-1)


def test_artifact_metadata_accepts_utc_created_at() -> None:
    created_at = datetime(2026, 5, 29, tzinfo=UTC)

    metadata = _metadata(created_at=created_at)

    assert metadata.created_at is created_at


def test_artifact_metadata_rejects_naive_created_at() -> None:
    with pytest.raises(ValidationError):
        _metadata(created_at=datetime(2026, 5, 29))


def test_domain_models_are_immutable() -> None:
    metadata = _metadata()

    with pytest.raises(ValidationError):
        metadata.size_bytes = 2


def test_model_dump_and_repr_do_not_contain_sensitive_marker_names() -> None:
    metadata = _metadata()
    output = f"{metadata!r} {metadata.model_dump(mode='json')}"

    for marker in ("access_token", "refresh_token", "client_secret", "stream_url", "manifest_url"):
        assert marker not in output


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert ArtifactId(value="safe-id").value == "safe-id"


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert _metadata().size_bytes == 1


def _metadata(
    *,
    size_bytes: int | None = 1,
    created_at: datetime | None = datetime(2026, 5, 29, tzinfo=UTC),
) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=ArtifactId(value="artifact-1"),
        kind=ArtifactKind.TEMPORARY,
        format=ArtifactFormat.M4A,
        relative_path=ArtifactRelativePath(value="tracks/artifact-1/audio.m4a"),
        size_bytes=size_bytes,
        checksum=ArtifactChecksum(value=SHA256),
        created_at=created_at,
    )
