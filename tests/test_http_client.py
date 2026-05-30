import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.infrastructure.http import (
    HttpMethod,
    HttpRequest,
    HttpRequestError,
    HttpResponse,
    NetworkDisabledError,
    SafeAsyncHttpClient,
)
from soundcloud_downloader.infrastructure.observability import REDACTED_VALUE, configure_logging


def run(coro: Awaitable[object]) -> object:
    return asyncio.run(coro)


def network_settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "allow_network": True,
        "http_timeout_seconds": 5.0,
        "http_max_retries": 0,
        "http_backoff_base_seconds": 0.0,
    }
    values.update(overrides)
    return AppSettings(**values)


def test_network_disabled_error_is_raised() -> None:
    client = SafeAsyncHttpClient(AppSettings(), transport=httpx.MockTransport(lambda request: None))

    with pytest.raises(NetworkDisabledError):
        run(client.request(HttpRequest(method=HttpMethod.GET, url="https://example.test/")))

    run(client.aclose())


def test_network_disabled_does_not_call_provided_transport() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    client = SafeAsyncHttpClient(AppSettings(), transport=httpx.MockTransport(handler))

    with pytest.raises(NetworkDisabledError):
        run(client.request(HttpRequest(method=HttpMethod.GET, url="https://example.test/")))

    assert called is False
    run(client.aclose())


def test_successful_get_returns_http_response_with_redacted_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok", headers={"x-result": "yes"}, request=request)

    client = SafeAsyncHttpClient(network_settings(), transport=httpx.MockTransport(handler))

    response = run(
        client.request(
            HttpRequest(
                method=HttpMethod.GET,
                url="https://example.test/path?token=secret#fragment",
            )
        )
    )

    assert isinstance(response, HttpResponse)
    assert response.status_code == 200
    assert response.text == "ok"
    assert response.headers["x-result"] == "yes"
    assert response.url_redacted == "https://example.test/path"
    run(client.aclose())


def test_query_strings_and_fragments_are_removed_from_url_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204, request=request)

    client = SafeAsyncHttpClient(network_settings(), transport=httpx.MockTransport(handler))

    response = run(
        client.request(
            HttpRequest(
                method=HttpMethod.GET,
                url="https://cdn.example.test/audio.aac?Signature=secret#part",
            )
        )
    )

    assert "Signature" not in response.url_redacted
    assert "secret" not in response.url_redacted
    assert "#" not in response.url_redacted
    run(client.aclose())


