from soundcloud_downloader.application.artifact_storage import ArtifactStoragePort
from soundcloud_downloader.domain import (
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
    ErrorCode,
    HLSByteRange,
    HLSSegmentPlan,
    HLSSegmentStagingResult,
    SoundcloudDownloaderError,
    StagedHLSSegment,
)
from soundcloud_downloader.infrastructure.http import (
    HttpMethod,
    HttpRequest,
    HttpRequestError,
    SafeAsyncHttpClient,
)

_DEFAULT_STORAGE_PREFIX = ArtifactRelativePath(value="hls/staged")
_REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_HEADER_NAMES = frozenset({"authorization", "cookie", "set-cookie"})


class HLSSegmentFetchError(SoundcloudDownloaderError):
    pass


class HLSSegmentFetcher:
    def __init__(
        self,
        *,
        http_client: SafeAsyncHttpClient,
        storage: ArtifactStoragePort,
        storage_prefix: ArtifactRelativePath | None = None,
    ) -> None:
        self._http_client = http_client
        self._storage = storage
        self._storage_prefix = storage_prefix or _DEFAULT_STORAGE_PREFIX

    async def stage_segments(
        self,
        *,
        plan: HLSSegmentPlan,
    ) -> HLSSegmentStagingResult:
        if not plan.segments:
            raise HLSSegmentFetchError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "HLS segment plan does not contain any segments.",
            )

        staged_segments: list[StagedHLSSegment] = []
        total_bytes = 0
        for segment in plan.segments:
            request = HttpRequest(
                method=HttpMethod.GET,
                url=segment.url.get_secret_value(),
                headers=self._headers_for_byte_range(segment.byte_range),
            )
            try:
                response = await self._http_client.request(request)
            except HttpRequestError as exc:
                raise HLSSegmentFetchError(
                    exc.code,
                    "HLS segment request failed.",
                ) from exc

            self._raise_for_status(response.status_code)
            if response.content == b"":
                raise HLSSegmentFetchError(
                    ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                    "HLS segment response was empty.",
                )

            relative_path = self._relative_path_for_index(segment.index)
            try:
                artifact = self._storage.write_bytes(
                    relative_path=relative_path,
                    data=response.content,
                )
            except Exception as exc:
                raise HLSSegmentFetchError(
                    ErrorCode.STORAGE_FAILED,
                    "HLS segment could not be staged.",
                ) from exc

            hls_artifact = self._as_hls_segment_artifact(artifact)
            staged_segments.append(
                StagedHLSSegment(
                    index=segment.index,
                    artifact=hls_artifact,
                    duration_seconds=segment.duration_seconds,
                    source_byte_range=segment.byte_range,
                )
            )
            total_bytes += len(response.content)

        return HLSSegmentStagingResult(
            manifest_url=plan.manifest_url,
            segments=tuple(staged_segments),
            total_bytes=total_bytes,
            complete=True,
        )

    def _headers_for_byte_range(self, byte_range: HLSByteRange | None) -> dict[str, str]:
        headers = {"accept": "*/*"}
        if byte_range is None:
            return headers
        start = 0 if byte_range.offset is None else byte_range.offset
        end = start + byte_range.length - 1
        headers["range"] = f"bytes={start}-{end}"
        return headers

    def _raise_for_status(self, status_code: int) -> None:
        if 200 <= status_code <= 299:
            return
        if status_code in {400, 401, 403}:
            raise HLSSegmentFetchError(
                ErrorCode.AUTH_REQUIRED,
                "HLS segment authorization failed.",
            )
        if status_code == 404:
            raise HLSSegmentFetchError(
                ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                "HLS segment was not found.",
            )
        if status_code == 429:
            raise HLSSegmentFetchError(
                ErrorCode.NETWORK_RETRYABLE,
                "HLS segment request was rate limited.",
            )
        if 500 <= status_code <= 599:
            raise HLSSegmentFetchError(
                ErrorCode.NETWORK_RETRYABLE,
                "HLS segment endpoint returned a server error.",
            )
        raise HLSSegmentFetchError(
            ErrorCode.UNKNOWN_UNSAFE,
            "HLS segment endpoint returned an unsupported response.",
        )

    def _relative_path_for_index(self, index: int) -> ArtifactRelativePath:
        return ArtifactRelativePath(value=f"{self._storage_prefix.value}/segments/{index:06d}.bin")

    def _as_hls_segment_artifact(self, artifact: ArtifactMetadata) -> ArtifactMetadata:
        return artifact.model_copy(update={"kind": ArtifactKind.HLS_SEGMENT})


def redact_hls_segment_request(request: HttpRequest) -> dict[str, object]:
    redacted_headers: dict[str, str] = {}
    for name, value in request.headers.items():
        lowered_name = name.lower()
        if lowered_name in _SENSITIVE_HEADER_NAMES:
            redacted_headers[lowered_name] = _REDACTED_VALUE
        elif lowered_name in {"accept", "range"}:
            redacted_headers[lowered_name] = value
    return {
        "method": request.method.value,
        "url": _REDACTED_VALUE,
        "headers": redacted_headers,
    }
