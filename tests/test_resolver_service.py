import pytest
from pydantic import ValidationError

from soundcloud_downloader.application import (
    ResolverService,
    ResolverServiceRequest,
    ResolverServiceResult,
)
from soundcloud_downloader.domain import (
    NormalizedResolverInput,
    ResolverInputType,
    SoundCloudResourceType,
)


def inspect(value: str) -> ResolverServiceResult:
    return ResolverService().inspect(ResolverServiceRequest(value=value))


def test_empty_input_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResolverServiceRequest(value="   ")


def test_input_value_is_stripped_before_normalization() -> None:
    request = ResolverServiceRequest(value="  https://soundcloud.com/user/track  ")
    result = ResolverService().inspect(request)

    assert request.value == "https://soundcloud.com/user/track"
    assert result.normalized.normalized_url == "https://soundcloud.com/user/track"


def test_track_url_requires_network_resolution_and_is_unresolved() -> None:
    result = inspect("https://soundcloud.com/user/track")

    assert result.resolved is False
    assert result.normalized.resource_type is SoundCloudResourceType.TRACK
    assert result.requires_network_resolution is True


def test_playlist_url_requires_network_resolution_and_is_unresolved() -> None:
    result = inspect("https://soundcloud.com/user/sets/playlist")

    assert result.resolved is False
    assert result.normalized.resource_type is SoundCloudResourceType.PLAYLIST
    assert result.requires_network_resolution is True


def test_user_url_requires_network_resolution_and_is_unresolved() -> None:
    result = inspect("https://soundcloud.com/user")

    assert result.resolved is False
    assert result.normalized.resource_type is SoundCloudResourceType.USER
    assert result.requires_network_resolution is True


def test_shortlink_requires_network_resolution_and_is_unresolved() -> None:
    result = inspect("https://on.soundcloud.com/abc123")

    assert result.resolved is False
    assert result.normalized.resource_type is SoundCloudResourceType.SHORTLINK
    assert result.requires_network_resolution is True


def test_raw_text_does_not_require_network_resolution() -> None:
    result = inspect("artist track name")

    assert result.resolved is False
    assert result.normalized.input_type is ResolverInputType.RAW_TEXT
    assert result.normalized.resource_type is SoundCloudResourceType.UNKNOWN
    assert result.requires_network_resolution is False


def test_unsupported_host_does_not_require_network_resolution_and_warns() -> None:
    result = inspect("https://example.test/user/track?token=secret")

    assert result.resolved is False
    assert result.normalized.resource_type is SoundCloudResourceType.UNKNOWN
    assert result.requires_network_resolution is False
    assert result.warnings


def test_reserved_soundcloud_route_returns_unknown_and_warning() -> None:
    result = inspect("https://soundcloud.com/discover")

    assert result.normalized.resource_type is SoundCloudResourceType.UNKNOWN
    assert result.requires_network_resolution is False
    assert result.warnings


def test_warnings_from_normalizer_are_propagated() -> None:
    result = inspect("https://soundcloud.com/user/sets/playlist/extra")

    assert result.normalized.warnings
    assert result.warnings == result.normalized.warnings


def test_resolver_service_result_is_immutable() -> None:
    result = inspect("https://soundcloud.com/user/track")

    with pytest.raises(ValidationError):
        result.resolved = True


def test_resolver_service_request_is_immutable() -> None:
    request = ResolverServiceRequest(value="https://soundcloud.com/user/track")

    with pytest.raises(ValidationError):
        request.value = "https://soundcloud.com/other/track"


def test_service_uses_injected_normalizer() -> None:
    class RecordingNormalizer:
        def __init__(self) -> None:
            self.value: str | None = None

        def normalize(self, value: str) -> NormalizedResolverInput:
            self.value = value
            return NormalizedResolverInput(
                input_type=ResolverInputType.URL,
                resource_type=SoundCloudResourceType.UNKNOWN,
                normalized_url="https://example.test/",
                normalized_path="/",
                host="example.test",
                warnings=("injected warning",),
            )

    normalizer = RecordingNormalizer()
    request = ResolverServiceRequest(value="  https://example.test/?token=secret  ")

    result = ResolverService(normalizer).inspect(request)

    assert normalizer.value == "https://example.test/?token=secret"
    assert result.normalized.host == "example.test"
    assert result.requires_network_resolution is False
    assert result.warnings == ("injected warning",)


def test_service_uses_local_string_normalization_only() -> None:
    result = inspect("https://soundcloud.com/user/track")

    assert result.normalized.resource_type is SoundCloudResourceType.TRACK
