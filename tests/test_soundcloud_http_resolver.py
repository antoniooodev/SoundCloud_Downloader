import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from soundcloud_downloader.application.resolver_input import ResolverInputNormalizer
from soundcloud_downloader.application.ports import (
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
)
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.infrastructure.http import NetworkDisabledError, SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.soundcloud import SoundCloudHttpResolver


def run(coro):
    return asyncio.run(coro)


def settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "allow_network": True,
        "http_max_retries": 0,
        "soundcloud_resolve_endpoint": "https://resolver.example.invalid/resolve",
    }
    values.update(overrides)
    return AppSettings(**values)


def normalized_track():
    return ResolverInputNormalizer().normalize("https://soundcloud.com/user/track?token=secret#frag")


def valid_track_json() -> str:
    return json.dumps(
        {
            "status": "resolved",
            "kind": "track",
            "track": {
                "soundcloud_id": "123",
                "title": "Track",
                "is_public": True,
                "is_downloadable": True,
            },
        }
    )


def test_missing_endpoint_returns_error_without_http_call() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(settings(soundcloud_resolve_endpoint=None), transport=httpx.MockTransport(handler))
    resource = run(
        SoundCloudHttpResolver(settings(soundcloud_resolve_endpoint=None), client).resolve(
            normalized_track()
        )
    )

    assert called is False
    assert resource.status is SoundCloudResolveStatus.ERROR
    run(client.aclose())


def test_unsupported_unknown_input_returns_unsupported_without_http_call() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(settings(), transport=httpx.MockTransport(handler))
    unknown = ResolverInputNormalizer().normalize("https://example.invalid/path")

    resource = run(SoundCloudHttpResolver(settings(), client).resolve(unknown))

    assert called is False
    assert resource.status is SoundCloudResolveStatus.UNSUPPORTED
    run(client.aclose())


def test_allow_network_false_propagates_network_disabled_and_transport_not_called() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, request=request)

    disabled_settings = settings(allow_network=False)
    client = SafeAsyncHttpClient(disabled_settings, transport=httpx.MockTransport(handler))

    with pytest.raises(NetworkDisabledError):
        run(SoundCloudHttpResolver(disabled_settings, client).resolve(normalized_track()))

    assert called is False
    run(client.aclose())


def test_successful_2xx_mocked_response_maps_to_resolved_track() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=valid_track_json(), request=request)

    client = SafeAsyncHttpClient(settings(), transport=httpx.MockTransport(handler))

    resource = run(SoundCloudHttpResolver(settings(), client).resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.TRACK
    assert resource.track is not None
    assert resource.track.soundcloud_id == "123"
    run(client.aclose())


def test_resolver_sends_only_sanitized_params() -> None:
    seen_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = request.url.query.decode()
        return httpx.Response(200, text=valid_track_json(), request=request)

    client = SafeAsyncHttpClient(settings(), transport=httpx.MockTransport(handler))

    run(SoundCloudHttpResolver(settings(), client).resolve(normalized_track()))

    params = parse_qs(seen_query)
    assert params["url"] == ["https://soundcloud.com/user/track"]
    assert params["path"] == ["/user/track"]
    assert params["resource_type"] == ["track"]
    assert "frag" not in seen_query
    assert "secret" not in seen_query
    run(client.aclose())


def test_404_response_maps_to_not_found() -> None:
    client = SafeAsyncHttpClient(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(404, request=request)),
    )

    resource = run(SoundCloudHttpResolver(settings(), client).resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.NOT_FOUND
    run(client.aclose())


def test_500_response_maps_to_error() -> None:
    client = SafeAsyncHttpClient(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request)),
    )

    resource = run(SoundCloudHttpResolver(settings(), client).resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.ERROR
    run(client.aclose())


def test_invalid_json_maps_to_error() -> None:
    client = SafeAsyncHttpClient(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="{", request=request)),
    )

    resource = run(SoundCloudHttpResolver(settings(), client).resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.ERROR
    run(client.aclose())


def test_resolver_does_not_add_authorization_or_cookie_headers() -> None:
    seen_headers: httpx.Headers | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_headers
        seen_headers = request.headers
        return httpx.Response(200, text=valid_track_json(), request=request)

    client = SafeAsyncHttpClient(settings(), transport=httpx.MockTransport(handler))

    run(SoundCloudHttpResolver(settings(), client).resolve(normalized_track()))

    assert seen_headers is not None
    assert "authorization" not in seen_headers
    assert "cookie" not in seen_headers
    run(client.aclose())


def test_resolver_does_not_expose_configured_endpoint_in_dto_output() -> None:
    client = SafeAsyncHttpClient(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=valid_track_json(), request=request)),
    )

    resource = run(SoundCloudHttpResolver(settings(), client).resolve(normalized_track()))
    payload = resource.model_dump(mode="json")

    assert "resolver.example.invalid" not in json.dumps(payload)
    run(client.aclose())


def test_settings_reject_endpoint_with_query_fragment_or_credentials() -> None:
    for endpoint in (
        "https://example.invalid/resolve?token=secret",
        "https://example.invalid/resolve#frag",
        "https://user:pass@example.invalid/resolve",
    ):
        with pytest.raises(ValidationError):
            settings(soundcloud_resolve_endpoint=endpoint)


def test_http_resolver_uses_mock_transport_only() -> None:
    client = SafeAsyncHttpClient(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=valid_track_json(), request=request)),
    )

    resource = run(SoundCloudHttpResolver(settings(), client).resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    run(client.aclose())
