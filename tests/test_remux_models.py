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
    RemuxInputArtifact,
    RemuxOutputArtifact,
    RemuxResult,
    RemuxStatus,
    redact_remux_result,
)

SHA256 = "a" * 64


def test_remux_input_artifact_accepts_staged_media_artifact() -> None:
    remux_input = RemuxInputArtifact(artifact=_artifact(kind=ArtifactKind.STAGED_MEDIA))

    assert remux_input.artifact.kind is ArtifactKind.STAGED_MEDIA


def test_remux_output_artifact_accepts_final_audio_m4a_artifact() -> None:
    remux_output = RemuxOutputArtifact(artifact=_artifact(kind=ArtifactKind.FINAL_AUDIO))

    assert remux_output.artifact.format is ArtifactFormat.M4A


def test_remux_result_accepts_valid_input_output_artifacts() -> None:
    result = _result()

    assert result.input_artifact.artifact.kind is ArtifactKind.STAGED_MEDIA
    assert result.output_artifact.artifact.kind is ArtifactKind.FINAL_AUDIO


def test_remux_result_default_status_is_succeeded() -> None:
    assert _result().status is RemuxStatus.SUCCEEDED


def test_redact_remux_result_returns_safe_metadata() -> None:
    redacted = redact_remux_result(_result())

    assert redacted == {
        "status": "succeeded",
        "input": {
            "artifact_id": "input-media",
            "relative_path": "hls/assembled/media.bin",
        },
        "output": {
            "artifact_id": "output-audio",
            "relative_path": "audio/final.m4a",
            "format": "m4a",
            "size_bytes": 8,
            "checksum": SHA256,
        },
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
        result.status = RemuxStatus.FAILED


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert _result().status is RemuxStatus.SUCCEEDED


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert _result().output_artifact.artifact.size_bytes == 8


def _artifact(
    *,
    artifact_id: str = "output-audio",
    kind: ArtifactKind,
    relative_path: str = "audio/final.m4a",
) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=ArtifactId(value=artifact_id),
        kind=kind,
        format=ArtifactFormat.M4A,
        relative_path=ArtifactRelativePath(value=relative_path),
        size_bytes=8,
        checksum=ArtifactChecksum(value=SHA256),
        created_at=datetime(2026, 5, 30, tzinfo=UTC),
    )


def _result() -> RemuxResult:
    return RemuxResult(
        input_artifact=RemuxInputArtifact(
            artifact=_artifact(
                artifact_id="input-media",
                kind=ArtifactKind.STAGED_MEDIA,
                relative_path="hls/assembled/media.bin",
            )
        ),
        output_artifact=RemuxOutputArtifact(
            artifact=_artifact(kind=ArtifactKind.FINAL_AUDIO)
        ),
    )
