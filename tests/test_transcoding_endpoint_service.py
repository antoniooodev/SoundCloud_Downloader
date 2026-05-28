import asyncio
import json
import logging
import socket
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from pydantic import SecretStr

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamKind,
    SoundCloudTranscodingEndpointUrl,
    SoundCloudTranscodingFormat,
    SoundCloudTranscodingMetadata,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
)
from soundcloud_downloader.infrastructure.http import NetworkDisabledError, SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.soundcloud import (
    SoundCloudAccessToken,
    SoundCloudTranscodingEndpointError,
    SoundCloudTranscodingEndpointService,
    redact_resolved_stream,
)


T = TypeVar("T")

RAW_ENDPOINT_URL = "https://api.soundcloud.test/media/transcoding?Policy=dummy-endpoint-policy"
RAW_HLS_STREAM_URL = "https://media.soundcloud.test/playlist.m3u8?Policy=dummy-stream-policy"
RAW_PROGRESSIVE_STREAM_URL = "https://media.soundcloud.test/audio.mp3?Policy=dummy-stream-policy"
RAW_ACCESS_TOKEN = "dummy-access-token"


def run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)


def test_allow_network_false_propagates_network_disabled_and_transport_is_not_called() -> None:
    captured_requests: list[httpx.Request] = []

    with pytest.raises(NetworkDisabledError):
        run(
            _resolve_with_response(
                allow_network=False,
                captured_requests=captured_requests,
            )
        )

    assert captured_requests == []


def test_successful_mocked_hls_endpoint_response_returns_resolved_stream() -> None:
    stream = run(_resolve_with_response(protocol=SoundCloudTranscodingProtocol.HLS))

    assert isinstance(stream, SoundCloudResolvedStream)
    assert stream.url.get_secret_value() == RAW_HLS_STREAM_URL


def test_successful_mocked_progressive_endpoint_response_returns_resolved_stream() -> None:
    stream = run(
        _resolve_with_response(
            protocol=SoundCloudTranscodingProtocol.PROGRESSIVE,
            stream_url=RAW_PROGRESSIVE_STREAM_URL,
        )
    )

    assert isinstance(stream, SoundCloudResolvedStream)
    assert stream.url.get_secret_value() == RAW_PROGRESSIVE_STREAM_URL


def test_hls_response_is_classified_as_hls_manifest() -> None:
    stream = run(_resolve_with_response(protocol=SoundCloudTranscodingProtocol.HLS))

    assert stream.kind is SoundCloudResolvedStreamKind.HLS_MANIFEST


def test_progressive_response_is_classified_as_progressive_media() -> None:
    stream = run(
        _resolve_with_response(
            protocol=SoundCloudTranscodingProtocol.PROGRESSIVE,
            stream_url=RAW_PROGRESSIVE_STREAM_URL,
        )
    )

    assert stream.kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA


def test_request_is_get() -> None:
    captured_requests: list[httpx.Request] = []

    run(_resolve_with_response(captured_requests=captured_requests))

    assert captured_requests[0].method == "GET"


def test_request_uses_transcoding_endpoint_url_internally() -> None:
    captured_requests: list[httpx.Request] = []

    run(_resolve_with_response(captured_requests=captured_requests))

    assert str(captured_requests[0].url) == RAW_ENDPOINT_URL


def test_request_sends_authorization_oauth_header() -> None:
    captured_requests: list[httpx.Request] = []

    run(_resolve_with_response(captured_requests=captured_requests))

    assert captured_requests[0].headers["authorization"] == f"OAuth {RAW_ACCESS_TOKEN}"


def test_request_sends_accept_json_header() -> None:
    captured_requests: list[httpx.Request] = []

    run(_resolve_with_response(captured_requests=captured_requests))

    assert captured_requests[0].headers["accept"] == "application/json; charset=utf-8"


def test_successful_response_repr_does_not_expose_final_url() -> None:
    stream = run(_resolve_with_response())

    assert RAW_HLS_STREAM_URL not in repr(stream)


def test_successful_response_model_dump_does_not_expose_final_url() -> None:
    stream = run(_resolve_with_response())

    assert RAW_HLS_STREAM_URL not in str(stream.model_dump(mode="json"))


