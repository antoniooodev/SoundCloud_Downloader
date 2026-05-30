import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.domain import (
    ArtifactChecksum,
    ArtifactFormat,
    ArtifactId,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
    HLSByteRange,
    HLSSegmentFetchStatus,
    HLSSegmentStagingResult,
    SoundCloudResolvedStreamUrl,
    StagedHLSSegment,
    redact_hls_staging_result,
)

MANIFEST_URL = "https://media.soundcloud.test/path/playlist.m3u8?Policy=dummy"
SEGMENT_URL = "https://media.soundcloud.test/path/segment0.ts?Policy=dummy"
SHA256 = "a" * 64


def test_staged_hls_segment_accepts_valid_artifact_metadata() -> None:
    segment = StagedHLSSegment(index=0, artifact=_artifact(), duration_seconds=6.0)

    assert segment.status is HLSSegmentFetchStatus.STAGED
    assert segment.artifact.kind is ArtifactKind.HLS_SEGMENT


def test_staged_hls_segment_rejects_negative_index() -> None:
    with pytest.raises(ValidationError):
        StagedHLSSegment(index=-1, artifact=_artifact(), duration_seconds=6.0)


def test_staged_hls_segment_rejects_non_positive_duration() -> None:
    with pytest.raises(ValidationError):
        StagedHLSSegment(index=0, artifact=_artifact(), duration_seconds=0)


def test_hls_segment_staging_result_rejects_empty_segments() -> None:
    with pytest.raises(ValidationError):
        HLSSegmentStagingResult(manifest_url=_manifest_url(), segments=(), total_bytes=0)


def test_hls_segment_staging_result_rejects_negative_total_bytes() -> None:
    with pytest.raises(ValidationError):
        HLSSegmentStagingResult(
            manifest_url=_manifest_url(),
            segments=(_segment(),),
            total_bytes=-1,
        )


def test_hls_segment_staging_result_exposes_segment_count() -> None:
    assert _result().segment_count == 2


def test_hls_segment_staging_result_exposes_total_duration_seconds() -> None:
    assert _result().total_duration_seconds == 12.5


def test_staging_result_repr_does_not_expose_manifest_url() -> None:
    assert MANIFEST_URL not in repr(_result())


def test_staging_result_model_dump_does_not_expose_manifest_url() -> None:
    assert MANIFEST_URL not in str(_result().model_dump(mode="json"))


def test_staging_result_repr_and_model_dump_do_not_expose_segment_url() -> None:
    output = f"{_result()!r} {_result().model_dump(mode='json')}"

    assert SEGMENT_URL not in output


def test_redact_hls_staging_result_redacts_manifest_url() -> None:
    redacted = redact_hls_staging_result(_result())

    assert redacted["manifest_url"] == "[REDACTED]"
    assert MANIFEST_URL not in str(redacted)


def test_redact_hls_staging_result_contains_safe_artifact_metadata() -> None:
    redacted = redact_hls_staging_result(_result())
    first = redacted["segments"][0]

    assert first["artifact_id"] == "segment-0"
    assert first["relative_path"] == "hls/staged/segments/000000.bin"
    assert first["size_bytes"] == 4
    assert first["checksum"] == SHA256
    assert SEGMENT_URL not in str(redacted)


def test_domain_models_are_immutable() -> None:
    result = _result()

    with pytest.raises(ValidationError):
        result.total_bytes = 1


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert _result().segment_count == 2


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert _result().complete is True


def _manifest_url() -> SoundCloudResolvedStreamUrl:
    return SoundCloudResolvedStreamUrl(value=SecretStr(MANIFEST_URL))


def _artifact(index: int = 0, size_bytes: int = 4) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=ArtifactId(value=f"segment-{index}"),
        kind=ArtifactKind.HLS_SEGMENT,
        format=ArtifactFormat.BIN,
        relative_path=ArtifactRelativePath(value=f"hls/staged/segments/{index:06d}.bin"),
        size_bytes=size_bytes,
        checksum=ArtifactChecksum(value=SHA256),
        created_at=datetime(2026, 5, 30, tzinfo=UTC),
    )


def _segment(index: int = 0, duration_seconds: float = 6.0) -> StagedHLSSegment:
    return StagedHLSSegment(
        index=index,
        artifact=_artifact(index=index),
        duration_seconds=duration_seconds,
        source_byte_range=HLSByteRange(length=4, offset=0),
    )


def _result() -> HLSSegmentStagingResult:
    return HLSSegmentStagingResult(
        manifest_url=_manifest_url(),
        segments=(_segment(0, 6.0), _segment(1, 6.5)),
        total_bytes=8,
    )
