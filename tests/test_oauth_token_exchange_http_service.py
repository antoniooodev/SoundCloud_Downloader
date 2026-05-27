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
    OAuthAuthorizationCode,
    OAuthClientId,
    OAuthClientSecret,
    OAuthCodeVerifier,
    OAuthRedirectUri,
    OAuthTokenResponse,
)
from soundcloud_downloader.infrastructure.http import NetworkDisabledError, SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.observability import configure_logging
from soundcloud_downloader.infrastructure.soundcloud import (
    OAuthTokenExchangeError,
    OAuthTokenExchangeService,
)


RAW_CODE = "dummy-authorization-code"
RAW_CODE_VERIFIER = "A" * 64
RAW_CLIENT_SECRET = "dummy-client-secret"
RAW_ACCESS_TOKEN = "dummy-access-token"
RAW_REFRESH_TOKEN = "dummy-refresh-token"


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
        run(_service(client, AppSettings()).exchange_authorization_code(**_exchange_kwargs()))

    assert called is False
    run(client.aclose())


def test_successful_mocked_token_response_returns_oauth_token_response() -> None:
    response = run(_exchange_with_response(_success_response_body()))

    assert isinstance(response, OAuthTokenResponse)


def test_returned_oauth_token_response_repr_does_not_expose_access_token() -> None:
    response = run(_exchange_with_response(_success_response_body()))

    assert RAW_ACCESS_TOKEN not in repr(response)


def test_returned_oauth_token_response_repr_does_not_expose_refresh_token() -> None:
    response = run(_exchange_with_response(_success_response_body()))

    assert RAW_REFRESH_TOKEN not in repr(response)


def test_raw_access_token_is_masked_in_model_dump() -> None:
    response = run(_exchange_with_response(_success_response_body()))
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


def test_request_form_includes_grant_type_authorization_code() -> None:
    form = _capture_successful_request()["form"]

    assert form["grant_type"] == ["authorization_code"]


def test_request_form_includes_client_id() -> None:
    form = _capture_successful_request()["form"]

    assert form["client_id"] == ["client-id"]


def test_request_form_includes_redirect_uri() -> None:
    form = _capture_successful_request()["form"]

    assert form["redirect_uri"] == ["http://localhost:8765/callback"]


def test_request_form_includes_code() -> None:
    form = _capture_successful_request()["form"]

    assert form["code"] == [RAW_CODE]


def test_request_form_includes_code_verifier() -> None:
    form = _capture_successful_request()["form"]

    assert form["code_verifier"] == [RAW_CODE_VERIFIER]


def test_request_form_includes_client_secret_when_provided() -> None:
    form = _capture_successful_request(client_secret=OAuthClientSecret(value=SecretStr(RAW_CLIENT_SECRET)))[
        "form"
    ]

    assert form["client_secret"] == [RAW_CLIENT_SECRET]


def test_request_form_omits_client_secret_when_none() -> None:
    form = _capture_successful_request(client_secret=None)["form"]

    assert "client_secret" not in form


def test_raw_code_code_verifier_client_secret_are_not_present_in_captured_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(AppSettings())
    run(
        _exchange_with_response(
            _success_response_body(),
            client_secret=OAuthClientSecret(value=SecretStr(RAW_CLIENT_SECRET)),
        )
    )
    output = capsys.readouterr().err

    assert RAW_CODE not in output
    assert RAW_CODE_VERIFIER not in output
    assert RAW_CLIENT_SECRET not in output


def test_raw_access_token_refresh_token_are_not_present_in_captured_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(AppSettings())
    run(_exchange_with_response(_success_response_body()))
    output = capsys.readouterr().err

    assert RAW_ACCESS_TOKEN not in output
    assert RAW_REFRESH_TOKEN not in output


@pytest.mark.parametrize("status_code", [400, 401, 403, 429, 500])
def test_error_status_raises_oauth_token_exchange_error(status_code: int) -> None:
    with pytest.raises(OAuthTokenExchangeError):
        run(_exchange_with_response(json.dumps({"error": "denied"}), status_code=status_code))


def test_invalid_json_raises_oauth_token_exchange_error() -> None:
    with pytest.raises(OAuthTokenExchangeError):
        run(_exchange_with_response("{"))


def test_2xx_response_missing_access_token_raises_oauth_token_exchange_error() -> None:
    with pytest.raises(OAuthTokenExchangeError):
        run(_exchange_with_response(json.dumps({"refresh_token": RAW_REFRESH_TOKEN})))


def test_non_positive_expires_in_response_raises_oauth_token_exchange_error() -> None:
    with pytest.raises(OAuthTokenExchangeError):
        run(_exchange_with_response(_success_response_body(expires_in=0)))


def test_tests_perform_no_real_network_calls(monkeypatch: Any) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Real network calls are not allowed in OAuth token exchange tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)

    response = run(_exchange_with_response(_success_response_body()))

    assert isinstance(response, OAuthTokenResponse)


def test_tests_write_no_files(monkeypatch: Any) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("File writes are not allowed in OAuth token exchange tests.")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    response = run(_exchange_with_response(_success_response_body()))

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
) -> OAuthTokenExchangeService:
    return OAuthTokenExchangeService(settings=settings or _settings(), http_client=client)


def _exchange_kwargs(
    *,
    client_secret: OAuthClientSecret | None = None,
) -> dict[str, object]:
    return {
        "client_id": OAuthClientId(value=SecretStr("client-id")),
        "client_secret": client_secret,
        "redirect_uri": OAuthRedirectUri(value="http://localhost:8765/callback"),
        "code": OAuthAuthorizationCode(value=SecretStr(RAW_CODE)),
        "code_verifier": OAuthCodeVerifier(value=SecretStr(RAW_CODE_VERIFIER)),
    }


async def _exchange_with_response(
    response_text: str,
    *,
    status_code: int = 200,
    client_secret: OAuthClientSecret | None = None,
) -> OAuthTokenResponse:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=response_text, request=request)

    client = SafeAsyncHttpClient(_settings(), transport=httpx.MockTransport(handler))
    try:
        response = await _service(client).exchange_authorization_code(
            **_exchange_kwargs(client_secret=client_secret)
        )
    finally:
        await client.aclose()
    return response


def _capture_successful_request(
    *,
    client_secret: OAuthClientSecret | None = None,
) -> dict[str, object]:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["content_type"] = request.headers["content-type"]
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(200, text=_success_response_body(), request=request)

    client = SafeAsyncHttpClient(_settings(), transport=httpx.MockTransport(handler))
    run(_service(client).exchange_authorization_code(**_exchange_kwargs(client_secret=client_secret)))
    run(client.aclose())
    return seen


def _success_response_body(*, expires_in: int = 3600) -> str:
    return json.dumps(
        {
            "access_token": RAW_ACCESS_TOKEN,
            "refresh_token": RAW_REFRESH_TOKEN,
            "expires_in": expires_in,
            "scope": "read",
        }
    )
