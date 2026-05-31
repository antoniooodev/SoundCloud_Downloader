import asyncio
import json
import socket
from collections.abc import Awaitable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    OAuthClientId,
    OAuthClientSecret,
    OAuthRefreshToken,
    OAuthTokenResponse,
)
from soundcloud_downloader.infrastructure.http import NetworkDisabledError, SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.observability import configure_logging
from soundcloud_downloader.infrastructure.soundcloud import (
    OAuthRefreshTokenError,
    OAuthRefreshTokenService,
)


RAW_CLIENT_SECRET = "dummy-client-secret"
RAW_REFRESH_TOKEN = "dummy-refresh-token"
RAW_ACCESS_TOKEN = "dummy-access-token"
RAW_NEW_REFRESH_TOKEN = "dummy-new-refresh-token"


def run(coro: Awaitable[object]) -> object:
    return asyncio.run(coro)


def test_allow_network_false_propagates_network_disabled_and_transport_not_called() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(AppSettings(), transport=httpx.MockTransport(handler))

    with pytest.raises(NetworkDisabledError):
        run(_service(client, AppSettings()).refresh_access_token(**_refresh_kwargs()))

    assert called is False
    run(client.aclose())


def test_successful_mocked_refresh_response_returns_oauth_token_response() -> None:
    response = run(_refresh_with_response(_success_response_body()))

    assert isinstance(response, OAuthTokenResponse)


def test_successful_refresh_response_with_empty_scope_is_accepted() -> None:
    response = run(_refresh_with_response(_success_response_body(scope="")))

    assert isinstance(response, OAuthTokenResponse)
    assert response.scope is None


def test_returned_oauth_token_response_repr_does_not_expose_access_token() -> None:
    response = run(_refresh_with_response(_success_response_body()))

    assert RAW_ACCESS_TOKEN not in repr(response)


def test_returned_oauth_token_response_repr_does_not_expose_refresh_token() -> None:
    response = run(_refresh_with_response(_success_response_body()))

    assert RAW_NEW_REFRESH_TOKEN not in repr(response)


def test_raw_access_token_is_masked_in_model_dump() -> None:
    response = run(_refresh_with_response(_success_response_body()))
    dumped = response.model_dump()

    assert dumped["access_token"]["value"] == "**********"
    assert dumped["refresh_token"]["value"] == "**********"
    assert RAW_ACCESS_TOKEN not in str(dumped)


def test_request_is_post_to_oauth_token() -> None:
    seen = _capture_successful_request()

    assert seen["method"] == "POST"
    assert seen["path"] == "/oauth/token"


def test_request_uses_content_type_application_x_www_form_urlencoded() -> None:
    seen = _capture_successful_request()

    assert str(seen["content_type"]).startswith("application/x-www-form-urlencoded")


def test_request_form_includes_grant_type_refresh_token() -> None:
    form = _capture_successful_request()["form"]

    assert form["grant_type"] == ["refresh_token"]


def test_request_form_includes_client_id() -> None:
    form = _capture_successful_request()["form"]

    assert form["client_id"] == ["client-id"]


def test_request_form_includes_client_secret() -> None:
    form = _capture_successful_request()["form"]

    assert form["client_secret"] == [RAW_CLIENT_SECRET]


def test_request_form_includes_refresh_token() -> None:
    form = _capture_successful_request()["form"]

    assert form["refresh_token"] == [RAW_REFRESH_TOKEN]


def test_request_form_does_not_include_authorization_code() -> None:
    form = _capture_successful_request()["form"]

    assert "code" not in form


def test_request_form_does_not_include_code_verifier() -> None:
    form = _capture_successful_request()["form"]

    assert "code_verifier" not in form


def test_raw_client_secret_refresh_token_are_not_present_in_captured_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(AppSettings())
    run(_refresh_with_response(_success_response_body()))
    output = capsys.readouterr().err

    assert RAW_CLIENT_SECRET not in output
    assert RAW_REFRESH_TOKEN not in output


