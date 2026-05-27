import asyncio

import pytest
from pydantic import ValidationError

from soundcloud_downloader.application import (
    ResolverService,
    ResolverServiceRequest,
    ResolverServiceResult,
)
from soundcloud_downloader.application.ports import (
    SoundCloudResolvedResource,
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
    SoundCloudTrackSummary,
)
from soundcloud_downloader.domain import (
    NormalizedResolverInput,
    ResolverInputType,
    SoundCloudResourceType,
)


def normalized_track(warnings: tuple[str, ...] = ()) -> NormalizedResolverInput:
    return NormalizedResolverInput(
        input_type=ResolverInputType.URL,
        resource_type=SoundCloudResourceType.TRACK,
        normalized_url="https://soundcloud.com/user/track",
        normalized_path="/user/track",
        host="soundcloud.com",
        path_parts=("user", "track"),
        warnings=warnings,
    )


def resource(
    status: SoundCloudResolveStatus,
    *,
    warnings: tuple[str, ...] = (),
) -> SoundCloudResolvedResource:
    kwargs = {}
    if status is SoundCloudResolveStatus.RESOLVED:
        kwargs["track"] = SoundCloudTrackSummary(soundcloud_id="track-1", title="Track")
    return SoundCloudResolvedResource(
        status=status,
        kind=SoundCloudResourceKind.TRACK,
        normalized=normalized_track(),
        warnings=warnings,
        **kwargs,
    )


def test_inspect_keeps_offline_behavior_and_never_calls_injected_resolver_port() -> None:
    port = FakeResolverPort(resource(SoundCloudResolveStatus.RESOLVED))

    result = ResolverService(resolver_port=port).inspect(
        ResolverServiceRequest(value="https://soundcloud.com/user/track")
    )

    assert port.called is False
    assert result.resolved is False
    assert result.requires_network_resolution is True
    assert result.resolved_resource is None


def test_resolve_with_external_resolution_false_does_not_call_resolver_port() -> None:
    port = FakeResolverPort(resource(SoundCloudResolveStatus.RESOLVED))

    result = asyncio.run(
        ResolverService(resolver_port=port).resolve(
            ResolverServiceRequest(
                value="https://soundcloud.com/user/track",
                allow_external_resolution=False,
            )
        )
    )

    assert port.called is False
    assert result.resolved is False
    assert result.requires_network_resolution is True


def test_resolve_with_external_resolution_true_and_no_port_returns_warning() -> None:
    result = asyncio.run(
        ResolverService().resolve(
            ResolverServiceRequest(
                value="https://soundcloud.com/user/track",
                allow_external_resolution=True,
            )
        )
    )

    assert result.resolved is False
    assert result.requires_network_resolution is True
    assert result.resolved_resource is None
    assert result.warnings


def test_resolve_with_fake_resolver_returns_resolved_track() -> None:
    port = FakeResolverPort(resource(SoundCloudResolveStatus.RESOLVED))

    result = asyncio.run(
        ResolverService(resolver_port=port).resolve(
            ResolverServiceRequest(
                value="https://soundcloud.com/user/track",
                allow_external_resolution=True,
            )
        )
    )

    assert port.called is True
    assert port.received == result.normalized
    assert result.resolved is True
    assert result.resolved_resource is port.resource
    assert result.resolved_resource.track is not None


def test_resolved_track_sets_no_network_resolution_required() -> None:
    result = asyncio.run(
        ResolverService(resolver_port=FakeResolverPort(resource(SoundCloudResolveStatus.RESOLVED))).resolve(
            ResolverServiceRequest(
                value="https://soundcloud.com/user/track",
                allow_external_resolution=True,
            )
        )
    )

    assert result.resolved is True
    assert result.requires_network_resolution is False


def test_fake_resolver_warnings_are_propagated() -> None:
    result = asyncio.run(
        ResolverService(
            resolver_port=FakeResolverPort(
                resource(SoundCloudResolveStatus.RESOLVED, warnings=("resolver warning",))
            )
        ).resolve(
            ResolverServiceRequest(
                value="https://soundcloud.com/user/track",
                allow_external_resolution=True,
            )
        )
    )

    assert "resolver warning" in result.warnings


def test_normalized_warnings_are_propagated() -> None:
    port = FakeResolverPort(resource(SoundCloudResolveStatus.RESOLVED))
    normalizer = FakeNormalizer(normalized_track(warnings=("normalized warning",)))

    result = asyncio.run(
        ResolverService(normalizer=normalizer, resolver_port=port).resolve(
            ResolverServiceRequest(
                value="https://soundcloud.com/user/track",
                allow_external_resolution=True,
            )
        )
    )

    assert "normalized warning" in result.warnings


def test_needs_network_result_sets_network_resolution_required() -> None:
    result = asyncio.run(_resolve_with_status(SoundCloudResolveStatus.NEEDS_NETWORK))

    assert result.resolved is False
    assert result.requires_network_resolution is True


@pytest.mark.parametrize(
    "status",
    [
        SoundCloudResolveStatus.NOT_FOUND,
        SoundCloudResolveStatus.UNSUPPORTED,
        SoundCloudResolveStatus.ERROR,
    ],
)
def test_terminal_unresolved_statuses_do_not_require_network_resolution(
    status: SoundCloudResolveStatus,
) -> None:
    result = asyncio.run(_resolve_with_status(status))

    assert result.resolved is False
    assert result.requires_network_resolution is False


def test_result_rejects_resolved_true_without_resolved_resource() -> None:
    with pytest.raises(ValidationError):
        ResolverServiceResult(
            normalized=normalized_track(),
            resolved=True,
            requires_network_resolution=False,
        )


def test_result_rejects_inconsistent_resolved_flag_and_resource_status() -> None:
    with pytest.raises(ValidationError):
        ResolverServiceResult(
            normalized=normalized_track(),
            resolved=True,
            requires_network_resolution=False,
            resolved_resource=resource(SoundCloudResolveStatus.NOT_FOUND),
        )


def test_request_default_allow_external_resolution_is_false() -> None:
    request = ResolverServiceRequest(value="https://soundcloud.com/user/track")

    assert request.allow_external_resolution is False


def test_resolver_service_result_is_immutable() -> None:
    result = ResolverService().inspect(ResolverServiceRequest(value="https://soundcloud.com/user/track"))

    with pytest.raises(ValidationError):
        result.resolved = True


async def _resolve_with_status(status: SoundCloudResolveStatus) -> ResolverServiceResult:
    return await ResolverService(resolver_port=FakeResolverPort(resource(status))).resolve(
        ResolverServiceRequest(
            value="https://soundcloud.com/user/track",
            allow_external_resolution=True,
        )
    )


class FakeResolverPort:
    def __init__(self, resource: SoundCloudResolvedResource) -> None:
        self.called = False
        self.received: NormalizedResolverInput | None = None
        self.resource = resource

    async def resolve(self, normalized: NormalizedResolverInput) -> SoundCloudResolvedResource:
        self.called = True
        self.received = normalized
        return self.resource


class FakeNormalizer:
    def __init__(self, normalized: NormalizedResolverInput) -> None:
        self.normalized = normalized

    def normalize(self, value: str) -> NormalizedResolverInput:
        return self.normalized
