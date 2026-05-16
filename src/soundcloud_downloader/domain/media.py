from pydantic import BaseModel, ConfigDict, Field

from soundcloud_downloader.domain.enums import (
    AccessMode,
    DRMStatus,
    MediaCodec,
    MediaContainer,
    SourceProtocol,
)


class MediaSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str | None = None
    protocol: SourceProtocol
    mime_type: str | None = None
    codec: MediaCodec = MediaCodec.UNKNOWN
    container: MediaContainer = MediaContainer.UNKNOWN
    bitrate_kbps: int | None = Field(default=None, gt=0)
    requires_auth: bool = False
    is_downloadable: bool = False
    drm_status: DRMStatus = DRMStatus.UNKNOWN


class TrackAccessContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_mode: AccessMode
    is_authenticated: bool = False
    has_go_plus: bool = False
    is_public: bool = False
    is_go_plus_track: bool = False
    is_preview_only: bool = False
    is_downloadable: bool = False
    is_own_track: bool = False
    offline_allowed: bool | None = None
    source: MediaSource | None = None
