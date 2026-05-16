from collections.abc import Mapping
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"


class HttpRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: HttpMethod
    url: str = Field(min_length=1)
    headers: Mapping[str, str] = Field(default_factory=dict)
    params: Mapping[str, str | int | float | bool] = Field(default_factory=dict)
    json_body: Mapping[str, object] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


class HttpResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status_code: int = Field(ge=100, le=599)
    headers: Mapping[str, str] = Field(default_factory=dict)
    text: str
    url_redacted: str

    @field_validator("url_redacted")
    @classmethod
    def reject_query_strings_and_fragments(cls, value: str) -> str:
        if "?" in value or "#" in value:
            raise ValueError("Redacted URLs must not contain query strings or fragments.")
        return value
