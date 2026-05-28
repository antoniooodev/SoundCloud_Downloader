import socket
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.domain import (
    SoundCloudTranscodingEndpointUrl,
    SoundCloudTranscodingFormat,
    SoundCloudTranscodingMetadata,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
)


RAW_ENDPOINT_URL = "https://api.soundcloud.test/media/soundcloud:tracks:123/transcodings/hls"


def test_endpoint_url_accepts_absolute_https_url() -> None:
    endpoint_url = SoundCloudTranscodingEndpointUrl(value=SecretStr(RAW_ENDPOINT_URL))

    assert endpoint_url.get_secret_value() == RAW_ENDPOINT_URL


def test_endpoint_url_accepts_absolute_http_url() -> None:
    endpoint_url = SoundCloudTranscodingEndpointUrl(
        value=SecretStr("http://api.soundcloud.test/media/transcoding")
    )

    assert endpoint_url.get_secret_value().startswith("http://")


def test_endpoint_url_rejects_empty_url() -> None:
    with pytest.raises(ValidationError):
        SoundCloudTranscodingEndpointUrl(value=SecretStr(""))


def test_endpoint_url_rejects_relative_url() -> None:
    with pytest.raises(ValidationError):
        SoundCloudTranscodingEndpointUrl(value=SecretStr("/media/transcoding"))


def test_endpoint_url_rejects_userinfo_credentials() -> None:
    with pytest.raises(ValidationError):
        SoundCloudTranscodingEndpointUrl(
            value=SecretStr("https://user:pass@api.soundcloud.test/media/transcoding")
        )


@pytest.mark.parametrize(
    "query_key",
    ["access_token", "refresh_token", "client_secret", "authorization", "cookie", "set-cookie"],
)
def test_endpoint_url_rejects_sensitive_query_keys(query_key: str) -> None:
    with pytest.raises(ValidationError):
        SoundCloudTranscodingEndpointUrl(
            value=SecretStr(f"https://api.soundcloud.test/media/transcoding?{query_key}=raw")
        )


def test_endpoint_url_repr_does_not_expose_raw_url() -> None:
    assert RAW_ENDPOINT_URL not in repr(_endpoint_url())


def test_endpoint_url_model_dump_does_not_expose_raw_url() -> None:
    dumped = _endpoint_url().model_dump(mode="json")

    assert dumped == {"value": "**********"}
    assert RAW_ENDPOINT_URL not in str(dumped)


def test_transcoding_metadata_repr_does_not_expose_raw_endpoint_url() -> None:
    assert RAW_ENDPOINT_URL not in repr(_metadata())


def test_transcoding_metadata_model_dump_does_not_expose_raw_endpoint_url() -> None:
    dumped = _metadata().model_dump(mode="json")

    assert RAW_ENDPOINT_URL not in str(dumped)


def test_transcoding_metadata_has_is_hls_true_for_hls() -> None:
    assert _metadata(protocol=SoundCloudTranscodingProtocol.HLS).is_hls is True


def test_transcoding_metadata_has_is_progressive_true_for_progressive() -> None:
    metadata = _metadata(protocol=SoundCloudTranscodingProtocol.PROGRESSIVE)

    assert metadata.is_progressive is True


def test_unknown_protocol_is_representable() -> None:
    metadata = _metadata(protocol=SoundCloudTranscodingProtocol.UNKNOWN)

    assert metadata.format.protocol is SoundCloudTranscodingProtocol.UNKNOWN


def test_unknown_mime_type_is_representable() -> None:
    metadata = _metadata(mime_type=SoundCloudTranscodingMimeType.UNKNOWN)

    assert metadata.format.mime_type is SoundCloudTranscodingMimeType.UNKNOWN


def test_empty_preset_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _metadata(preset="")


def test_empty_quality_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _metadata(quality="")


def test_domain_models_are_immutable() -> None:
    metadata = _metadata()

    with pytest.raises(ValidationError):
        metadata.quality = "hq"


def test_transcoding_metadata_has_no_final_stream_or_manifest_url_fields() -> None:
    fields = set(SoundCloudTranscodingMetadata.model_fields)

    assert fields.isdisjoint({"stream_url", "manifest_url", "final_url"})


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert _metadata().endpoint_url.get_secret_value() == RAW_ENDPOINT_URL


def test_tests_write_no_files(tmp_path: Path) -> None:
    before = set(tmp_path.iterdir())

    assert _metadata().is_hls is True
    assert set(tmp_path.iterdir()) == before


def _endpoint_url() -> SoundCloudTranscodingEndpointUrl:
    return SoundCloudTranscodingEndpointUrl(value=SecretStr(RAW_ENDPOINT_URL))


def _metadata(
    *,
    protocol: SoundCloudTranscodingProtocol = SoundCloudTranscodingProtocol.HLS,
    mime_type: SoundCloudTranscodingMimeType = SoundCloudTranscodingMimeType.AUDIO_MPEG,
    preset: str | None = "mp3_1_0",
    quality: str | None = "sq",
) -> SoundCloudTranscodingMetadata:
    return SoundCloudTranscodingMetadata(
        preset=preset,
        quality=quality,
        snipped=False,
        format=SoundCloudTranscodingFormat(protocol=protocol, mime_type=mime_type),
        endpoint_url=_endpoint_url(),
    )
