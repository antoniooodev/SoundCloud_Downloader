from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soundcloud_downloader.domain.artifact import ArtifactKind, ArtifactMetadata
from soundcloud_downloader.domain.hls_segments import HLSByteRange
from soundcloud_downloader.domain.stream_url import SoundCloudResolvedStreamUrl

_REDACTED_VALUE = "[REDACTED]"


class HLSSegmentFetchStatus(str, Enum):
    STAGED = "staged"
    FAILED = "failed"


class StagedHLSSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    artifact: ArtifactMetadata
    duration_seconds: float = Field(gt=0)
    source_byte_range: HLSByteRange | None = None
    status: HLSSegmentFetchStatus = HLSSegmentFetchStatus.STAGED

    @model_validator(mode="after")
    def validate_artifact_kind(self) -> "StagedHLSSegment":
        if self.artifact.kind is not ArtifactKind.HLS_SEGMENT:
            raise ValueError("Staged HLS segment artifacts must use HLS_SEGMENT kind.")
        return self


class HLSSegmentStagingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest_url: SoundCloudResolvedStreamUrl
    segments: tuple[StagedHLSSegment, ...] = Field(min_length=1)
    total_bytes: int = Field(ge=0)
    complete: bool = True

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def total_duration_seconds(self) -> float:
        return sum(segment.duration_seconds for segment in self.segments)


def redact_hls_staging_result(
    result: HLSSegmentStagingResult,
) -> dict[str, object]:
    return {
        "segment_count": result.segment_count,
        "total_bytes": result.total_bytes,
        "total_duration_seconds": result.total_duration_seconds,
        "complete": result.complete,
        "manifest_url": _REDACTED_VALUE,
        "segments": [
            {
                "index": segment.index,
                "artifact_id": segment.artifact.artifact_id.value,
                "relative_path": segment.artifact.relative_path.value,
                "duration_seconds": segment.duration_seconds,
                "size_bytes": segment.artifact.size_bytes,
                "checksum": (
                    None if segment.artifact.checksum is None else segment.artifact.checksum.value
                ),
            }
            for segment in result.segments
        ],
    }
