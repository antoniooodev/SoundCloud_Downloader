from enum import Enum
from typing import TypeAlias
from urllib.parse import parse_qsl, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SoundCloudMetadataKind(str, Enum):
    TRACK = "track"
    PLAYLIST = "playlist"
    USER = "user"


_UNSAFE_URL_MARKERS = frozenset(
    {
        "access_token",
        "refresh_token",
        "client_secret",
        "authorization",
        "cookie",
        "set-cookie",
        "set_cookie",
        "stream_url",
        "manifest_url",
    }
)


class SoundCloudResourceId(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str = Field(min_length=1)


class SoundCloudPermalinkUrl(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str

    @field_validator("value")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_safe_url(value)


class SoundCloudArtworkUrl(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str

    @field_validator("value")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_safe_url(value)


class SoundCloudUserMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: SoundCloudMetadataKind = SoundCloudMetadataKind.USER
    id: SoundCloudResourceId
    username: str = Field(min_length=1)
    display_name: str | None = None
    permalink_url: SoundCloudPermalinkUrl | None = None
    avatar_url: SoundCloudArtworkUrl | None = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("SoundCloud display name must not be empty when provided.")
        return value


class SoundCloudTrackMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: SoundCloudMetadataKind = SoundCloudMetadataKind.TRACK
    id: SoundCloudResourceId
    title: str = Field(min_length=1)
    duration_ms: int | None = Field(default=None, ge=0)
    permalink_url: SoundCloudPermalinkUrl | None = None
    artwork_url: SoundCloudArtworkUrl | None = None
    user: SoundCloudUserMetadata | None = None
    streamable: bool | None = None
    downloadable: bool | None = None
    access: str | None = None

    @field_validator("access")
    @classmethod
    def validate_access(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("SoundCloud access label must not be empty when provided.")
        return value


class SoundCloudPlaylistMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: SoundCloudMetadataKind = SoundCloudMetadataKind.PLAYLIST
    id: SoundCloudResourceId
    title: str = Field(min_length=1)
    duration_ms: int | None = Field(default=None, ge=0)
    permalink_url: SoundCloudPermalinkUrl | None = None
    artwork_url: SoundCloudArtworkUrl | None = None
    user: SoundCloudUserMetadata | None = None
    track_count: int | None = Field(default=None, ge=0)
    tracks: tuple[SoundCloudTrackMetadata, ...] = ()


SoundCloudResolvedMetadata: TypeAlias = (
    SoundCloudTrackMetadata | SoundCloudPlaylistMetadata | SoundCloudUserMetadata
)


def _validate_safe_url(value: str) -> str:
    parsed = urlparse(value)
    if value == "" or parsed.scheme not in {"http", "https"} or parsed.netloc == "":
        raise ValueError("SoundCloud metadata URL must be an absolute http or https URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("SoundCloud metadata URL must not contain credentials.")

    lowered = value.lower()
    normalized = lowered.replace("_", "-")
    for marker in _UNSAFE_URL_MARKERS:
        normalized_marker = marker.replace("_", "-")
        if marker in lowered or normalized_marker in normalized:
            raise ValueError("SoundCloud metadata URL contains unsafe fields.")

    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.strip().lower().replace("_", "-")
        if normalized_key in {marker.replace("_", "-") for marker in _UNSAFE_URL_MARKERS}:
            raise ValueError("SoundCloud metadata URL contains unsafe query fields.")
    return value
