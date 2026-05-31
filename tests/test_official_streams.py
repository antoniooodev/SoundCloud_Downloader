import asyncio

import httpx
import pytest
from pydantic import SecretStr

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    SoundCloudResolvedStreamKind,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
)
from soundcloud_downloader.infrastructure.http import SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.soundcloud import (
    NoOfficialHLSStreamsError,
    OfficialStreamsClient,
    OfficialStreamsEndpointError,
)
from soundcloud_downloader.infrastructure.soundcloud.api_contract import SoundCloudAccessToken

RAW_HLS_URL = (
    "https://playback.media-streaming.soundcloud.cloud/track/aac_160k/uuid/playlist.m3u8"
    "?client_secret=SHOULD_NOT_LEAK"
)
HLS_AAC_96_URL = "https://playback.media-streaming.soundcloud.cloud/track/aac_96k/uuid/playlist.m3u8"
HLS_MP3_URL = "https://playback.media-streaming.soundcloud.cloud/track/mp3_128/uuid/playlist.m3u8"
HLS_OPUS_URL = "https://playback.media-streaming.soundcloud.cloud/track/opus_64/uuid/playlist.m3u8"
HTTP_MP3_URL = "https://playback.media-streaming.soundcloud.cloud/track/mp3_128/uuid/media.mp3"
PREVIEW_URL = "https://playback.media-streaming.soundcloud.cloud/track/preview/uuid/media.mp3"


def test_streams_client_accepts_hls_aac_160_url() -> None:
    stream, _transport = _resolve({"hls_aac_160_url": RAW_HLS_URL})

    assert stream.kind is SoundCloudResolvedStreamKind.HLS_MANIFEST
    assert stream.protocol is SoundCloudTranscodingProtocol.HLS
    assert stream.mime_type is SoundCloudTranscodingMimeType.AUDIO_MP4
    assert stream.url.get_secret_value() == RAW_HLS_URL


def test_streams_client_accepts_hls_aac_96_url() -> None:
    stream, _transport = _resolve({"hls_aac_96_url": HLS_AAC_96_URL})

    assert stream.url.get_secret_value() == HLS_AAC_96_URL
    assert stream.preset == "hls_aac_96"


def test_streams_client_prefers_hls_aac_160_over_hls_aac_96() -> None:
    stream, _transport = _resolve(
        {
            "hls_aac_96_url": HLS_AAC_96_URL,
            "hls_aac_160_url": RAW_HLS_URL,
        }
    )

    assert stream.url.get_secret_value() == RAW_HLS_URL


@pytest.mark.parametrize(
    ("field_name", "raw_url"),
    [
        ("hls_mp3_128_url", HLS_MP3_URL),
        ("hls_opus_64_url", HLS_OPUS_URL),
    ],
)
def test_streams_client_accepts_legacy_hls_fallbacks(field_name: str, raw_url: str) -> None:
    stream, _transport = _resolve({field_name: raw_url})

    assert stream.url.get_secret_value() == raw_url
    assert stream.preset == field_name.removesuffix("_url")


def test_streams_client_ignores_progressive_and_preview_streams() -> None:
    with pytest.raises(NoOfficialHLSStreamsError):
        _resolve(
            {
                "http_mp3_128_url": HTTP_MP3_URL,
                "preview_mp3_128_url": PREVIEW_URL,
                "unknown_url": RAW_HLS_URL,
            }
        )


def test_streams_client_uses_authorization_bearer_and_track_urn() -> None:
    _stream, transport = _resolve({"hls_aac_160_url": RAW_HLS_URL}, track_urn="soundcloud:tracks:123")

    assert transport.paths == ["/tracks/soundcloud%3Atracks%3A123/streams"]
    assert transport.authorizations == ["Bearer raw-access-token"]


def test_streams_client_direct_hls_url_is_redacted_in_repr_and_dump() -> None:
    stream, _transport = _resolve({"hls_aac_160_url": RAW_HLS_URL})

    dumped = str(stream.model_dump(mode="json"))

    assert RAW_HLS_URL not in repr(stream)
    assert RAW_HLS_URL not in dumped
    assert "SHOULD_NOT_LEAK" not in repr(stream)
    assert "SHOULD_NOT_LEAK" not in dumped


def test_streams_client_http_failure_is_safe() -> None:
    with pytest.raises(OfficialStreamsEndpointError) as exc_info:
        _resolve({"hls_aac_160_url": RAW_HLS_URL}, status_code=500)

    assert RAW_HLS_URL not in str(exc_info.value)
    assert "SHOULD_NOT_LEAK" not in str(exc_info.value)


class StreamsTransport:
    def __init__(self, payload: dict[str, object], *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.paths: list[str] = []
        self.authorizations: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.raw_path.decode())
        self.authorizations.append(request.headers.get("authorization"))
        return httpx.Response(self.status_code, json=self.payload, request=request)


def _resolve(
    payload: dict[str, object],
    *,
    track_urn: str = "123",
    status_code: int = 200,
) -> tuple[object, StreamsTransport]:
    transport = StreamsTransport(payload, status_code=status_code)
    client = SafeAsyncHttpClient(
        settings=AppSettings(allow_network=True),
        transport=httpx.MockTransport(transport),
    )

    async def run_resolve():
        async with client:
            return await OfficialStreamsClient(
                settings=AppSettings(
                    allow_network=True,
                    soundcloud_api_base_url="https://api.soundcloud.test",
                ),
                http_client=client,
            ).resolve_hls_stream(
                track_urn=track_urn,
                access_token=SoundCloudAccessToken(value=SecretStr("raw-access-token")),
            )

    return asyncio.run(run_resolve()), transport
