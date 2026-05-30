import re
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from soundcloud_downloader.domain.artifact import (
    ArtifactFormat,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
)

_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_SENSITIVE_METADATA_MARKERS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "cookie",
        "refresh_token",
        "set-cookie",
    }
)
_EXTENSION_BY_FORMAT = {
    "m4a": ".m4a",
    "mp3": ".mp3",
    "wav": ".wav",
}
_ARTIFACT_FORMAT_BY_EXPORT_FORMAT = {
    "m4a": ArtifactFormat.M4A,
    "mp3": ArtifactFormat.MP3,
    "wav": ArtifactFormat.WAV,
}


class AudioExportFormat(str, Enum):
    M4A = "m4a"
    MP3 = "mp3"
    WAV = "wav"


class AudioExportStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AudioExportMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str | None = None
    artist: str | None = None
    album: str | None = None

    @field_validator("title", "artist", "album")
    @classmethod
    def validate_metadata_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if stripped == "":
            raise ValueError("Audio export metadata fields must not be empty.")
        if _CONTROL_CHARACTER_PATTERN.search(stripped):
            raise ValueError("Audio export metadata fields must not contain control characters.")
        lowered = stripped.lower()
        if any(marker in lowered for marker in _SENSITIVE_METADATA_MARKERS):
            raise ValueError(
                "Audio export metadata fields must not contain sensitive marker names."
            )
        return stripped


class AudioArtworkArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact: ArtifactMetadata

    @model_validator(mode="after")
    def validate_artwork_artifact(self) -> Self:
        if self.artifact.kind is not ArtifactKind.ARTWORK:
            raise ValueError("Audio artwork artifact must use ARTWORK kind.")
        if self.artifact.format not in {ArtifactFormat.JPG, ArtifactFormat.PNG}:
            raise ValueError("Audio artwork artifact must use JPG or PNG format.")
        return self


class AudioExportRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_artifact: ArtifactMetadata
    output_format: AudioExportFormat
    output_path: ArtifactRelativePath
    metadata: AudioExportMetadata | None = None
    artwork: AudioArtworkArtifact | None = None

    @model_validator(mode="after")
    def validate_output_path_extension(self) -> Self:
        expected_extension = _EXTENSION_BY_FORMAT[self.output_format.value]
        if not self.output_path.value.lower().endswith(expected_extension):
            raise ValueError("Audio export output path extension must match output format.")
        return self


class AudioExportResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_artifact: ArtifactMetadata
    output_artifact: ArtifactMetadata
    output_format: AudioExportFormat
    metadata_embedded: bool = False
    artwork_embedded: bool = False
    status: AudioExportStatus = AudioExportStatus.SUCCEEDED

    @model_validator(mode="after")
    def validate_output_artifact(self) -> Self:
        if self.output_artifact.kind is not ArtifactKind.FINAL_AUDIO:
            raise ValueError("Audio export output artifact must use FINAL_AUDIO kind.")
        expected_format = _ARTIFACT_FORMAT_BY_EXPORT_FORMAT[self.output_format.value]
        if self.output_artifact.format is not expected_format:
            raise ValueError("Audio export output artifact format must match output format.")
        return self


def redact_audio_export_result(result: AudioExportResult) -> dict[str, object]:
    return {
        "status": result.status.value,
        "output_format": result.output_format.value,
        "input": {
            "artifact_id": result.input_artifact.artifact_id.value,
            "relative_path": result.input_artifact.relative_path.value,
        },
        "output": {
            "artifact_id": result.output_artifact.artifact_id.value,
            "relative_path": result.output_artifact.relative_path.value,
            "format": result.output_artifact.format.value,
            "size_bytes": result.output_artifact.size_bytes,
            "checksum": (
                None
                if result.output_artifact.checksum is None
                else result.output_artifact.checksum.value
            ),
        },
        "metadata_embedded": result.metadata_embedded,
        "artwork_embedded": result.artwork_embedded,
    }