def test_caplog_does_not_contain_access_token(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    run(_resolve_with_response())

    assert RAW_ACCESS_TOKEN not in caplog.text


def test_caplog_does_not_contain_endpoint_url(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    run(_resolve_with_response())

    assert RAW_ENDPOINT_URL not in caplog.text


def test_caplog_does_not_contain_final_url(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    run(_resolve_with_response())

    assert RAW_HLS_STREAM_URL not in caplog.text


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 500])
def test_error_status_raises_transcoding_endpoint_error(status_code: int) -> None:
    with pytest.raises(SoundCloudTranscodingEndpointError):
        run(_resolve_with_response(status_code=status_code))


def test_invalid_json_raises_transcoding_endpoint_error() -> None:
    with pytest.raises(SoundCloudTranscodingEndpointError):
        run(_resolve_with_response(response_text="not-json"))


def test_2xx_missing_url_raises_transcoding_endpoint_error() -> None:
    with pytest.raises(SoundCloudTranscodingEndpointError):
        run(_resolve_with_response(response_payload={}))


def test_2xx_non_string_url_raises_transcoding_endpoint_error() -> None:
    with pytest.raises(SoundCloudTranscodingEndpointError):
        run(_resolve_with_response(response_payload={"url": 123}))


def test_2xx_empty_url_raises_transcoding_endpoint_error() -> None:
    with pytest.raises(SoundCloudTranscodingEndpointError):
        run(_resolve_with_response(response_payload={"url": ""}))


def test_2xx_unsafe_url_raises_transcoding_endpoint_error() -> None:
    with pytest.raises(SoundCloudTranscodingEndpointError):
        run(
            _resolve_with_response(
                response_payload={
                    "url": "https://media.soundcloud.test/audio.mp3?access_token=raw"
                }
            )
        )


@pytest.mark.parametrize(
    "status_code",
    [400, 401, 403, 404, 429, 500],
)
def test_error_messages_do_not_contain_access_token(status_code: int) -> None:
    with pytest.raises(SoundCloudTranscodingEndpointError) as exc_info:
        run(_resolve_with_response(status_code=status_code))

    assert RAW_ACCESS_TOKEN not in str(exc_info.value)


def test_error_messages_do_not_contain_endpoint_url() -> None:
    with pytest.raises(SoundCloudTranscodingEndpointError) as exc_info:
        run(_resolve_with_response(status_code=404))

    assert RAW_ENDPOINT_URL not in str(exc_info.value)


def test_error_messages_do_not_contain_final_url() -> None:
    with pytest.raises(SoundCloudTranscodingEndpointError) as exc_info:
        run(_resolve_with_response(response_payload={"url": "not-a-url"}))

    assert RAW_HLS_STREAM_URL not in str(exc_info.value)


def test_redact_resolved_stream_redacts_final_url() -> None:
    redacted = redact_resolved_stream(run(_resolve_with_response()))

    assert redacted["url"] == "[REDACTED]"
    assert RAW_HLS_STREAM_URL not in str(redacted)


def test_redact_resolved_stream_does_not_contain_access_token_or_endpoint_url() -> None:
    redacted = redact_resolved_stream(run(_resolve_with_response()))

    assert RAW_ACCESS_TOKEN not in str(redacted)
    assert RAW_ENDPOINT_URL not in str(redacted)


def test_tests_perform_no_real_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    stream = run(_resolve_with_response())

    assert stream.kind is SoundCloudResolvedStreamKind.HLS_MANIFEST


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    stream = run(_resolve_with_response())

    assert stream.kind is SoundCloudResolvedStreamKind.HLS_MANIFEST


async def _resolve_with_response(
    *,
    allow_network: bool = True,
    status_code: int = 200,
    response_payload: dict[str, object] | None = None,
    response_text: str | None = None,
    protocol: SoundCloudTranscodingProtocol = SoundCloudTranscodingProtocol.HLS,
    stream_url: str = RAW_HLS_STREAM_URL,
    captured_requests: list[httpx.Request] | None = None,
) -> SoundCloudResolvedStream:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured_requests is not None:
            captured_requests.append(request)
        text = response_text
        if text is None:
            payload = response_payload if response_payload is not None else {"url": stream_url}
            text = json.dumps(payload)
        return httpx.Response(status_code, text=text, request=request)

    async with SafeAsyncHttpClient(
        _settings(allow_network=allow_network),
        transport=httpx.MockTransport(handler),
    ) as http_client:
        return await SoundCloudTranscodingEndpointService(http_client=http_client).resolve_stream_url(
            transcoding=_transcoding(protocol=protocol),
            access_token=SoundCloudAccessToken(value=SecretStr(RAW_ACCESS_TOKEN)),
        )


def _settings(*, allow_network: bool) -> AppSettings:
    return AppSettings(
        allow_network=allow_network,
        http_max_retries=0,
        http_timeout_seconds=5.0,
        http_backoff_base_seconds=0.0,
    )


def _transcoding(
    *,
    protocol: SoundCloudTranscodingProtocol = SoundCloudTranscodingProtocol.HLS,
) -> SoundCloudTranscodingMetadata:
    return SoundCloudTranscodingMetadata(
        preset="mp3_1_0",
        quality="sq",
        snipped=False,
        format=SoundCloudTranscodingFormat(
            protocol=protocol,
            mime_type=SoundCloudTranscodingMimeType.AUDIO_MPEG,
        ),
        endpoint_url=SoundCloudTranscodingEndpointUrl(value=SecretStr(RAW_ENDPOINT_URL)),
    )