def test_raw_access_token_refresh_token_response_values_are_not_present_in_captured_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(AppSettings())
    run(_refresh_with_response(_success_response_body()))
    output = capsys.readouterr().err

    assert RAW_ACCESS_TOKEN not in output
    assert RAW_NEW_REFRESH_TOKEN not in output


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_rejected_status_raises_oauth_refresh_token_error(status_code: int) -> None:
    with pytest.raises(OAuthRefreshTokenError):
        run(_refresh_with_response(json.dumps({"error": "denied"}), status_code=status_code))


def test_429_raises_oauth_refresh_token_error() -> None:
    with pytest.raises(OAuthRefreshTokenError):
        run(_refresh_with_response(json.dumps({"error": "rate_limited"}), status_code=429))


def test_500_raises_oauth_refresh_token_error() -> None:
    with pytest.raises(OAuthRefreshTokenError):
        run(_refresh_with_response(json.dumps({"error": "server_error"}), status_code=500))


def test_invalid_json_raises_oauth_refresh_token_error() -> None:
    with pytest.raises(OAuthRefreshTokenError):
        run(_refresh_with_response("{"))


def test_2xx_response_missing_access_token_raises_oauth_refresh_token_error() -> None:
    with pytest.raises(OAuthRefreshTokenError):
        run(_refresh_with_response(json.dumps({"refresh_token": RAW_NEW_REFRESH_TOKEN})))


def test_non_positive_expires_in_response_raises_safely() -> None:
    with pytest.raises(OAuthRefreshTokenError):
        run(_refresh_with_response(_success_response_body(expires_in=0)))


def test_refresh_service_does_not_persist_tokens(monkeypatch: Any) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("Refresh service must not persist refreshed tokens.")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    response = run(_refresh_with_response(_success_response_body()))

    assert isinstance(response, OAuthTokenResponse)


def test_tests_perform_no_real_network_calls(monkeypatch: Any) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Real network calls are not allowed in OAuth refresh tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)

    response = run(_refresh_with_response(_success_response_body()))

    assert isinstance(response, OAuthTokenResponse)


def test_tests_write_no_files(monkeypatch: Any) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("File writes are not allowed in OAuth refresh tests.")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    response = run(_refresh_with_response(_success_response_body()))

    assert isinstance(response, OAuthTokenResponse)


def _settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "allow_network": True,
        "http_max_retries": 0,
        "soundcloud_auth_base_url": "https://secure.soundcloud.com",
    }
    values.update(overrides)
    return AppSettings(**values)


def _service(
    client: SafeAsyncHttpClient,
    settings: AppSettings | None = None,
) -> OAuthRefreshTokenService:
    return OAuthRefreshTokenService(settings=settings or _settings(), http_client=client)


def _refresh_kwargs() -> dict[str, object]:
    return {
        "client_id": OAuthClientId(value=SecretStr("client-id")),
        "client_secret": OAuthClientSecret(value=SecretStr(RAW_CLIENT_SECRET)),
        "refresh_token": OAuthRefreshToken(value=SecretStr(RAW_REFRESH_TOKEN)),
    }


async def _refresh_with_response(
    response_text: str,
    *,
    status_code: int = 200,
) -> OAuthTokenResponse:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=response_text, request=request)

    client = SafeAsyncHttpClient(_settings(), transport=httpx.MockTransport(handler))
    try:
        response = await _service(client).refresh_access_token(**_refresh_kwargs())
    finally:
        await client.aclose()
    return response


def _capture_successful_request() -> dict[str, object]:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["content_type"] = request.headers["content-type"]
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(200, text=_success_response_body(), request=request)

    client = SafeAsyncHttpClient(_settings(), transport=httpx.MockTransport(handler))
    run(_service(client).refresh_access_token(**_refresh_kwargs()))
    run(client.aclose())
    return seen


def _success_response_body(*, expires_in: int = 3600, scope: str = "read") -> str:
    return json.dumps(
        {
            "access_token": RAW_ACCESS_TOKEN,
            "refresh_token": RAW_NEW_REFRESH_TOKEN,
            "expires_in": expires_in,
            "scope": scope,
        }
    )
