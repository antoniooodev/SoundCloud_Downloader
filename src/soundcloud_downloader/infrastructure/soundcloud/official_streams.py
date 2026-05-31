import json
from urllib.parse import quote

from pydantic import SecretStr, ValidationError

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ErrorCode,
    SoundcloudDownloaderError,
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
from soundcloud_downloader.infrastructure.soundcloud.api_contract import SoundCloudAccessToken


class OfficialStreamsEndpointError(SoundcloudDownloaderError):
    pass


class NoOfficialHLSStreamsError(SoundcloudDownloaderError):
    pass


class OfficialStreamsClient:
    def __init__(self, *, settings: AppSettings, http_client: SafeAsyncHttpClient) -> None:
        self._settings = settings
        self._http_client = http_client

    async def resolve_hls_stream(
        self,
        *,
        track_urn: str,
        access_token: SoundCloudAccessToken,
    ) -> SoundCloudResolvedStream:
        response = await self._http_client.request(
            HttpRequest(
                method=HttpMethod.GET,
                url=(
                    f"{self._settings.soundcloud_api_base_url}/tracks/"
                    f"{quote(track_urn, safe='')}/streams"
                ),
                headers={
                    "Authorization": f"Bearer {access_token.value.get_secret_value()}",
                    "accept": "application/json; charset=utf-8",
                },
            )
        )
        if response.status_code in {400, 401, 403}:
            raise OfficialStreamsEndpointError(
                ErrorCode.AUTH_REQUIRED,
                "SoundCloud streams endpoint authorization failed.",
            )
        if response.status_code == 404:
            raise OfficialStreamsEndpointError(
                ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                "SoundCloud streams endpoint was not found.",
            )
        if response.status_code == 429:
            raise OfficialStreamsEndpointError(
                ErrorCode.NETWORK_RETRYABLE,
                "SoundCloud streams endpoint was rate limited.",
            )
        if 500 <= response.status_code <= 599:
            raise OfficialStreamsEndpointError(
                ErrorCode.NETWORK_RETRYABLE,
                "SoundCloud streams endpoint returned a server error.",
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise OfficialStreamsEndpointError(
                ErrorCode.UNKNOWN_UNSAFE,
                "SoundCloud streams endpoint returned an unsupported response.",
            )

        payload = self._json_payload(response.text)
        selection = _select_official_hls_stream(payload)
        if selection is None:
            raise NoOfficialHLSStreamsError(
                ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                "No safe HLS streams are available.",
            )
        field_name, raw_url = selection
        try:
            return _stream_from_field(field_name, raw_url)
        except (TypeError, ValueError, ValidationError) as exc:
            raise OfficialStreamsEndpointError(
                ErrorCode.UNKNOWN_UNSAFE,
                "SoundCloud streams endpoint response was malformed.",
            ) from exc

    def _json_payload(self, response_text: str) -> dict[str, object]:
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise OfficialStreamsEndpointError(
                ErrorCode.UNKNOWN_UNSAFE,
                "SoundCloud streams endpoint returned invalid JSON.",
            ) from exc
        if not isinstance(payload, dict):
            raise OfficialStreamsEndpointError(
                ErrorCode.UNKNOWN_UNSAFE,
                "SoundCloud streams endpoint returned a non-object JSON payload.",
            )
        return payload


def _select_official_hls_stream(payload: dict[str, object]) -> tuple[str, str] | None:
    for field_name in (
        "hls_aac_160_url",
        "hls_aac_96_url",
        "hls_mp3_128_url",
        "hls_opus_64_url",
    ):
        value = payload.get(field_name)
        if isinstance(value, str) and value:
            return field_name, value
    return None


def _stream_from_field(field_name: str, raw_url: str) -> SoundCloudResolvedStream:
    return SoundCloudResolvedStream(
        kind=SoundCloudResolvedStreamKind.HLS_MANIFEST,
        url=SoundCloudResolvedStreamUrl(value=SecretStr(raw_url)),
        protocol=SoundCloudTranscodingProtocol.HLS,
        mime_type=_mime_type_for_field(field_name),
        preset=field_name.removesuffix("_url"),
        quality="sq",
        snipped=False,
    )


def _mime_type_for_field(field_name: str) -> SoundCloudTranscodingMimeType:
    if field_name.startswith("hls_aac_"):
        return SoundCloudTranscodingMimeType.AUDIO_MP4
    if field_name.startswith("hls_mp3_"):
        return SoundCloudTranscodingMimeType.AUDIO_MPEG
    if field_name.startswith("hls_opus_"):
        return SoundCloudTranscodingMimeType.AUDIO_MP4
    return SoundCloudTranscodingMimeType.UNKNOWN