def test_authorization_header_is_not_emitted_raw_in_logs(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(AppSettings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(network_settings(), transport=httpx.MockTransport(handler))
    run(
        client.request(
            HttpRequest(
                method=HttpMethod.GET,
                url="https://example.test/",
                headers={"Authorization": "Bearer raw-auth-token"},
            )
        )
    )
    output = capsys.readouterr().err

    assert REDACTED_VALUE in output
    assert "raw-auth-token" not in output
    run(client.aclose())


def test_cookie_header_is_not_emitted_raw_in_logs(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(AppSettings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(network_settings(), transport=httpx.MockTransport(handler))
    run(
        client.request(
            HttpRequest(
                method=HttpMethod.GET,
                url="https://example.test/",
                headers={"Cookie": "session=raw-cookie"},
            )
        )
    )
    output = capsys.readouterr().err

    assert REDACTED_VALUE in output
    assert "raw-cookie" not in output
    run(client.aclose())


def test_json_body_secret_fields_are_redacted_in_logs(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(AppSettings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(network_settings(), transport=httpx.MockTransport(handler))
    run(
        client.request(
            HttpRequest(
                method=HttpMethod.POST,
                url="https://example.test/",
                json_body={"client_secret": "raw-secret", "name": "safe"},
            )
        )
    )
    output = capsys.readouterr().err

    assert REDACTED_VALUE in output
    assert "raw-secret" not in output
    assert "safe" in output
    run(client.aclose())


def test_http_request_rejects_both_json_body_and_form_data() -> None:
    with pytest.raises(ValidationError):
        HttpRequest(
            method=HttpMethod.POST,
            url="https://example.test/",
            json_body={"name": "value"},
            form_data={"name": "value"},
        )


def test_form_data_is_sent_as_urlencoded_body() -> None:
    seen_content_type = ""
    seen_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_content_type, seen_body
        seen_content_type = request.headers["content-type"]
        seen_body = request.content
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(network_settings(), transport=httpx.MockTransport(handler))
    run(
        client.request(
            HttpRequest(
                method=HttpMethod.POST,
                url="https://example.test/",
                form_data={"grant_type": "authorization_code", "client_id": "client-id"},
            )
        )
    )

    parsed_body = parse_qs(seen_body.decode())
    assert seen_content_type.startswith("application/x-www-form-urlencoded")
    assert parsed_body["grant_type"] == ["authorization_code"]
    assert parsed_body["client_id"] == ["client-id"]
    run(client.aclose())


def test_form_data_secrets_are_redacted_in_logs(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(AppSettings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(network_settings(), transport=httpx.MockTransport(handler))
    run(
        client.request(
            HttpRequest(
                method=HttpMethod.POST,
                url="https://example.test/",
                form_data={
                    "code": "raw-code",
                    "code_verifier": "raw-code-verifier",
                    "client_secret": "raw-client-secret",
                    "client_id": "safe-client-id",
                },
            )
        )
    )
    output = capsys.readouterr().err

    assert REDACTED_VALUE in output
    assert "safe-client-id" in output
    assert "raw-code" not in output
    assert "raw-code-verifier" not in output
    assert "raw-client-secret" not in output
    run(client.aclose())


def test_raw_oauth_form_secret_values_do_not_appear_in_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(AppSettings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(network_settings(), transport=httpx.MockTransport(handler))
    raw_values = {
        "code": "dummy-authorization-code",
        "code_verifier": "A" * 64,
        "client_secret": "dummy-client-secret",
    }
    run(
        client.request(
            HttpRequest(
                method=HttpMethod.POST,
                url="https://example.test/",
                form_data=raw_values,
            )
        )
    )
    output = capsys.readouterr().err

    for raw_value in raw_values.values():
        assert raw_value not in output
    run(client.aclose())


def test_retry_happens_for_500_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, request=request)
        return httpx.Response(200, text="ok", request=request)

    client = SafeAsyncHttpClient(
        network_settings(http_max_retries=1),
        transport=httpx.MockTransport(handler),
    )

    response = run(client.request(HttpRequest(method=HttpMethod.GET, url="https://example.test/")))

    assert attempts == 2
    assert response.status_code == 200
    run(client.aclose())


def test_retry_happens_for_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, request=request)
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(
        network_settings(http_max_retries=1),
        transport=httpx.MockTransport(handler),
    )

    response = run(client.request(HttpRequest(method=HttpMethod.GET, url="https://example.test/")))

    assert attempts == 2
    assert response.status_code == 200
    run(client.aclose())


def test_no_retry_happens_for_404(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404, request=request)

    client = SafeAsyncHttpClient(
        network_settings(http_max_retries=3),
        transport=httpx.MockTransport(handler),
    )

    response = run(client.request(HttpRequest(method=HttpMethod.GET, url="https://example.test/")))

    assert attempts == 1
    assert response.status_code == 404
    run(client.aclose())


def test_redirects_are_not_followed_by_default() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(302, headers={"Location": "/next"}, request=request)

    client = SafeAsyncHttpClient(network_settings(), transport=httpx.MockTransport(handler))

    response = run(client.request(HttpRequest(method=HttpMethod.GET, url="https://example.test/")))

    assert attempts == 1
    assert response.status_code == 302
    run(client.aclose())


@pytest.mark.parametrize(
    "exception_factory",
    [
        lambda request: httpx.TimeoutException("timed out", request=request),
        lambda request: httpx.NetworkError("network failed", request=request),
    ],
)
def test_transport_exceptions_are_retried_then_raise_http_request_error(
    monkeypatch: pytest.MonkeyPatch,
    exception_factory: Callable[[httpx.Request], Exception],
) -> None:
    attempts = 0
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise exception_factory(request)

    client = SafeAsyncHttpClient(
        network_settings(http_max_retries=2),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HttpRequestError) as exc_info:
        run(client.request(HttpRequest(method=HttpMethod.GET, url="https://example.test/?token=raw")))

    assert attempts == 3
    assert exc_info.value.status_code is None
    assert "token=raw" not in str(exc_info.value)
    run(client.aclose())


def test_total_attempts_equal_one_plus_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request)

    client = SafeAsyncHttpClient(
        network_settings(http_max_retries=4),
        transport=httpx.MockTransport(handler),
    )

    response = run(client.request(HttpRequest(method=HttpMethod.GET, url="https://example.test/")))

    assert attempts == 5
    assert response.status_code == 503
    run(client.aclose())


def test_per_request_timeout_overrides_settings_timeout() -> None:
    seen_timeout: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_timeout.update(request.extensions["timeout"])
        return httpx.Response(200, request=request)

    client = SafeAsyncHttpClient(
        network_settings(http_timeout_seconds=30.0),
        transport=httpx.MockTransport(handler),
    )

    run(
        client.request(
            HttpRequest(
                method=HttpMethod.GET,
                url="https://example.test/",
                timeout_seconds=1.25,
            )
        )
    )

    assert seen_timeout["connect"] == 1.25
    assert seen_timeout["read"] == 1.25
    run(client.aclose())


def test_aclose_can_be_called_without_error() -> None:
    client = SafeAsyncHttpClient(network_settings(), transport=httpx.MockTransport(lambda request: None))

    run(client.aclose())


def test_context_manager_closes_the_client() -> None:
    async def use_client() -> httpx.AsyncClient:
        async_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None))
        async with SafeAsyncHttpClient(network_settings(), client=async_client):
            pass
        return async_client

    async_client = run(use_client())

    assert async_client.is_closed is True


def test_http_request_rejects_invalid_timeout() -> None:
    with pytest.raises(ValidationError):
        HttpRequest(method=HttpMethod.GET, url="https://example.test/", timeout_seconds=0)


def test_http_response_rejects_invalid_status_code() -> None:
    with pytest.raises(ValidationError):
        HttpResponse(status_code=99, text="", url_redacted="https://example.test/")


async def _no_sleep(delay: float) -> None:
    return None
