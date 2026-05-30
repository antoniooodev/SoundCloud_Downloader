from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soundcloud_downloader.domain.artifact import ArtifactKind, ArtifactMetadata
from soundcloud_downloader.domain.hls_staging import HLSSegmentStagingResult


class HLSMediaAssemblyStatus(str, Enum):
    ASSEMBLED = "assembled"
    FAILED = "failed"


class HLSMediaAssemblyInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    staging_result: HLSSegmentStagingResult

    @model_validator(mode="after")
    def validate_staging_result(self) -> "HLSMediaAssemblyInput":
        if not self.staging_result.complete:
            raise ValueError("HLS media assembly input requires complete staged segments.")
        if not self.staging_result.segments:
            raise ValueError("HLS media assembly input requires at least one staged segment.")
        return self


class HLSMediaAssemblyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact: ArtifactMetadata
    source_segment_count: int = Field(gt=0)
    total_duration_seconds: float = Field(gt=0)
    total_bytes: int = Field(ge=0)
    status: HLSMediaAssemblyStatus = HLSMediaAssemblyStatus.ASSEMBLED

    @model_validator(mode="after")
    def validate_artifact_kind(self) -> "HLSMediaAssemblyResult":
        if self.artifact.kind is not ArtifactKind.STAGED_MEDIA:
            raise ValueError("Assembled HLS media artifacts must use STAGED_MEDIA kind.")
        return self


def redact_hls_media_assembly_result(
    result: HLSMediaAssemblyResult,
) -> dict[str, object]:
    return {
        "artifact_id": result.artifact.artifact_id.value,
        "relative_path": result.artifact.relative_path.value,
        "format": result.artifact.format.value,
        "source_segment_count": result.source_segment_count,
        "total_duration_seconds": result.total_duration_seconds,
        "total_bytes": result.total_bytes,
        "checksum": None if result.artifact.checksum is None else result.artifact.checksum.value,
        "status": result.status.value,
    }
