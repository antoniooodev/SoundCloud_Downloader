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
    AudioArtworkArtifact,
    AudioExportFormat,
    AudioExportMetadata,
    AudioExportRequest,
    AudioExportResult,
    AudioExportStatus,
    redact_audio_export_result,
)

SHA256 = "a" * 64


def test_audio_export_metadata_accepts_title_artist_album() -> None:
    metadata = AudioExportMetadata(title="Track", artist="Artist", album="Album")

    assert metadata.title == "Track"
    assert metadata.artist == "Artist"
    assert metadata.album == "Album"


def test_audio_export_metadata_rejects_empty_title() -> None:
    with pytest.raises(ValidationError):
        AudioExportMetadata(title=" ")


def test_audio_export_metadata_rejects_newline_control_characters() -> None:
    with pytest.raises(ValidationError):
        AudioExportMetadata(title="bad\nvalue")


def test_audio_export_metadata_rejects_access_token_like_value() -> None:
    with pytest.raises(ValidationError):
        AudioExportMetadata(title="access_token=secret")


def test_audio_artwork_artifact_accepts_artwork_artifact_metadata() -> None:
    artwork = AudioArtworkArtifact(
        artifact=_artifact(kind=ArtifactKind.ARTWORK, format=ArtifactFormat.JPG)
    )

    assert artwork.artifact.kind is ArtifactKind.ARTWORK


def test_audio_export_request_accepts_mp3_output_path() -> None:
    request = _request(AudioExportFormat.MP3, "audio/final.mp3")

    assert request.output_format is AudioExportFormat.MP3


def test_audio_export_request_accepts_wav_output_path() -> None:
    request = _request(AudioExportFormat.WAV, "audio/final.wav")

    assert request.output_format is AudioExportFormat.WAV


def test_audio_export_request_accepts_m4a_output_path() -> None:
    request = _request(AudioExportFormat.M4A, "audio/final.m4a")

    assert request.output_format is AudioExportFormat.M4A


def test_audio_export_request_rejects_mismatched_output_extension() -> None:
    with pytest.raises(ValidationError):
        _request(AudioExportFormat.MP3, "audio/final.wav")


def test_audio_export_result_accepts_valid_output_artifact() -> None:
    result = _result()

    assert result.output_artifact.kind is ArtifactKind.FINAL_AUDIO
    assert result.status is AudioExportStatus.SUCCEEDED


def test_redact_audio_export_result_returns_safe_metadata() -> None:
    redacted = redact_audio_export_result(_result())

    assert redacted == {
        "status": "succeeded",
        "output_format": "mp3",
        "input": {
            "artifact_id": "input-media",
            "relative_path": "audio/input.m4a",
        },
        "output": {
            "artifact_id": "output-audio",
            "relative_path": "audio/final.mp3",
            "format": "mp3",
            "size_bytes": 8,
            "checksum": SHA256,
        },
        "metadata_embedded": True,
        "artwork_embedded": False,
    }


@pytest.mark.parametrize(
    "marker",
    ["access_token", "refresh_token", "stream_url", "manifest_url"],
)
def test_repr_and_model_dump_do_not_contain_sensitive_markers(marker: str) -> None:
    output = f"{_result()!r} {_result().model_dump(mode='json')}"

    assert marker not in output


def test_domain_models_are_immutable() -> None:
    result = _result()

    with pytest.raises(ValidationError):
        result.status = AudioExportStatus.FAILED


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert _result().status is AudioExportStatus.SUCCEEDED


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert _result().output_artifact.size_bytes == 8


def _request(output_format: AudioExportFormat, output_path: str) -> AudioExportRequest:
    return AudioExportRequest(
        input_artifact=_artifact(
            artifact_id="input-media",
            kind=ArtifactKind.FINAL_AUDIO,
            format=ArtifactFormat.M4A,
            relative_path="audio/input.m4a",
        ),
        output_format=output_format,
        output_path=ArtifactRelativePath(value=output_path),
    )


def _result() -> AudioExportResult:
    return AudioExportResult(
        input_artifact=_artifact(
            artifact_id="input-media",
            kind=ArtifactKind.FINAL_AUDIO,
            format=ArtifactFormat.M4A,
            relative_path="audio/input.m4a",
        ),
        output_artifact=_artifact(
            artifact_id="output-audio",
            kind=ArtifactKind.FINAL_AUDIO,
            format=ArtifactFormat.MP3,
            relative_path="audio/final.mp3",
        ),
        output_format=AudioExportFormat.MP3,
        metadata_embedded=True,
    )


def _artifact(
    *,
    artifact_id: str = "artifact",
    kind: ArtifactKind,
    format: ArtifactFormat,
    relative_path: str = "audio/final.mp3",
) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=ArtifactId(value=artifact_id),
        kind=kind,
        format=format,
        relative_path=ArtifactRelativePath(value=relative_path),
        size_bytes=8,
        checksum=ArtifactChecksum(value=SHA256),
        created_at=datetime(2026, 5, 30, tzinfo=UTC),
    )
