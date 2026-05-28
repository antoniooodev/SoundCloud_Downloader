import asyncio
import json
import logging
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from soundcloud_downloader.application.ports import (
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
)
from soundcloud_downloader.application.resolver_input import ResolverInputNormalizer
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.infrastructure.http import NetworkDisabledError, SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.observability import configure_logging
from soundcloud_downloader.infrastructure.soundcloud import (
    OfficialSoundCloudResolver,
    SoundCloudAccessToken,
)


RAW_TOKEN = "raw-integration-access-token"


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


class FakeTokenProvider:
    def __init__(self, token: str = RAW_TOKEN) -> None:
        self.calls = 0
        self._token = token

    async def get_access_token(self) -> SoundCloudAccessToken:
        self.calls += 1
        return SoundCloudAccessToken(value=self._token)


def settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {"allow_network": True, "http_max_retries": 0}
    values.update(overrides)
    return AppSettings(**values)


def normalized_track():
    return ResolverInputNormalizer().normalize(
        "https://soundcloud.com/user/track?token=input-secret#fragment"
    )


def official_track_payload() -> dict[str, object]:
    return {
        "kind": "track",
        "id": 123,
        "title": "Official Track",
        "permalink_url": "https://soundcloud.com/user/track",
        "duration": 1_000,
        "sharing": "public",
        "downloadable": False,
        "user": {"kind": "user", "id": 456, "username": "artist"},
    }


def official_playlist_payload() -> dict[str, object]:
    return {
        "kind": "playlist",
        "id": 789,
        "title": "Official Playlist",
        "permalink_url": "https://soundcloud.com/user/sets/playlist",
        "track_count": 1,
        "tracks": [official_track_payload()],
    }


def official_user_payload() -> dict[str, object]:
    return {
        "kind": "user",
        "id": 456,
        "username": "artist",
        "permalink_url": "https://soundcloud.com/artist",
    }


def resolver_with_transport(
    handler: Callable[[httpx.Request], httpx.Response],
    app_settings: AppSettings | None = None,
    token_provider: FakeTokenProvider | None = None,
):
    effective_settings = app_settings or settings()
    provider = token_provider or FakeTokenProvider()
    client = SafeAsyncHttpClient(
        effective_settings,
        transport=httpx.MockTransport(handler),
    )
    return OfficialSoundCloudResolver(effective_settings, client, provider), client, provider


def json_response(request: httpx.Request, payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, text=json.dumps(payload), request=request)


def test_resolver_calls_resolve_with_target_url_query_parameter() -> None:
    seen_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = request.url.query.decode()
        return json_response(request, official_track_payload())

    resolver, client, _provider = resolver_with_transport(handler)

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert parse_qs(seen_query)["url"] == ["https://soundcloud.com/user/track"]
    run(client.aclose())


def test_resolver_sends_oauth_authorization_header() -> None:
    seen_authorization: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_authorization
        seen_authorization = request.headers.get("authorization")
        return json_response(request, official_track_payload())

    resolver, client, _provider = resolver_with_transport(handler)

    run(resolver.resolve(normalized_track()))

    assert seen_authorization == f"OAuth {RAW_TOKEN}"
    run(client.aclose())


def test_resolver_sends_accept_json_header() -> None:
    seen_accept: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_accept
        seen_accept = request.headers.get("accept")
        return json_response(request, official_track_payload())

    resolver, client, _provider = resolver_with_transport(handler)

    run(resolver.resolve(normalized_track()))

    assert seen_accept == "application/json; charset=utf-8"
    run(client.aclose())


def test_network_disabled_propagates_and_does_not_call_transport() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return json_response(request, official_track_payload())

    resolver, client, _provider = resolver_with_transport(
        handler,
        settings(allow_network=False),
    )

    with pytest.raises(NetworkDisabledError):
        run(resolver.resolve(normalized_track()))

    assert called is False
    run(client.aclose())


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (official_track_payload(), SoundCloudResourceKind.TRACK),
        (official_playlist_payload(), SoundCloudResourceKind.PLAYLIST),
        (official_user_payload(), SoundCloudResourceKind.USER),
    ],
)
def test_successful_mocked_response_maps_to_internal_resource(
    payload: dict[str, object],
    kind: SoundCloudResourceKind,
) -> None:
    resolver, client, _provider = resolver_with_transport(
        lambda request: json_response(request, payload)
    )

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is kind
    run(client.aclose())


@pytest.mark.parametrize(
    ("status_code", "expected_status"),
    [
        (401, SoundCloudResolveStatus.ERROR),
        (403, SoundCloudResolveStatus.ERROR),
        (404, SoundCloudResolveStatus.NOT_FOUND),
        (429, SoundCloudResolveStatus.ERROR),
        (500, SoundCloudResolveStatus.ERROR),
    ],
)
def test_http_failures_return_safe_resolver_errors(
    status_code: int,
    expected_status: SoundCloudResolveStatus,
) -> None:
    resolver, client, _provider = resolver_with_transport(
        lambda request: httpx.Response(status_code, text="raw-response-secret", request=request)
    )

    resource = run(resolver.resolve(normalized_track()))
    dumped = repr(resource) + str(resource.model_dump(mode="json"))

    assert resource.status is expected_status
    assert "raw-response-secret" not in dumped
    run(client.aclose())


def test_invalid_json_returns_safe_resolver_error() -> None:
    resolver, client, _provider = resolver_with_transport(
        lambda request: httpx.Response(200, text="{", request=request)
    )

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert resource.warnings == ("Official SoundCloud resolve returned invalid JSON.",)
    run(client.aclose())


def test_unsupported_payload_kind_returns_safe_resolver_error() -> None:
    resolver, client, _provider = resolver_with_transport(
        lambda request: json_response(request, {"kind": "comment", "id": 999})
    )

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert resource.kind is SoundCloudResourceKind.UNKNOWN
    run(client.aclose())


def test_raw_access_token_does_not_appear_in_captured_logs(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging(settings())
    resolver, client, _provider = resolver_with_transport(
        lambda request: json_response(request, official_track_payload())
    )

    run(resolver.resolve(normalized_track()))
    logging.shutdown()
    output = capsys.readouterr().err

    assert RAW_TOKEN not in output
    assert "[REDACTED]" in output
    run(client.aclose())


def test_raw_access_token_does_not_appear_in_error_result() -> None:
    resolver, client, _provider = resolver_with_transport(
        lambda request: httpx.Response(401, request=request)
    )

    resource = run(resolver.resolve(normalized_track()))
    dumped = repr(resource) + str(resource.model_dump(mode="json"))

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert RAW_TOKEN not in dumped
    run(client.aclose())


def test_raw_response_payload_secret_does_not_leak_in_error_result() -> None:
    secret = "fake-response-secret"
    resolver, client, _provider = resolver_with_transport(
        lambda request: httpx.Response(
            200,
            text=json.dumps({"kind": "track", "id": 1, "title": secret, "access_token": secret}),
            request=request,
        )
    )

    resource = run(resolver.resolve(normalized_track()))
    dumped = repr(resource) + str(resource.model_dump(mode="json"))

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert secret not in dumped
    run(client.aclose())


def test_resolver_uses_injected_token_provider() -> None:
    provider = FakeTokenProvider()
    resolver, client, used_provider = resolver_with_transport(
        lambda request: json_response(request, official_track_payload()),
        token_provider=provider,
    )

    resource = run(resolver.resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert used_provider is provider
    assert provider.calls == 1
    run(client.aclose())
