import socket
from pathlib import Path

import pytest
from pydantic import SecretStr

from soundcloud_downloader.application import (
    SoundCloudTranscodingEndpointRequestBuilder,
    redact_transcoding_endpoint_request,
)
from soundcloud_downloader.domain import (
    SoundCloudTranscodingEndpointUrl,
    SoundCloudTranscodingFormat,
    SoundCloudTranscodingMetadata,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
)
from soundcloud_downloader.infrastructure.http import HttpMethod, HttpRequest
from soundcloud_downloader.infrastructure.soundcloud import SoundCloudAccessToken


RAW_ENDPOINT_URL = "https://api.soundcloud.test/media/soundcloud:tracks:123/transcodings/hls"
RAW_ACCESS_TOKEN = "dummy-access-token"


def test_builder_creates_get_request() -> None:
    request = _request()

    assert request.method is HttpMethod.GET


def test_builder_uses_transcoding_endpoint_url_internally() -> None:
    request = _request()

    assert request.url == RAW_ENDPOINT_URL


def test_builder_sets_accept_json_header() -> None:
    request = _request()

    assert request.headers["accept"] == "application/json; charset=utf-8"


def test_builder_sets_authorization_oauth_header() -> None:
    request = _request()

    assert request.headers["authorization"] == f"OAuth {RAW_ACCESS_TOKEN}"


def test_builder_does_not_set_json_body() -> None:
    assert _request().json_body is None


def test_builder_does_not_set_form_data() -> None:
    assert _request().form_data is None


def test_redaction_helper_redacts_endpoint_url() -> None:
    redacted = redact_transcoding_endpoint_request(_request())

    assert redacted["url"] == "[REDACTED]"
    assert RAW_ENDPOINT_URL not in str(redacted)


def test_redaction_helper_redacts_authorization_header() -> None:
    redacted = redact_transcoding_endpoint_request(_request())

    assert isinstance(redacted["headers"], dict)
    assert redacted["headers"]["authorization"] == "[REDACTED]"


def test_redaction_helper_output_does_not_contain_raw_access_token() -> None:
    redacted = redact_transcoding_endpoint_request(_request())

    assert RAW_ACCESS_TOKEN not in str(redacted)


def test_redaction_helper_output_does_not_contain_raw_endpoint_url() -> None:
    redacted = redact_transcoding_endpoint_request(_request())

    assert RAW_ENDPOINT_URL not in str(redacted)


def test_builder_does_not_perform_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert _request().method is HttpMethod.GET


def test_builder_writes_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert _request().url == RAW_ENDPOINT_URL


def _request() -> HttpRequest:
    return SoundCloudTranscodingEndpointRequestBuilder().build_request(
        transcoding=_transcoding(),
        access_token=SoundCloudAccessToken(value=SecretStr(RAW_ACCESS_TOKEN)),
    )


def _transcoding() -> SoundCloudTranscodingMetadata:
    return SoundCloudTranscodingMetadata(
        preset="mp3_1_0",
        quality="sq",
        snipped=False,
        format=SoundCloudTranscodingFormat(
            protocol=SoundCloudTranscodingProtocol.HLS,
            mime_type=SoundCloudTranscodingMimeType.AUDIO_MPEG,
        ),
        endpoint_url=SoundCloudTranscodingEndpointUrl(value=SecretStr(RAW_ENDPOINT_URL)),
    )
