import socket
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.domain import (
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamKind,
    SoundCloudResolvedStreamUrl,
    SoundCloudTranscodingEndpointUrl,
    SoundCloudTranscodingFormat,
    SoundCloudTranscodingMetadata,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
)


RAW_STREAM_URL = "https://media.soundcloud.test/playlist.m3u8?Policy=dummy-policy"
RAW_ENDPOINT_URL = "https://api.soundcloud.test/media/transcoding?Policy=dummy-endpoint-policy"


def test_resolved_stream_url_accepts_absolute_https_url() -> None:
    stream_url = SoundCloudResolvedStreamUrl(value=SecretStr(RAW_STREAM_URL))

    assert stream_url.get_secret_value() == RAW_STREAM_URL


def test_resolved_stream_url_accepts_absolute_http_url() -> None:
    stream_url = SoundCloudResolvedStreamUrl(
        value=SecretStr("http://media.soundcloud.test/audio.mp3")
    )

    assert stream_url.get_secret_value().startswith("http://")


def test_resolved_stream_url_rejects_empty_url() -> None:
    with pytest.raises(ValidationError):
        SoundCloudResolvedStreamUrl(value=SecretStr(""))


def test_resolved_stream_url_rejects_relative_url() -> None:
    with pytest.raises(ValidationError):
        SoundCloudResolvedStreamUrl(value=SecretStr("/playlist.m3u8"))


def test_resolved_stream_url_rejects_userinfo_credentials() -> None:
    with pytest.raises(ValidationError):
        SoundCloudResolvedStreamUrl(
            value=SecretStr("https://user:pass@media.soundcloud.test/audio.mp3")
        )


@pytest.mark.parametrize("query_key", ["access_token", "refresh_token", "client_secret"])
def test_resolved_stream_url_rejects_sensitive_query_keys(query_key: str) -> None:
    with pytest.raises(ValidationError):
        SoundCloudResolvedStreamUrl(
            value=SecretStr(f"https://media.soundcloud.test/audio.mp3?{query_key}=raw")
        )


def test_resolved_stream_url_repr_does_not_expose_raw_url() -> None:
    assert RAW_STREAM_URL not in repr(_stream_url())


def test_resolved_stream_url_model_dump_does_not_expose_raw_url() -> None:
    dumped = _stream_url().model_dump(mode="json")

    assert dumped == {"value": "**********"}
    assert RAW_STREAM_URL not in str(dumped)


def test_resolved_stream_repr_does_not_expose_raw_url() -> None:
    assert RAW_STREAM_URL not in repr(_stream())


def test_resolved_stream_model_dump_does_not_expose_raw_url() -> None:
    dumped = _stream().model_dump(mode="json")

    assert RAW_STREAM_URL not in str(dumped)


def test_from_transcoding_classifies_hls_as_hls_manifest() -> None:
    stream = _stream(protocol=SoundCloudTranscodingProtocol.HLS)

    assert stream.kind is SoundCloudResolvedStreamKind.HLS_MANIFEST


def test_from_transcoding_classifies_progressive_as_progressive_media() -> None:
    stream = _stream(protocol=SoundCloudTranscodingProtocol.PROGRESSIVE)

    assert stream.kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA


def test_from_transcoding_classifies_unknown_as_unknown() -> None:
    stream = _stream(protocol=SoundCloudTranscodingProtocol.UNKNOWN)

    assert stream.kind is SoundCloudResolvedStreamKind.UNKNOWN


def test_is_hls_manifest_is_true_only_for_hls_manifest() -> None:
    assert _stream(protocol=SoundCloudTranscodingProtocol.HLS).is_hls_manifest is True
    assert _stream(protocol=SoundCloudTranscodingProtocol.PROGRESSIVE).is_hls_manifest is False


def test_is_progressive_media_is_true_only_for_progressive_media() -> None:
    assert _stream(protocol=SoundCloudTranscodingProtocol.PROGRESSIVE).is_progressive_media is True
    assert _stream(protocol=SoundCloudTranscodingProtocol.HLS).is_progressive_media is False


def test_empty_preset_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SoundCloudResolvedStream(
            kind=SoundCloudResolvedStreamKind.HLS_MANIFEST,
            url=_stream_url(),
            protocol=SoundCloudTranscodingProtocol.HLS,
            mime_type=SoundCloudTranscodingMimeType.AUDIO_MPEG,
            preset="",
        )


def test_empty_quality_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SoundCloudResolvedStream(
            kind=SoundCloudResolvedStreamKind.HLS_MANIFEST,
            url=_stream_url(),
            protocol=SoundCloudTranscodingProtocol.HLS,
            mime_type=SoundCloudTranscodingMimeType.AUDIO_MPEG,
            quality="",
        )


def test_domain_models_are_immutable() -> None:
    stream = _stream()

    with pytest.raises(ValidationError):
        stream.quality = "hq"


def test_resolved_stream_has_no_sensitive_or_manifest_content_fields() -> None:
    fields = set(SoundCloudResolvedStream.model_fields)

    assert fields.isdisjoint(
        {
            "access_token",
            "refresh_token",
            "manifest_content",
            "segment_urls",
        }
    )


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert _stream().url.get_secret_value() == RAW_STREAM_URL


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert _stream().is_hls_manifest is True


def _stream_url() -> SoundCloudResolvedStreamUrl:
    return SoundCloudResolvedStreamUrl(value=SecretStr(RAW_STREAM_URL))


def _stream(
    *,
    protocol: SoundCloudTranscodingProtocol = SoundCloudTranscodingProtocol.HLS,
) -> SoundCloudResolvedStream:
    return SoundCloudResolvedStream.from_transcoding(
        transcoding=_transcoding(protocol=protocol),
        url=_stream_url(),
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
