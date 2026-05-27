from enum import Enum
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soundcloud_downloader.domain import (
    MediaCodec,
    MediaContainer,
    NormalizedResolverInput,
    SourceProtocol,
)


class SoundCloudResolveStatus(str, Enum):
    RESOLVED = "resolved"
    NEEDS_NETWORK = "needs_network"
    UNSUPPORTED = "unsupported"
    NOT_FOUND = "not_found"
    ERROR = "error"


class SoundCloudResourceKind(str, Enum):
    TRACK = "track"
    PLAYLIST = "playlist"
    USER = "user"
    SHORTLINK = "shortlink"
    UNKNOWN = "unknown"


class _SafeUrlModel(BaseModel):
    @field_validator(
        "permalink_url_redacted",
        "avatar_url_redacted",
        "artwork_url_redacted",
        check_fields=False,
    )
    @classmethod
    def reject_query_strings_and_fragments(cls, value: str | None) -> str | None:
        if value is not None and ("?" in value or "#" in value):
            raise ValueError("Redacted URL fields must not contain query strings or fragments.")
        return value


class SoundCloudUserSummary(_SafeUrlModel):
    model_config = ConfigDict(frozen=True)

    soundcloud_id: str
    username: str | None = None
    permalink: str | None = None
    permalink_url_redacted: str | None = None
    avatar_url_redacted: str | None = None


class SoundCloudTranscodingSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    preset: str | None = None
    protocol: SourceProtocol = SourceProtocol.UNKNOWN
    mime_type: str | None = None
    quality: str | None = None
    codec: MediaCodec = MediaCodec.UNKNOWN
    container: MediaContainer = MediaContainer.UNKNOWN
    requires_auth: bool = False
    is_downloadable: bool = False


class SoundCloudTrackSummary(_SafeUrlModel):
    model_config = ConfigDict(frozen=True)

    soundcloud_id: str
    title: str
    duration_ms: int | None = Field(default=None, ge=0)
    permalink: str | None = None
    permalink_url_redacted: str | None = None
    artwork_url_redacted: str | None = None
    user: SoundCloudUserSummary | None = None
    is_public: bool = False
    is_go_plus: bool = False
    is_preview_only: bool = False
    is_downloadable: bool = False
    transcodings: tuple[SoundCloudTranscodingSummary, ...] = ()


class SoundCloudPlaylistSummary(_SafeUrlModel):
    model_config = ConfigDict(frozen=True)

    soundcloud_id: str
    title: str
    permalink: str | None = None
    permalink_url_redacted: str | None = None
    artwork_url_redacted: str | None = None
    user: SoundCloudUserSummary | None = None
    track_count: int | None = Field(default=None, ge=0)
    tracks: tuple[SoundCloudTrackSummary, ...] = ()


class SoundCloudResolvedResource(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: SoundCloudResolveStatus
    kind: SoundCloudResourceKind
    normalized: NormalizedResolverInput
    track: SoundCloudTrackSummary | None = None
    playlist: SoundCloudPlaylistSummary | None = None
    user: SoundCloudUserSummary | None = None
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_resolved_payload(self) -> Self:
        if self.status is not SoundCloudResolveStatus.RESOLVED:
            return self
        if self.kind is SoundCloudResourceKind.TRACK and self.track is None:
            raise ValueError("Resolved track resources require a track payload.")
        if self.kind is SoundCloudResourceKind.PLAYLIST and self.playlist is None:
            raise ValueError("Resolved playlist resources require a playlist payload.")
        if self.kind is SoundCloudResourceKind.USER and self.user is None:
            raise ValueError("Resolved user resources require a user payload.")
        return self


@runtime_checkable
class SoundCloudResolverPort(Protocol):
    async def resolve(
        self,
        normalized: NormalizedResolverInput,
    ) -> SoundCloudResolvedResource:
        ...


@runtime_checkable
class SoundCloudMetadataPort(Protocol):
    async def get_track(self, soundcloud_id: str) -> SoundCloudTrackSummary:
        ...

    async def get_playlist(self, soundcloud_id: str) -> SoundCloudPlaylistSummary:
        ...

    async def get_user(self, soundcloud_id: str) -> SoundCloudUserSummary:
        ...
