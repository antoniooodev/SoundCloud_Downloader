from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soundcloud_downloader.application.resolver_input import ResolverInputNormalizer
from soundcloud_downloader.domain import (
    NormalizedResolverInput,
    SoundCloudResourceType,
)


class _ResolverInputNormalizer(Protocol):
    def normalize(self, value: str) -> NormalizedResolverInput:
        ...


class ResolverServiceRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def strip_and_validate_value(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Resolver input value must not be empty.")
        return stripped


class ResolverServiceResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    normalized: NormalizedResolverInput
    resolved: bool = False
    requires_network_resolution: bool
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_shell_result(self) -> Self:
        if self.resolved:
            raise ValueError("Resolver service shell cannot return resolved results.")
        for warning in self.normalized.warnings:
            if warning not in self.warnings:
                raise ValueError("Resolver result warnings must include normalized warnings.")
        return self


class ResolverService:
    def __init__(self, normalizer: _ResolverInputNormalizer | None = None) -> None:
        self._normalizer = normalizer or ResolverInputNormalizer()

    def inspect(self, request: ResolverServiceRequest) -> ResolverServiceResult:
        normalized = self._normalizer.normalize(request.value)
        requires_network_resolution = normalized.requires_network_resolution or normalized.resource_type in {
            SoundCloudResourceType.TRACK,
            SoundCloudResourceType.PLAYLIST,
            SoundCloudResourceType.USER,
        }
        return ResolverServiceResult(
            normalized=normalized,
            resolved=False,
            requires_network_resolution=requires_network_resolution,
            warnings=normalized.warnings,
        )
