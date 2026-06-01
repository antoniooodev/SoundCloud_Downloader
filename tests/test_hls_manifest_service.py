import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from pydantic import SecretStr

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamKind,
    SoundCloudResolvedStreamUrl,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
)
from soundcloud_downloader.infrastructure.http import (
    HttpMethod,
    HttpRequest,
    SafeAsyncHttpClient,
)
from soundcloud_downloader.infrastructure.soundcloud import (
    HLSManifestFetchFailureKind,
    SoundCloudHLSManifestRetrievalError,
    SoundCloudHLSManifestService,
    redact_hls_manifest_request,
)

T = TypeVar("T")

RAW_MANIFEST_URL = "https://media.soundcloud.test/playlist.m3u8?Policy=dummy-policy"
RAW_PROGRESSIVE_URL = "https://media.soundcloud.test/audio.mp3?Policy=dummy-policy"
SEGMENT_URL = "https://media.soundcloud.test/segment0.ts?Policy=dummy-segment-policy"
PLAIN_MANIFEST = f"""#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
{SEGMENT_URL}
#EXT-X-ENDLIST
"""
HLS_ACCEPT_HEADER = (
    "application/vnd.apple.mpegurl, application/x-mpegURL, audio/mpegurl, */*"
)


def run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)


def test_allow_network_false_reports_safe_policy_denial_and_transport_is_not_called() -> None:
    captured_requests: list[httpx.Request] = []

    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(
            _fetch_with_response(
                allow_network=False,
                captured_requests=captured_requests,
            )
        )

    assert captured_requests == []
    assert exc_info.value.failure_kind is HLSManifestFetchFailureKind.SAFE_CLIENT_POLICY_DENIED


def test_successful_mocked_manifest_fetch_returns_manifest_text() -> None:
    manifest = run(_fetch_with_response())

    assert manifest == PLAIN_MANIFEST


def test_request_is_get() -> None:
    captured_requests: list[httpx.Request] = []

    run(_fetch_with_response(captured_requests=captured_requests))

    assert captured_requests[0].method == "GET"


def test_request_uses_resolved_stream_url_internally() -> None:
    captured_requests: list[httpx.Request] = []

    run(_fetch_with_response(captured_requests=captured_requests))

    assert str(captured_requests[0].url) == RAW_MANIFEST_URL


def test_request_sends_hls_friendly_accept_header() -> None:
    captured_requests: list[httpx.Request] = []

    run(_fetch_with_response(captured_requests=captured_requests))

    assert captured_requests[0].headers["accept"] == HLS_ACCEPT_HEADER


def test_progressive_stream_is_rejected_safely() -> None:
    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(_fetch_with_response(stream=_stream(SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA)))

    assert RAW_PROGRESSIVE_URL not in str(exc_info.value)
    assert exc_info.value.failure_kind is HLSManifestFetchFailureKind.SAFE_CLIENT_POLICY_DENIED


def test_unknown_stream_is_rejected_safely() -> None:
    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(_fetch_with_response(stream=_stream(SoundCloudResolvedStreamKind.UNKNOWN)))

    assert RAW_MANIFEST_URL not in str(exc_info.value)
    assert exc_info.value.failure_kind is HLSManifestFetchFailureKind.SAFE_CLIENT_POLICY_DENIED


def test_host_not_allowed_reports_safe_failure_kind() -> None:
    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(
            _fetch_with_response(
                stream=_stream(
                    SoundCloudResolvedStreamKind.HLS_MANIFEST,
                    raw_url="https://evil.example.test/playlist.m3u8?token=SHOULD_NOT_LEAK",
                )
            )
        )

    assert exc_info.value.failure_kind is HLSManifestFetchFailureKind.HOST_NOT_ALLOWED
    assert exc_info.value.allowed_host is False
    assert "SHOULD_NOT_LEAK" not in str(exc_info.value)


def test_empty_response_body_raises_retrieval_error() -> None:
    with pytest.raises(SoundCloudHLSManifestRetrievalError):
        run(_fetch_with_response(response_text=""))


def test_non_hls_body_without_extm3u_raises_retrieval_error() -> None:
    with pytest.raises(SoundCloudHLSManifestRetrievalError):
        run(_fetch_with_response(response_text=f"not hls\n{SEGMENT_URL}\n"))


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 500])
def test_error_status_raises_retrieval_error(status_code: int) -> None:
    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(_fetch_with_response(status_code=status_code))

    assert exc_info.value.failure_kind is HLSManifestFetchFailureKind.HTTP_STATUS
    assert exc_info.value.manifest_request_status == status_code


def test_timeout_reports_safe_failure_kind() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out SHOULD_NOT_LEAK", request=request)

    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(_fetch_with_handler(handler))

    assert exc_info.value.failure_kind is HLSManifestFetchFailureKind.TIMEOUT
    assert "SHOULD_NOT_LEAK" not in str(exc_info.value)


def test_network_error_reports_safe_failure_kind() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.NetworkError("network failed SHOULD_NOT_LEAK", request=request)

    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(_fetch_with_handler(handler))

    assert exc_info.value.failure_kind is HLSManifestFetchFailureKind.NETWORK_ERROR
    assert "SHOULD_NOT_LEAK" not in str(exc_info.value)


