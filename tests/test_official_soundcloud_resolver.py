import asyncio
import json
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from soundcloud_downloader.application.resolver_input import ResolverInputNormalizer
from soundcloud_downloader.application.ports import (
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
)
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.infrastructure.http import NetworkDisabledError, SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.observability import configure_logging
from soundcloud_downloader.infrastructure.soundcloud import (
    OfficialSoundCloudResolver,
    SoundCloudAccessToken,
)


def run(coro):
    return asyncio.run(coro)


class FakeTokenProvider:
    async def get_access_token(self) -> SoundCloudAccessToken:
        return SoundCloudAccessToken(value="test-secret-token")


def settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "allow_network": True,
        "http_max_retries": 0,
    }
    values.update(overrides)
    return AppSettings(**values)


def normalized_track():
    return ResolverInputNormalizer().normalize(
        "https://soundcloud.com/user/track?token=input-secret#input-fragment"
    )


def unsupported_normalized():
    return ResolverInputNormalizer().normalize("raw text")


def valid_track_json() -> str:
    return json.dumps(
        {
            "status": "resolved",
            "kind": "track",
            "track": {
                "soundcloud_id": "123",
                "title": "Official Track",
                "is_public": True,
                "is_downloadable": True,
            },
        }
    )


def resolver_with_transport(handler, app_settings: AppSettings | None = None):
    effective_settings = app_settings or settings()
    client = SafeAsyncHttpClient(
        effective_settings,
        transport=httpx.MockTransport(handler),
    )
    return OfficialSoundCloudResolver(effective_settings, client, FakeTokenProvider()), client


def test_unsupported_normalized_input_returns_unsupported_without_http_call() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text=valid_track_json(), request=request)

    resolver, client = resolver_with_transport(handler)

    resource = run(resolver.resolve(unsupported_normalized()))

    assert called is False
    assert resource.status is SoundCloudResolveStatus.UNSUPPORTED
    run(client.aclose())


def test_allow_network_false_propagates_network_disabled_and_transport_not_called() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text=valid_track_json(), request=request)

    disabled_settings = settings(allow_network=False)
    resolver, client = resolver_with_transport(handler, disabled_settings)

    with pytest.raises(NetworkDisabledError):
        run(resolver.resolve(normalized_track()))

    assert called is False
    run(client.aclose())


def test_successful_mocked_resolve_response_maps_to_resolved_track() -> None:
    resolver, client = resolver_with_transport(
        lambda request: httpx.Response(200, text=valid_track_json(), request=request)
    )

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.TRACK
    assert resource.track is not None
    assert resource.track.soundcloud_id == "123"
    run(client.aclose())


def test_resolver_sends_authorization_header_to_http_layer() -> None:
    seen_authorization: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_authorization
        seen_authorization = request.headers.get("authorization")
        return httpx.Response(200, text=valid_track_json(), request=request)

    resolver, client = resolver_with_transport(handler)

    run(resolver.resolve(normalized_track()))

    assert seen_authorization == "OAuth test-secret-token"
    run(client.aclose())


def test_raw_token_value_is_not_present_in_returned_dto_json() -> None:
    resolver, client = resolver_with_transport(
        lambda request: httpx.Response(200, text=valid_track_json(), request=request)
    )

    resource = run(resolver.resolve(normalized_track()))
    payload = resource.model_dump(mode="json")

    assert "test-secret-token" not in json.dumps(payload)
    run(client.aclose())


def test_raw_token_value_is_not_present_in_captured_logs(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging(settings())
    resolver, client = resolver_with_transport(
        lambda request: httpx.Response(200, text=valid_track_json(), request=request)
    )

    run(resolver.resolve(normalized_track()))
    logging.shutdown()
    output = capsys.readouterr().err

    assert "test-secret-token" not in output
    assert "[REDACTED]" in output
    run(client.aclose())


def test_request_url_sent_to_mock_transport_has_resolve_path() -> None:
    seen_path: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_path
        seen_path = request.url.path
        return httpx.Response(200, text=valid_track_json(), request=request)

    resolver, client = resolver_with_transport(handler)

    run(resolver.resolve(normalized_track()))

    assert seen_path == "/resolve"
    run(client.aclose())


def test_request_url_includes_sanitized_url_param() -> None:
    seen_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = request.url.query.decode()
        return httpx.Response(200, text=valid_track_json(), request=request)

    resolver, client = resolver_with_transport(handler)

    run(resolver.resolve(normalized_track()))

    params = parse_qs(seen_query)
    assert params["url"] == ["https://soundcloud.com/user/track"]
    run(client.aclose())


def test_request_url_omits_input_query_token_and_fragment() -> None:
    seen_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(200, text=valid_track_json(), request=request)

    resolver, client = resolver_with_transport(handler)

    run(resolver.resolve(normalized_track()))

    assert "input-secret" not in seen_url
    assert "input-fragment" not in seen_url
    run(client.aclose())


def test_404_maps_to_not_found() -> None:
    resolver, client = resolver_with_transport(
        lambda request: httpx.Response(404, request=request)
    )

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.NOT_FOUND
    run(client.aclose())


def test_authorization_failures_map_to_error() -> None:
    for status_code in (401, 403):
        resolver, client = resolver_with_transport(
            lambda request, status_code=status_code: httpx.Response(
                status_code,
                request=request,
            )
        )

        resource = run(resolver.resolve(normalized_track()))

        assert resource.status is SoundCloudResolveStatus.ERROR
        run(client.aclose())


def test_rate_limit_maps_to_error() -> None:
    resolver, client = resolver_with_transport(
        lambda request: httpx.Response(429, request=request)
    )

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.ERROR
    run(client.aclose())


def test_500_maps_to_error() -> None:
    resolver, client = resolver_with_transport(
        lambda request: httpx.Response(500, request=request)
    )

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.ERROR
    run(client.aclose())


def test_invalid_json_maps_to_error() -> None:
    resolver, client = resolver_with_transport(
        lambda request: httpx.Response(200, text="{", request=request)
    )

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.ERROR
    run(client.aclose())


def test_resolver_does_not_add_cookie_header() -> None:
    seen_headers: httpx.Headers | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_headers
        seen_headers = request.headers
        return httpx.Response(200, text=valid_track_json(), request=request)

    resolver, client = resolver_with_transport(handler)

    run(resolver.resolve(normalized_track()))

    assert seen_headers is not None
    assert "cookie" not in seen_headers
    run(client.aclose())


def test_resolver_does_not_add_client_credentials_params() -> None:
    seen_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = request.url.query.decode()
        return httpx.Response(200, text=valid_track_json(), request=request)

    resolver, client = resolver_with_transport(handler)

    run(resolver.resolve(normalized_track()))

    params = parse_qs(seen_query)
    assert "client_id" not in params
    assert "client_secret" not in params
    run(client.aclose())


def test_official_resolver_uses_mock_transport_only() -> None:
    resolver, client = resolver_with_transport(
        lambda request: httpx.Response(200, text=valid_track_json(), request=request)
    )

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    run(client.aclose())
