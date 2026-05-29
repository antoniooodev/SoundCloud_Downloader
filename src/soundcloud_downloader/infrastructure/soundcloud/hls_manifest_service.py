from soundcloud_downloader.domain import (
    ErrorCode,
    SoundcloudDownloaderError,
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamKind,
)
from soundcloud_downloader.infrastructure.http import (
    HttpMethod,
    HttpRequest,
    HttpRequestError,
    SafeAsyncHttpClient,
)
from soundcloud_downloader.infrastructure.observability import REDACTED_VALUE

_HLS_ACCEPT_HEADER = (
    "application/vnd.apple.mpegurl, application/x-mpegURL, text/plain;q=0.9, */*;q=0.1"
)


class SoundCloudHLSManifestRetrievalError(SoundcloudDownloaderError):
    pass


class SoundCloudHLSManifestService:
    def __init__(
        self,
        *,
        http_client: SafeAsyncHttpClient,
    ) -> None:
        self._http_client = http_client

    async def fetch_manifest(
        self,
        *,
        stream: SoundCloudResolvedStream,
    ) -> str:
        if stream.kind is not SoundCloudResolvedStreamKind.HLS_MANIFEST:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                "SoundCloud stream is not an HLS manifest source.",
            )

        request = HttpRequest(
            method=HttpMethod.GET,
            url=stream.url.get_secret_value(),
            headers={"accept": _HLS_ACCEPT_HEADER},
        )
        try:
            response = await self._http_client.request(request)
        except HttpRequestError as exc:
            raise SoundCloudHLSManifestRetrievalError(
                exc.code,
                "SoundCloud HLS manifest request failed.",
            ) from exc

        if 200 <= response.status_code <= 299:
            return self._validated_manifest_text(response.text)

        if response.status_code in {400, 401, 403}:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.AUTH_REQUIRED,
                "SoundCloud HLS manifest authorization failed.",
            )
        if response.status_code == 404:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                "SoundCloud HLS manifest was not found.",
            )
        if response.status_code == 429:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.NETWORK_RETRYABLE,
                "SoundCloud HLS manifest request was rate limited.",
            )
        if 500 <= response.status_code <= 599:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.NETWORK_RETRYABLE,
                "SoundCloud HLS manifest endpoint returned a server error.",
            )
        raise SoundCloudHLSManifestRetrievalError(
            ErrorCode.UNKNOWN_UNSAFE,
            "SoundCloud HLS manifest endpoint returned an unsupported response.",
        )

    def _validated_manifest_text(self, response_text: str) -> str:
        if response_text.strip() == "":
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "SoundCloud HLS manifest response was empty.",
            )
        if "#EXTM3U" not in response_text:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "SoundCloud HLS manifest response was not a valid HLS manifest.",
            )
        return response_text


def redact_hls_manifest_request(request: HttpRequest) -> dict[str, object]:
    return {
        "method": request.method.value,
        "url": REDACTED_VALUE,
        "headers": {
            "accept": request.headers.get("accept", _HLS_ACCEPT_HEADER),
        },
    }