def test_redirect_rejection_reports_safe_failure_kind() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "ftp://media.soundcloud.test/playlist.m3u8"},
            request=request,
        )

    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(_fetch_with_handler(handler))

    assert exc_info.value.failure_kind is HLSManifestFetchFailureKind.REDIRECT_REJECTED
    assert exc_info.value.redirect_count == 1


def test_redirect_host_not_allowed_reports_redirect_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "https://evil.example.test/playlist.m3u8?token=SHOULD_NOT_LEAK"},
            request=request,
        )

    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(_fetch_with_handler(handler))

    assert exc_info.value.failure_kind is HLSManifestFetchFailureKind.REDIRECT_REJECTED
    assert exc_info.value.allowed_host is False
    assert "SHOULD_NOT_LEAK" not in str(exc_info.value)


def test_caplog_does_not_contain_raw_manifest_url(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    run(_fetch_with_response())

    assert RAW_MANIFEST_URL not in caplog.text


def test_caplog_does_not_contain_manifest_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    run(_fetch_with_response())

    assert PLAIN_MANIFEST not in caplog.text


def test_caplog_does_not_contain_segment_url_from_manifest_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    run(_fetch_with_response())

    assert SEGMENT_URL not in caplog.text


def test_error_messages_do_not_contain_raw_manifest_url() -> None:
    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(_fetch_with_response(status_code=404))

    assert RAW_MANIFEST_URL not in str(exc_info.value)


def test_error_messages_do_not_contain_manifest_body() -> None:
    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(_fetch_with_response(response_text=PLAIN_MANIFEST.replace("#EXTM3U", "#INVALID")))

    assert PLAIN_MANIFEST not in str(exc_info.value)


def test_error_messages_do_not_contain_segment_url() -> None:
    with pytest.raises(SoundCloudHLSManifestRetrievalError) as exc_info:
        run(_fetch_with_response(response_text=f"not hls\n{SEGMENT_URL}\n"))

    assert SEGMENT_URL not in str(exc_info.value)


def test_redact_hls_manifest_request_redacts_url() -> None:
    request = HttpRequest(
        method=HttpMethod.GET,
        url=RAW_MANIFEST_URL,
        headers={"accept": HLS_ACCEPT_HEADER},
    )

    redacted = redact_hls_manifest_request(request)

    assert redacted == {
        "method": "GET",
        "url": "[REDACTED]",
        "headers": {"accept": HLS_ACCEPT_HEADER},
    }
    assert RAW_MANIFEST_URL not in str(redacted)


def test_tests_perform_no_real_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert run(_fetch_with_response()) == PLAIN_MANIFEST


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert run(_fetch_with_response()) == PLAIN_MANIFEST


async def _fetch_with_response(
    *,
    allow_network: bool = True,
    status_code: int = 200,
    response_text: str = PLAIN_MANIFEST,
    stream: SoundCloudResolvedStream | None = None,
    captured_requests: list[httpx.Request] | None = None,
) -> str:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured_requests is not None:
            captured_requests.append(request)
        return httpx.Response(status_code, text=response_text, request=request)

    async with SafeAsyncHttpClient(
        _settings(allow_network=allow_network),
        transport=httpx.MockTransport(handler),
    ) as http_client:
        return await SoundCloudHLSManifestService(
            http_client=http_client,
            allowed_media_hosts=("media.soundcloud.test",),
        ).fetch_manifest(
            stream=stream or _stream(SoundCloudResolvedStreamKind.HLS_MANIFEST),
        )


async def _fetch_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    stream: SoundCloudResolvedStream | None = None,
) -> str:
    async with SafeAsyncHttpClient(
        _settings(allow_network=True),
        transport=httpx.MockTransport(handler),
    ) as http_client:
        return await SoundCloudHLSManifestService(
            http_client=http_client,
            allowed_media_hosts=("media.soundcloud.test",),
        ).fetch_manifest(
            stream=stream or _stream(SoundCloudResolvedStreamKind.HLS_MANIFEST),
        )


def _settings(*, allow_network: bool) -> AppSettings:
    return AppSettings(
        allow_network=allow_network,
        http_max_retries=0,
        http_timeout_seconds=5.0,
        http_backoff_base_seconds=0.0,
    )


def _stream(
    kind: SoundCloudResolvedStreamKind,
    *,
    raw_url: str | None = None,
) -> SoundCloudResolvedStream:
    url = raw_url or (
        RAW_PROGRESSIVE_URL if kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA else RAW_MANIFEST_URL
    )
    protocol = (
        SoundCloudTranscodingProtocol.PROGRESSIVE
        if kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA
        else SoundCloudTranscodingProtocol.HLS
    )
    return SoundCloudResolvedStream(
        kind=kind,
        url=SoundCloudResolvedStreamUrl(value=SecretStr(url)),
        protocol=protocol,
        mime_type=SoundCloudTranscodingMimeType.AUDIO_MP4,
        preset="aac_0_1",
        quality="sq",
        snipped=False,
    )
