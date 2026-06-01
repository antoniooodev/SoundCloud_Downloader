from enum import Enum
from urllib.parse import urlsplit

from soundcloud_downloader.domain import (
    ErrorCode,
    SoundcloudDownloaderError,
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamKind,
)
from soundcloud_downloader.infrastructure.http import (
    HttpRequestFailureKind,
    HttpMethod,
    HttpRequest,
    HttpRequestError,
    NetworkDisabledError,
    SafeAsyncHttpClient,
)
from soundcloud_downloader.infrastructure.observability import REDACTED_VALUE

_HLS_ACCEPT_HEADER = (
    "application/vnd.apple.mpegurl, application/x-mpegURL, audio/mpegurl, */*"
)
_ALLOWED_HLS_MEDIA_HOSTS = (
    "playback.media-streaming.soundcloud.cloud",
    "cf-media.sndcdn.com",
    "cf-hls-media.sndcdn.com",
)


class HLSManifestFetchFailureKind(str, Enum):
    SAFE_CLIENT_POLICY_DENIED = "safe_client_policy_denied"
    HOST_NOT_ALLOWED = "host_not_allowed"
    REDIRECT_REJECTED = "redirect_rejected"
    HTTP_STATUS = "http_status"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


class SoundCloudHLSManifestRetrievalError(SoundcloudDownloaderError):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        hls_analysis_reason: str = "hls_manifest_fetch_failed",
        manifest_request_status: int | None = None,
        failure_kind: HLSManifestFetchFailureKind = HLSManifestFetchFailureKind.UNKNOWN,
        redirect_count: int | None = None,
        allowed_host: bool | None = None,
    ) -> None:
        self.hls_analysis_reason = hls_analysis_reason
        self.manifest_request_status = manifest_request_status
        self.failure_kind = failure_kind
        self.redirect_count = redirect_count
        self.allowed_host = allowed_host
        super().__init__(code, message)


class SoundCloudHLSManifestService:
    def __init__(
        self,
        *,
        http_client: SafeAsyncHttpClient,
        allowed_media_hosts: tuple[str, ...] = _ALLOWED_HLS_MEDIA_HOSTS,
    ) -> None:
        self._http_client = http_client
        self._allowed_media_hosts = allowed_media_hosts

    async def fetch_manifest(
        self,
        *,
        stream: SoundCloudResolvedStream,
    ) -> str:
        if stream.kind is not SoundCloudResolvedStreamKind.HLS_MANIFEST:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                "SoundCloud stream is not an HLS manifest source.",
                failure_kind=HLSManifestFetchFailureKind.SAFE_CLIENT_POLICY_DENIED,
            )

        if not _host_is_allowed(stream.url.get_secret_value(), self._allowed_media_hosts):
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                "SoundCloud HLS manifest host is not allowed.",
                failure_kind=HLSManifestFetchFailureKind.HOST_NOT_ALLOWED,
                allowed_host=False,
            )

        request = HttpRequest(
            method=HttpMethod.GET,
            url=stream.url.get_secret_value(),
            headers={"accept": _HLS_ACCEPT_HEADER},
            follow_redirects=True,
            max_redirects=3,
            redirect_allowed_hosts=self._allowed_media_hosts,
            allow_sensitive_redirect_query=True,
        )
        try:
            response = await self._http_client.request(request)
        except NetworkDisabledError as exc:
            raise SoundCloudHLSManifestRetrievalError(
                exc.code,
                "SoundCloud HLS manifest request was denied by safe client policy.",
                failure_kind=HLSManifestFetchFailureKind.SAFE_CLIENT_POLICY_DENIED,
            ) from exc
        except HttpRequestError as exc:
            raise SoundCloudHLSManifestRetrievalError(
                exc.code,
                "SoundCloud HLS manifest request failed.",
                manifest_request_status=exc.status_code,
                failure_kind=_failure_kind_from_http_error(exc),
                redirect_count=exc.redirect_count,
                allowed_host=exc.allowed_host,
            ) from exc

        if 200 <= response.status_code <= 299:
            return self._validated_manifest_text(response.text)

        if response.status_code in {400, 401, 403}:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.AUTH_REQUIRED,
                "SoundCloud HLS manifest authorization failed.",
                manifest_request_status=response.status_code,
                failure_kind=HLSManifestFetchFailureKind.HTTP_STATUS,
            )
        if response.status_code == 404:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                "SoundCloud HLS manifest was not found.",
                manifest_request_status=response.status_code,
                failure_kind=HLSManifestFetchFailureKind.HTTP_STATUS,
            )
        if response.status_code == 429:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.NETWORK_RETRYABLE,
                "SoundCloud HLS manifest request was rate limited.",
                manifest_request_status=response.status_code,
                failure_kind=HLSManifestFetchFailureKind.HTTP_STATUS,
            )
        if 500 <= response.status_code <= 599:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.NETWORK_RETRYABLE,
                "SoundCloud HLS manifest endpoint returned a server error.",
                manifest_request_status=response.status_code,
                failure_kind=HLSManifestFetchFailureKind.HTTP_STATUS,
            )
        raise SoundCloudHLSManifestRetrievalError(
            ErrorCode.UNKNOWN_UNSAFE,
            "SoundCloud HLS manifest endpoint returned an unsupported response.",
            manifest_request_status=response.status_code,
            failure_kind=HLSManifestFetchFailureKind.HTTP_STATUS,
        )

    def _validated_manifest_text(self, response_text: str) -> str:
        if response_text.strip() == "":
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "SoundCloud HLS manifest response was empty.",
                hls_analysis_reason="hls_manifest_parse_failed",
                failure_kind=HLSManifestFetchFailureKind.INVALID_RESPONSE,
            )
        if "#EXTM3U" not in response_text:
            raise SoundCloudHLSManifestRetrievalError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "SoundCloud HLS manifest response was not a valid HLS manifest.",
                hls_analysis_reason="hls_manifest_parse_failed",
                failure_kind=HLSManifestFetchFailureKind.INVALID_RESPONSE,
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


def _failure_kind_from_http_error(exc: HttpRequestError) -> HLSManifestFetchFailureKind:
    if exc.failure_kind is HttpRequestFailureKind.HOST_NOT_ALLOWED:
        return HLSManifestFetchFailureKind.HOST_NOT_ALLOWED
    if exc.failure_kind is HttpRequestFailureKind.REDIRECT_REJECTED:
        return HLSManifestFetchFailureKind.REDIRECT_REJECTED
    if exc.failure_kind is HttpRequestFailureKind.TIMEOUT:
        return HLSManifestFetchFailureKind.TIMEOUT
    if exc.failure_kind is HttpRequestFailureKind.NETWORK_ERROR:
        return HLSManifestFetchFailureKind.NETWORK_ERROR
    return HLSManifestFetchFailureKind.UNKNOWN


def _host_is_allowed(raw_url: str, allowed_hosts: tuple[str, ...]) -> bool:
    try:
        hostname = urlsplit(raw_url).hostname
    except ValueError:
        return False
    if hostname is None:
        return False
    normalized = hostname.lower().rstrip(".")
    return any(normalized == allowed.lower().rstrip(".") for allowed in allowed_hosts)
