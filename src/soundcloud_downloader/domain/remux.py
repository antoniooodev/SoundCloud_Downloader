from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from soundcloud_downloader.domain.artifact import ArtifactFormat, ArtifactKind, ArtifactMetadata


class RemuxOutputFormat(str, Enum):
    M4A = "m4a"


class RemuxStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RemuxInputArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact: ArtifactMetadata

    @model_validator(mode="after")
    def validate_artifact_kind(self) -> "RemuxInputArtifact":
        if self.artifact.kind is not ArtifactKind.STAGED_MEDIA:
            raise ValueError("Remux input artifact must use STAGED_MEDIA kind.")
        return self


class RemuxOutputArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact: ArtifactMetadata
    format: RemuxOutputFormat = RemuxOutputFormat.M4A

    @model_validator(mode="after")
    def validate_output_artifact(self) -> "RemuxOutputArtifact":
        if self.artifact.kind is not ArtifactKind.FINAL_AUDIO:
            raise ValueError("Remux output artifact must use FINAL_AUDIO kind.")
        if self.artifact.format is not ArtifactFormat.M4A:
            raise ValueError("Remux output artifact must use M4A format.")
        return self


class RemuxResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_artifact: RemuxInputArtifact
    output_artifact: RemuxOutputArtifact
    status: RemuxStatus = RemuxStatus.SUCCEEDED


def redact_remux_result(result: RemuxResult) -> dict[str, object]:
    return {
        "status": result.status.value,
        "input": {
            "artifact_id": result.input_artifact.artifact.artifact_id.value,
            "relative_path": result.input_artifact.artifact.relative_path.value,
        },
        "output": {
            "artifact_id": result.output_artifact.artifact.artifact_id.value,
            "relative_path": result.output_artifact.artifact.relative_path.value,
            "format": result.output_artifact.format.value,
            "size_bytes": result.output_artifact.artifact.size_bytes,
            "checksum": (
                None
                if result.output_artifact.artifact.checksum is None
                else result.output_artifact.artifact.checksum.value
            ),
        },
    }
