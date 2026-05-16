from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class ResolverInputType(str, Enum):
    URL = "url"
    RAW_TEXT = "raw_text"


class SoundCloudResourceType(str, Enum):
    TRACK = "track"
    PLAYLIST = "playlist"
    USER = "user"
    SHORTLINK = "shortlink"
    UNKNOWN = "unknown"


class NormalizedResolverInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_type: ResolverInputType
    resource_type: SoundCloudResourceType
    normalized_url: str | None = None
    normalized_path: str | None = None
    host: str | None = None
    path_parts: tuple[str, ...] = ()
    requires_network_resolution: bool = False
    warnings: tuple[str, ...] = ()

    @field_validator("normalized_url")
    @classmethod
    def reject_query_strings_and_fragments(cls, value: str | None) -> str | None:
        if value is not None and ("?" in value or "#" in value):
            raise ValueError("Normalized resolver URLs must not contain query strings or fragments.")
        return value

    @field_validator("normalized_path")
    @classmethod
    def validate_normalized_path(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("/"):
            raise ValueError("Normalized resolver paths must start with '/'.")
        return value

    @field_validator("path_parts")
    @classmethod
    def reject_empty_path_parts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(part == "" for part in value):
            raise ValueError("Resolver path parts must not contain empty values.")
        return value

    @model_validator(mode="after")
    def validate_url_input(self) -> Self:
        if self.input_type is ResolverInputType.URL and self.normalized_url is None:
            raise ValueError("URL resolver inputs require a normalized URL.")
        return self
