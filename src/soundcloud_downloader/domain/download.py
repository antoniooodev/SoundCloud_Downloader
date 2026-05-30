from enum import Enum
from typing import Any, Self
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from soundcloud_downloader.domain.artifact import ArtifactKind, ArtifactMetadata
from soundcloud_downloader.domain.audio_export import AudioExportFormat, AudioExportMetadata
from soundcloud_downloader.domain.enums import AccessMode, OutputProfile
from soundcloud_downloader.domain.hls_assembly import HLSMediaAssemblyResult
from soundcloud_downloader.domain.hls_segments import HLSSegmentPlan
from soundcloud_downloader.domain.hls_staging import HLSSegmentStagingResult
from soundcloud_downloader.domain.metadata import SoundCloudTrackMetadata
from soundcloud_downloader.domain.transcoding import SoundCloudTranscodingMetadata

_SENSITIVE_URL_MARKERS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "cookie",
        "credentials",
        "refresh_token",
        "set-cookie",
    }
)


class TrackDownloadStatus(str, Enum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"


class TrackDownloadRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_url: str
    output_format: AudioExportFormat = AudioExportFormat.M4A
    access_mode: AccessMode
    output_profile: OutputProfile
    metadata: AudioExportMetadata | None = None

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        stripped = value.strip()
        parsed = urlsplit(stripped)
        if stripped == "" or parsed.scheme not in {"http", "https"} or parsed.netloc == "":
            raise ValueError("Track download source URL must be an absolute http or https URL.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Track download source URL must not contain credentials.")
        lowered = stripped.lower()
        if any(marker in lowered for marker in _SENSITIVE_URL_MARKERS):
            raise ValueError("Track download source URL must not contain sensitive fields.")
        query_keys = {
            key.strip().lower().replace("_", "-")
            for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
        }
        sensitive_keys = {marker.replace("_", "-") for marker in _SENSITIVE_URL_MARKERS}
        if query_keys & sensitive_keys:
            raise ValueError("Track download source URL must not contain sensitive query fields.")
        return stripped


class TrackDownloadResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: TrackDownloadStatus
    metadata: SoundCloudTrackMetadata
    selected_transcoding: SoundCloudTranscodingMetadata
    stream_analysis: Any
    segment_plan: HLSSegmentPlan
    staging_result: HLSSegmentStagingResult
    assembly_result: HLSMediaAssemblyResult
    final_artifact: ArtifactMetadata
    output_format: AudioExportFormat

    @model_validator(mode="after")
    def validate_successful_result(self) -> Self:
        if self.status is not TrackDownloadStatus.SUCCEEDED:
            raise ValueError("Track download result currently represents successful results only.")
        if self.final_artifact.kind is not ArtifactKind.FINAL_AUDIO:
            raise ValueError("Track download final artifact must use FINAL_AUDIO kind.")
        return self


def redact_track_download_result(result: TrackDownloadResult) -> dict[str, object]:
    return {
        "status": result.status.value,
        "track": {
            "id": result.metadata.id.value,
            "title": result.metadata.title,
            "user": None if result.metadata.user is None else result.metadata.user.username,
        },
        "output": {
            "format": result.output_format.value,
            "artifact_id": result.final_artifact.artifact_id.value,
            "relative_path": result.final_artifact.relative_path.value,
            "size_bytes": result.final_artifact.size_bytes,
            "checksum": (
                None
                if result.final_artifact.checksum is None
                else result.final_artifact.checksum.value
            ),
        },
        "segments": {
            "count": result.staging_result.segment_count,
            "total_bytes": result.staging_result.total_bytes,
        },
    }
