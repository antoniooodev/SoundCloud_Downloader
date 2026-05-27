from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soundcloud_downloader.application.ports import (
    SoundCloudResolvedResource,
    SoundCloudResolverPort,
    SoundCloudResolveStatus,
)
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
    allow_external_resolution: bool = False

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
    resolved_resource: SoundCloudResolvedResource | None = None
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_result_state(self) -> Self:
        if self.resolved and self.resolved_resource is None:
            raise ValueError("Resolved resolver results require a resolved resource.")
        if self.resolved_resource is not None:
            expected_resolved = self.resolved_resource.status is SoundCloudResolveStatus.RESOLVED
            if self.resolved is not expected_resolved:
                raise ValueError("Resolver result resolved flag must match resolved resource status.")
        for warning in self.normalized.warnings:
            if warning not in self.warnings:
                raise ValueError("Resolver result warnings must include normalized warnings.")
        if self.resolved_resource is not None:
            for warning in self.resolved_resource.warnings:
                if warning not in self.warnings:
                    raise ValueError("Resolver result warnings must include resolved resource warnings.")
        return self


class ResolverService:
    def __init__(
        self,
        normalizer: _ResolverInputNormalizer | None = None,
        resolver_port: SoundCloudResolverPort | None = None,
    ) -> None:
        self._normalizer = normalizer or ResolverInputNormalizer()
        self._resolver_port = resolver_port

    def inspect(self, request: ResolverServiceRequest) -> ResolverServiceResult:
        normalized = self._normalizer.normalize(request.value)
        return self._build_shell_result(normalized)

    async def resolve(self, request: ResolverServiceRequest) -> ResolverServiceResult:
        normalized = self._normalizer.normalize(request.value)
        if not request.allow_external_resolution:
            return self._build_shell_result(normalized)

        if self._resolver_port is None:
            return self._build_shell_result(
                normalized,
                extra_warnings=("External resolution was requested but no resolver port is configured.",),
            )

        resolved_resource = await self._resolver_port.resolve(normalized)
        resolved = resolved_resource.status is SoundCloudResolveStatus.RESOLVED
        requires_network_resolution = self._requires_network_after_resolution(
            normalized,
            resolved_resource,
        )
        warnings = normalized.warnings + tuple(
            warning for warning in resolved_resource.warnings if warning not in normalized.warnings
        )
        if resolved_resource.normalized != normalized:
            warnings = warnings + (
                "Resolver port returned a resource for a different normalized input.",
            )
        return ResolverServiceResult(
            normalized=normalized,
            resolved=resolved,
            requires_network_resolution=requires_network_resolution,
            resolved_resource=resolved_resource,
            warnings=warnings,
        )

    def _build_shell_result(
        self,
        normalized: NormalizedResolverInput,
        *,
        extra_warnings: tuple[str, ...] = (),
    ) -> ResolverServiceResult:
        requires_network_resolution = normalized.requires_network_resolution or normalized.resource_type in {
            SoundCloudResourceType.TRACK,
            SoundCloudResourceType.PLAYLIST,
            SoundCloudResourceType.USER,
        }
        return ResolverServiceResult(
            normalized=normalized,
            resolved=False,
            requires_network_resolution=requires_network_resolution,
            warnings=normalized.warnings + extra_warnings,
        )

    def _requires_network_after_resolution(
        self,
        normalized: NormalizedResolverInput,
        resolved_resource: SoundCloudResolvedResource,
    ) -> bool:
        if resolved_resource.status is SoundCloudResolveStatus.RESOLVED:
            return False
        if resolved_resource.status is SoundCloudResolveStatus.NEEDS_NETWORK:
            return True
        return False
