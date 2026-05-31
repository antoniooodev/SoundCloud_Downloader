import json
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pydantic import SecretStr, ValidationError

from soundcloud_downloader.application.transcoding_endpoint import (
    SoundCloudTranscodingEndpointRequestBuilder,
)
from soundcloud_downloader.domain import (
    ErrorCode,
    SoundcloudDownloaderError,
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamUrl,
    SoundCloudTranscodingMetadata,
)
from soundcloud_downloader.infrastructure.http import SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.observability import REDACTED_VALUE
from soundcloud_downloader.infrastructure.soundcloud.api_contract import SoundCloudAccessToken


class SoundCloudTranscodingEndpointError(SoundcloudDownloaderError):
    pass


class SoundCloudTranscodingEndpointService:
    def __init__(
        self,
        *,
        http_client: SafeAsyncHttpClient,
        request_builder: SoundCloudTranscodingEndpointRequestBuilder | None = None,
    ) -> None:
        self._http_client = http_client
        self._request_builder = request_builder or SoundCloudTranscodingEndpointRequestBuilder()

    async def resolve_stream_url(
        self,
        *,
        transcoding: SoundCloudTranscodingMetadata,
        access_token: SoundCloudAccessToken,
    ) -> SoundCloudResolvedStream:
        request = self._request_builder.build_request(
            transcoding=transcoding,
            access_token=access_token,
        )
        response = await self._http_client.request(request)

        if response.status_code in {400, 401, 403}:
            raise SoundCloudTranscodingEndpointError(
                ErrorCode.AUTH_REQUIRED,
                "SoundCloud transcoding endpoint authorization failed.",
            )
        if response.status_code == 404:
            raise SoundCloudTranscodingEndpointError(
                ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                "SoundCloud transcoding endpoint was not found.",
            )
        if response.status_code == 429:
            raise SoundCloudTranscodingEndpointError(
                ErrorCode.NETWORK_RETRYABLE,
                "SoundCloud transcoding endpoint was rate limited.",
            )
        if 500 <= response.status_code <= 599:
            raise SoundCloudTranscodingEndpointError(
                ErrorCode.NETWORK_RETRYABLE,
                "SoundCloud transcoding endpoint returned a server error.",
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise SoundCloudTranscodingEndpointError(
                ErrorCode.UNKNOWN_UNSAFE,
                "SoundCloud transcoding endpoint returned an unsupported response.",
            )

        payload = self._json_payload(response.text)
        raw_url = payload.get("url")
        if not isinstance(raw_url, str) or raw_url == "":
            raise SoundCloudTranscodingEndpointError(
                ErrorCode.UNKNOWN_UNSAFE,
                "SoundCloud transcoding endpoint response was malformed.",
            )
        if _has_sensitive_url_material(raw_url):
            raise SoundCloudTranscodingEndpointError(
                ErrorCode.UNKNOWN_UNSAFE,
                "SoundCloud transcoding endpoint response was malformed.",
            )

        try:
            stream_url = SoundCloudResolvedStreamUrl(value=SecretStr(raw_url))
            return SoundCloudResolvedStream.from_transcoding(
                transcoding=transcoding,
                url=stream_url,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise SoundCloudTranscodingEndpointError(
                ErrorCode.UNKNOWN_UNSAFE,
                "SoundCloud transcoding endpoint response was malformed.",
            ) from exc

    def _json_payload(self, response_text: str) -> dict[str, Any]:
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise SoundCloudTranscodingEndpointError(
                ErrorCode.UNKNOWN_UNSAFE,
                "SoundCloud transcoding endpoint returned invalid JSON.",
            ) from exc
        if not isinstance(payload, dict):
            raise SoundCloudTranscodingEndpointError(
                ErrorCode.UNKNOWN_UNSAFE,
                "SoundCloud transcoding endpoint returned a non-object JSON payload.",
            )
        return payload


def redact_resolved_stream(stream: SoundCloudResolvedStream) -> dict[str, object]:
    return {
        "kind": stream.kind.value,
        "protocol": stream.protocol.value,
        "mime_type": stream.mime_type.value,
        "preset": stream.preset,
        "quality": stream.quality,
        "snipped": stream.snipped,
        "url": REDACTED_VALUE,
    }


_FORBIDDEN_STREAM_URL_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "cookie",
        "refresh_token",
        "set-cookie",
    }
)


def _has_sensitive_url_material(raw_url: str) -> bool:
    lowered_url = raw_url.lower()
    if any(forbidden_key in lowered_url for forbidden_key in _FORBIDDEN_STREAM_URL_KEYS):
        return True
    query_keys = {
        key.lower() for key, _value in parse_qsl(urlsplit(raw_url).query, keep_blank_values=True)
    }
    return bool(query_keys & _FORBIDDEN_STREAM_URL_KEYS)
