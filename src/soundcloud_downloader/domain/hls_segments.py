from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer, field_validator

from soundcloud_downloader.domain.stream_url import SoundCloudResolvedStreamUrl

_REDACTED_VALUE = "[REDACTED]"

_FORBIDDEN_HLS_URL_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "cookie",
        "refresh_token",
        "set-cookie",
    }
)


class HLSSegmentUrl(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: SecretStr) -> SecretStr:
        _validate_safe_hls_url(value.get_secret_value())
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)

    def get_secret_value(self) -> str:
        return self.value.get_secret_value()


class HLSInitializationMapUrl(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: SecretStr) -> SecretStr:
        _validate_safe_hls_url(value.get_secret_value())
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)

    def get_secret_value(self) -> str:
        return self.value.get_secret_value()


class HLSByteRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    length: int = Field(gt=0)
    offset: int | None = Field(default=None, ge=0)


class HLSSegmentReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    url: HLSSegmentUrl
    duration_seconds: float = Field(gt=0)
    title: str | None = None
    byte_range: HLSByteRange | None = None


class HLSInitializationMapReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: HLSInitializationMapUrl
    byte_range: HLSByteRange | None = None


class HLSSegmentPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest_url: SoundCloudResolvedStreamUrl
    segments: tuple[HLSSegmentReference, ...] = Field(min_length=1)
    initialization_map: HLSInitializationMapReference | None = None
    target_duration_seconds: float | None = Field(default=None, gt=0)
    media_sequence: int = Field(default=0, ge=0)
    end_list: bool = False

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def total_duration_seconds(self) -> float:
        return sum(segment.duration_seconds for segment in self.segments)


def redact_hls_segment_plan(plan: HLSSegmentPlan) -> dict[str, object]:
    return {
        "segment_count": plan.segment_count,
        "total_duration_seconds": plan.total_duration_seconds,
        "target_duration_seconds": plan.target_duration_seconds,
        "media_sequence": plan.media_sequence,
        "end_list": plan.end_list,
        "manifest_url": _REDACTED_VALUE,
        "initialization_map": _REDACTED_VALUE if plan.initialization_map is not None else None,
        "segments": [
            {
                "index": segment.index,
                "duration_seconds": segment.duration_seconds,
                "url": _REDACTED_VALUE,
                "byte_range": (
                    None
                    if segment.byte_range is None
                    else {
                        "length": segment.byte_range.length,
                        "offset": segment.byte_range.offset,
                    }
                ),
            }
            for segment in plan.segments
        ],
    }


def _validate_safe_hls_url(raw_url: str) -> None:
    parsed = urlsplit(raw_url)
    if raw_url == "" or parsed.scheme not in {"http", "https"} or parsed.netloc == "":
        raise ValueError("HLS artifact URL must be a non-empty absolute HTTP(S) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("HLS artifact URL must not contain userinfo credentials.")
    lowered_url = raw_url.lower()
    if any(forbidden_key in lowered_url for forbidden_key in _FORBIDDEN_HLS_URL_KEYS):
        raise ValueError("HLS artifact URL must not contain sensitive URL material.")
    query_keys = {key.lower() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    if query_keys & _FORBIDDEN_HLS_URL_KEYS:
        raise ValueError("HLS artifact URL must not contain sensitive query keys.")
