from enum import Enum
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr, field_serializer, field_validator


class SoundCloudTranscodingProtocol(str, Enum):
    HLS = "hls"
    PROGRESSIVE = "progressive"
    UNKNOWN = "unknown"


class SoundCloudTranscodingMimeType(str, Enum):
    AUDIO_MPEG = "audio/mpeg"
    AUDIO_MP4 = "audio/mp4"
    AUDIO_AAC = "audio/aac"
    APPLICATION_VND_APPLE_MPEGURL = "application/vnd.apple.mpegurl"
    UNKNOWN = "unknown"


_FORBIDDEN_ENDPOINT_QUERY_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "cookie",
        "refresh_token",
        "set-cookie",
    }
)


class SoundCloudTranscodingEndpointUrl(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: SecretStr) -> SecretStr:
        raw_url = value.get_secret_value()
        parsed = urlsplit(raw_url)
        if raw_url == "" or parsed.scheme not in {"http", "https"} or parsed.netloc == "":
            raise ValueError(
                "SoundCloud transcoding endpoint URL must be a non-empty absolute URL."
            )
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "SoundCloud transcoding endpoint URL must not contain userinfo credentials."
            )
        lowered_url = raw_url.lower()
        if any(forbidden_key in lowered_url for forbidden_key in _FORBIDDEN_ENDPOINT_QUERY_KEYS):
            raise ValueError(
                "SoundCloud transcoding endpoint URL must not contain sensitive URL material."
            )
        query_keys = {key.lower() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
        if query_keys & _FORBIDDEN_ENDPOINT_QUERY_KEYS:
            raise ValueError(
                "SoundCloud transcoding endpoint URL must not contain sensitive query keys."
            )
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)

    def get_secret_value(self) -> str:
        return self.value.get_secret_value()


class SoundCloudTranscodingFormat(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol: SoundCloudTranscodingProtocol
    mime_type: SoundCloudTranscodingMimeType


class SoundCloudTranscodingMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    preset: str | None = None
    quality: str | None = None
    snipped: bool | None = None
    format: SoundCloudTranscodingFormat
    endpoint_url: SoundCloudTranscodingEndpointUrl

    @field_validator("preset", "quality")
    @classmethod
    def validate_optional_non_empty(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("SoundCloud transcoding metadata fields must not be empty.")
        return value

    @property
    def is_hls(self) -> bool:
        return self.format.protocol is SoundCloudTranscodingProtocol.HLS

    @property
    def is_progressive(self) -> bool:
        return self.format.protocol is SoundCloudTranscodingProtocol.PROGRESSIVE

    @property
    def is_aac_like(self) -> bool:
        return self.format.mime_type in {
            SoundCloudTranscodingMimeType.AUDIO_AAC,
            SoundCloudTranscodingMimeType.AUDIO_MP4,
        }
