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
    HLSMediaAssemblyInput,
    HLSMediaAssemblyResult,
    HLSMediaAssemblyStatus,
    HLSSegmentStagingResult,
    SoundCloudResolvedStreamUrl,
    StagedHLSSegment,
    redact_hls_media_assembly_result,
)

MANIFEST_URL = "https://media.soundcloud.test/path/playlist.m3u8?Policy=dummy"
SEGMENT_URL = "https://media.soundcloud.test/path/segment0.ts?Policy=dummy"
SHA256 = "a" * 64


def test_hls_media_assembly_input_accepts_complete_staging_result() -> None:
    assembly_input = HLSMediaAssemblyInput(staging_result=_staging_result())

    assert assembly_input.staging_result.complete is True


def test_hls_media_assembly_input_rejects_incomplete_staging_result() -> None:
    with pytest.raises(ValidationError):
        HLSMediaAssemblyInput(staging_result=_staging_result(complete=False))


def test_hls_media_assembly_input_rejects_empty_staged_segments() -> None:
    staging_result = HLSSegmentStagingResult.model_construct(
        manifest_url=_manifest_url(),
        segments=(),
        total_bytes=0,
        complete=True,
    )

    with pytest.raises(ValidationError):
        HLSMediaAssemblyInput(staging_result=staging_result)


def test_hls_media_assembly_result_accepts_valid_staged_media_artifact() -> None:
    result = _assembly_result()

    assert result.status is HLSMediaAssemblyStatus.ASSEMBLED
    assert result.artifact.kind is ArtifactKind.STAGED_MEDIA


def test_hls_media_assembly_result_rejects_zero_source_segment_count() -> None:
    with pytest.raises(ValidationError):
        _assembly_result(source_segment_count=0)


def test_hls_media_assembly_result_rejects_non_positive_total_duration_seconds() -> None:
    with pytest.raises(ValidationError):
        _assembly_result(total_duration_seconds=0)


def test_hls_media_assembly_result_rejects_negative_total_bytes() -> None:
    with pytest.raises(ValidationError):
        _assembly_result(total_bytes=-1)


def test_assembly_input_repr_and_model_dump_do_not_expose_manifest_url() -> None:
    assembly_input = HLSMediaAssemblyInput(staging_result=_staging_result())
    output = f"{assembly_input!r} {assembly_input.model_dump(mode='json')}"

    assert MANIFEST_URL not in output


def test_assembly_result_repr_and_model_dump_do_not_expose_manifest_url() -> None:
    output = f"{_assembly_result()!r} {_assembly_result().model_dump(mode='json')}"

    assert MANIFEST_URL not in output


def test_assembly_result_repr_and_model_dump_do_not_expose_segment_url() -> None:
    output = f"{_assembly_result()!r} {_assembly_result().model_dump(mode='json')}"

    assert SEGMENT_URL not in output


def test_redact_hls_media_assembly_result_returns_safe_artifact_metadata() -> None:
    redacted = redact_hls_media_assembly_result(_assembly_result())

    assert redacted == {
        "artifact_id": "assembled-media",
        "relative_path": "hls/assembled/media.bin",
        "format": "bin",
        "source_segment_count": 2,
        "total_duration_seconds": 12.5,
        "total_bytes": 8,
        "checksum": SHA256,
        "status": "assembled",
    }
    assert MANIFEST_URL not in str(redacted)
    assert SEGMENT_URL not in str(redacted)


def test_domain_models_are_immutable() -> None:
    result = _assembly_result()

    with pytest.raises(ValidationError):
        result.total_bytes = 10


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert _assembly_result().source_segment_count == 2


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert _assembly_result().total_bytes == 8


def _manifest_url() -> SoundCloudResolvedStreamUrl:
    return SoundCloudResolvedStreamUrl(value=SecretStr(MANIFEST_URL))


def _artifact(
    *,
    artifact_id: str = "assembled-media",
    kind: ArtifactKind = ArtifactKind.STAGED_MEDIA,
    relative_path: str = "hls/assembled/media.bin",
    size_bytes: int = 8,
) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=ArtifactId(value=artifact_id),
        kind=kind,
        format=ArtifactFormat.BIN,
        relative_path=ArtifactRelativePath(value=relative_path),
        size_bytes=size_bytes,
        checksum=ArtifactChecksum(value=SHA256),
        created_at=datetime(2026, 5, 30, tzinfo=UTC),
    )


def _staged_segment(index: int = 0, duration_seconds: float = 6.0) -> StagedHLSSegment:
    return StagedHLSSegment(
        index=index,
        artifact=_artifact(
            artifact_id=f"segment-{index}",
            kind=ArtifactKind.HLS_SEGMENT,
            relative_path=f"hls/staged/segments/{index:06d}.bin",
            size_bytes=4,
        ),
        duration_seconds=duration_seconds,
    )


def _staging_result(*, complete: bool = True) -> HLSSegmentStagingResult:
    return HLSSegmentStagingResult(
        manifest_url=_manifest_url(),
        segments=(_staged_segment(0, 6.0), _staged_segment(1, 6.5)),
        total_bytes=8,
        complete=complete,
    )


def _assembly_result(
    *,
    source_segment_count: int = 2,
    total_duration_seconds: float = 12.5,
    total_bytes: int = 8,
) -> HLSMediaAssemblyResult:
    return HLSMediaAssemblyResult(
        artifact=_artifact(),
        source_segment_count=source_segment_count,
        total_duration_seconds=total_duration_seconds,
        total_bytes=total_bytes,
    )
