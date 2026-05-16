from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soundcloud_downloader.domain.enums import DRMStatus


class HLSManifestKind(str, Enum):
    MEDIA = "media"
    MASTER = "master"
    UNKNOWN = "unknown"


class HLSDrmIndicator(BaseModel):
    model_config = ConfigDict(frozen=True)

    tag: str
    method: str | None = None
    uri_present: bool = False
    key_format: str | None = None
    raw_line_redacted: str

    @field_validator("raw_line_redacted")
    @classmethod
    def reject_unredacted_uri_query(cls, value: str) -> str:
        if "URI=" in value and "?" in value and "[redacted]" not in value:
            raise ValueError("Redacted HLS key lines must not contain URI query strings.")
        return value


class HLSManifestAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: HLSManifestKind
    is_hls: bool
    is_encrypted: bool
    drm_status: DRMStatus
    has_ext_x_key: bool
    has_ext_x_session_key: bool
    has_stream_inf: bool
    has_media_sequence: bool
    has_endlist: bool
    segment_count: int = Field(ge=0)
    drm_indicators: tuple[HLSDrmIndicator, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_encryption_state(self) -> Self:
        if self.is_encrypted and self.drm_status is DRMStatus.NONE:
            raise ValueError("Encrypted HLS analysis must not use DRMStatus.NONE.")
        if self.drm_status in {DRMStatus.ENCRYPTED_HLS, DRMStatus.EME_DRM} and not self.is_encrypted:
            raise ValueError("Encrypted DRM statuses require is_encrypted=True.")
        return self
