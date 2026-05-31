from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr, field_serializer, field_validator

from soundcloud_downloader.domain.transcoding import (
    SoundCloudTranscodingMetadata,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
)


class SoundCloudResolvedStreamKind(str, Enum):
    HLS_MANIFEST = "hls_manifest"
    PROGRESSIVE_MEDIA = "progressive_media"
    UNKNOWN = "unknown"


class SoundCloudResolvedStreamUrl(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: SecretStr) -> SecretStr:
        raw_url = value.get_secret_value()
        parsed = urlsplit(raw_url)
        if raw_url == "" or parsed.scheme not in {"http", "https"} or parsed.netloc == "":
            raise ValueError("SoundCloud resolved stream URL must be a non-empty absolute URL.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("SoundCloud resolved stream URL must not contain userinfo credentials.")
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)

    def get_secret_value(self) -> str:
        return self.value.get_secret_value()


class SoundCloudResolvedStream(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: SoundCloudResolvedStreamKind
    url: SoundCloudResolvedStreamUrl
    protocol: SoundCloudTranscodingProtocol
    mime_type: SoundCloudTranscodingMimeType
    preset: str | None = None
    quality: str | None = None
    snipped: bool | None = None

    @field_validator("preset", "quality")
    @classmethod
    def validate_optional_non_empty(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("SoundCloud resolved stream metadata fields must not be empty.")
        return value

    @classmethod
    def from_transcoding(
        cls,
        *,
        transcoding: SoundCloudTranscodingMetadata,
        url: SoundCloudResolvedStreamUrl,
    ) -> "SoundCloudResolvedStream":
        return cls(
            kind=_stream_kind(transcoding.format.protocol),
            url=url,
            protocol=transcoding.format.protocol,
            mime_type=transcoding.format.mime_type,
            preset=transcoding.preset,
            quality=transcoding.quality,
            snipped=transcoding.snipped,
        )

    @property
    def is_hls_manifest(self) -> bool:
        return self.kind is SoundCloudResolvedStreamKind.HLS_MANIFEST

    @property
    def is_progressive_media(self) -> bool:
        return self.kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA


def _stream_kind(protocol: SoundCloudTranscodingProtocol) -> SoundCloudResolvedStreamKind:
    if protocol is SoundCloudTranscodingProtocol.HLS:
        return SoundCloudResolvedStreamKind.HLS_MANIFEST
    if protocol is SoundCloudTranscodingProtocol.PROGRESSIVE:
        return SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA
    return SoundCloudResolvedStreamKind.UNKNOWN
