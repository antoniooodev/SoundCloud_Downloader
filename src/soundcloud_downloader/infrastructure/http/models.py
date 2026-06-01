from collections.abc import Mapping
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    form_data: Mapping[str, str] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    follow_redirects: bool = False
    max_redirects: int = Field(default=3, ge=0, le=10)
    redirect_allowed_hosts: tuple[str, ...] = ()
    allow_sensitive_redirect_query: bool = False

    @field_validator("form_data", mode="before")
    @classmethod
    def validate_form_data(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, Mapping):
            raise ValueError("HTTP form data must be a mapping.")
        for key, form_value in value.items():
            if key == "":
                raise ValueError("HTTP form data keys must not be empty.")
            if not isinstance(form_value, str):
                raise ValueError("HTTP form data values must be strings.")
        return value

    @model_validator(mode="after")
    def validate_body_choice(self) -> "HttpRequest":
        if self.json_body is not None and self.form_data is not None:
            raise ValueError("HTTP requests must not set both json_body and form_data.")
        return self


class HttpResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status_code: int = Field(ge=100, le=599)
    headers: Mapping[str, str] = Field(default_factory=dict)
    text: str
    content: bytes = b""
    url_redacted: str

    @field_validator("url_redacted")
    @classmethod
    def reject_query_strings_and_fragments(cls, value: str) -> str:
        if "?" in value or "#" in value:
            raise ValueError("Redacted URLs must not contain query strings or fragments.")
        return value
