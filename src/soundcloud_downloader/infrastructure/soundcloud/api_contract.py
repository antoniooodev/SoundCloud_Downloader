from collections.abc import Mapping
from enum import Enum
from typing import Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

from soundcloud_downloader.infrastructure.http import HttpMethod
from soundcloud_downloader.infrastructure.observability import (
    REDACTED_VALUE,
    is_sensitive_field,
)

_FORBIDDEN_PARAM_NAMES = frozenset(
    {
        "authorization",
        "client_id",
        "client_secret",
        "cookie",
        "manifest_url",
        "refresh_token",
        "stream_url",
        "token",
        "access_token",
    }
)


class SoundCloudApiEndpoint(str, Enum):
    RESOLVE = "resolve"
    ME = "me"
    TRACKS = "tracks"
    PLAYLISTS = "playlists"


class SoundCloudAccessToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr
    token_type: str = "OAuth"

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("SoundCloud access token must not be empty.")
        return value

    @field_validator("token_type")
    @classmethod
    def validate_token_type(cls, value: str) -> str:
        if value != "OAuth":
            raise ValueError("SoundCloud access tokens must use OAuth token type.")
        return value


class SoundCloudApiRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: HttpMethod
    url: str = Field(min_length=1)
    headers: Mapping[str, str] = Field(default_factory=dict, repr=False)
    params: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def reject_url_query_strings_and_fragments(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.query or parsed.fragment:
            raise ValueError("SoundCloud API request URL must not contain query or fragment.")
        return value

    @model_validator(mode="after")
    def validate_params(self) -> Self:
        for key in self.params:
            normalized_key = str(key)
            if (
                normalized_key.lower() in _FORBIDDEN_PARAM_NAMES
                or is_sensitive_field(normalized_key)
            ):
                raise ValueError("SoundCloud API request params must not contain sensitive keys.")
        return self

    @field_serializer("headers")
    def serialize_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        return {
            key: REDACTED_VALUE if is_sensitive_field(key) else value
            for key, value in headers.items()
        }
