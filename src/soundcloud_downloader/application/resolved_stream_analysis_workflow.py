from enum import Enum
from urllib.parse import urljoin
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer

from soundcloud_downloader.application.reconstruction_planner import (
    ReconstructionPlan,
    ReconstructionPlanner,
    ReconstructionPlanRequest,
)
from soundcloud_downloader.application.stream_analysis_service import (
    StreamAnalysisRequest,
    StreamAnalysisService,
)
from soundcloud_downloader.domain import (
    AccessMode,
    DRMStatus,
    ErrorCode,
    HLSManifestAnalysis,
    HLSManifestKind,
    MediaCodec,
    MediaContainer,
    OutputProfile,
    SoundcloudDownloaderError,
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamKind,
    SoundCloudResolvedStreamUrl,
    SoundCloudTranscodingMimeType,
    SourceProtocol,
)


@runtime_checkable
class HLSManifestFetcherPort(Protocol):
    async def fetch_manifest(
        self,
        *,
        stream: SoundCloudResolvedStream,
    ) -> str: ...


class HLSAnalysisFailureReason(str, Enum):
    HLS_MANIFEST_FETCH_FAILED = "hls_manifest_fetch_failed"
    HLS_MANIFEST_REDIRECT_REJECTED = "hls_manifest_redirect_rejected"
    HLS_MANIFEST_PARSE_FAILED = "hls_manifest_parse_failed"
    HLS_MASTER_PLAYLIST_UNSUPPORTED = "hls_master_playlist_unsupported"
    HLS_NO_VARIANTS = "hls_no_variants"
    HLS_NO_SEGMENTS = "hls_no_segments"
    HLS_ENCRYPTED_STREAM_UNSUPPORTED = "hls_encrypted_stream_unsupported"
    HLS_UNSUPPORTED_MEDIA_PLAYLIST = "hls_unsupported_media_playlist"
    HLS_FMP4_UNSUPPORTED = "hls_fmp4_unsupported"


class HLSAnalysisError(SoundcloudDownloaderError):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        reason: HLSAnalysisFailureReason,
        manifest_request_status: int | None = None,
        manifest_kind: str | None = None,
        variant_count: int | None = None,
        segment_count: int | None = None,
        uses_init_map: bool | None = None,
        uses_byterange: bool | None = None,
        encrypted: bool | None = None,
    ) -> None:
        self.reason = reason
        self.manifest_request_status = manifest_request_status
        self.manifest_kind = manifest_kind
        self.variant_count = variant_count
        self.segment_count = segment_count
        self.uses_init_map = uses_init_map
        self.uses_byterange = uses_byterange
        self.encrypted = encrypted
        super().__init__(code, message)


class ResolvedStreamAnalysisRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream: SoundCloudResolvedStream
    access_mode: AccessMode
    output_profile: OutputProfile


class ResolvedStreamAnalysisResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream: SoundCloudResolvedStream
    manifest_analysis: HLSManifestAnalysis | None = None
    plan: ReconstructionPlan
    manifest_text: SecretStr | None = Field(default=None, repr=False)

    @field_serializer("manifest_text", when_used="always")
    def serialize_manifest_text(self, value: SecretStr | None) -> str | None:
        return None if value is None else str(value)


class ResolvedStreamAnalysisWorkflow:
    def __init__(
        self,
        *,
        manifest_fetcher: HLSManifestFetcherPort,
        stream_analysis_service: StreamAnalysisService | None = None,
        reconstruction_planner: ReconstructionPlanner | None = None,
    ) -> None:
        self._manifest_fetcher = manifest_fetcher
        self._stream_analysis_service = stream_analysis_service or StreamAnalysisService()
        self._reconstruction_planner = reconstruction_planner or ReconstructionPlanner(
            stream_analysis_service=self._stream_analysis_service
        )

    async def analyze(
        self,
        request: ResolvedStreamAnalysisRequest,
    ) -> ResolvedStreamAnalysisResult:
        manifest_text: str | None = None
        stream = request.stream
        if request.stream.kind is SoundCloudResolvedStreamKind.HLS_MANIFEST:
            manifest_text = await self._fetch_manifest(stream)
            manifest_text, stream = await self._select_media_playlist(
                manifest_text=manifest_text,
                stream=stream,
            )

        stream_request = _stream_analysis_request(
            stream,
            manifest_text=manifest_text,
        )
        try:
            plan = self._reconstruction_planner.plan(
                ReconstructionPlanRequest(
                    access_mode=request.access_mode,
                    requested_profile=request.output_profile,
                    is_authenticated=request.access_mode is AccessMode.GO_PLUS,
                    has_go_plus=request.access_mode is AccessMode.GO_PLUS,
                    is_public=request.access_mode is AccessMode.PUBLIC,
                    stream=stream_request,
                )
            )
        except Exception as exc:
            raise HLSAnalysisError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "HLS manifest analysis failed.",
                reason=HLSAnalysisFailureReason.HLS_MANIFEST_PARSE_FAILED,
            ) from exc
        self._validate_hls_analysis(plan.hls_analysis, manifest_text, allow_master=False)
        return ResolvedStreamAnalysisResult(
            stream=stream,
            manifest_analysis=plan.hls_analysis,
            plan=plan,
            manifest_text=None if manifest_text is None else SecretStr(manifest_text),
        )

    async def _fetch_manifest(self, stream: SoundCloudResolvedStream) -> str:
        try:
            return await self._manifest_fetcher.fetch_manifest(stream=stream)
        except HLSAnalysisError:
            raise
        except Exception as exc:
            raise HLSAnalysisError(
                getattr(exc, "code", ErrorCode.NETWORK_PERMANENT),
                "HLS manifest fetch failed.",
                reason=_hls_reason_from_exception(exc),
                manifest_request_status=getattr(exc, "manifest_request_status", None),
            ) from exc

    async def _select_media_playlist(
        self,
        *,
        manifest_text: str,
        stream: SoundCloudResolvedStream,
    ) -> tuple[str, SoundCloudResolvedStream]:
        analysis = self._stream_analysis_service.analyze(
            _stream_analysis_request(stream, manifest_text=manifest_text)
        ).hls_analysis
        self._validate_hls_analysis(analysis, manifest_text, allow_master=True)
        if analysis is None or analysis.kind is not HLSManifestKind.MASTER:
            return manifest_text, stream

        variants = _master_playlist_variants(manifest_text)
        if not variants:
            raise HLSAnalysisError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "HLS master playlist contained no variants.",
                reason=HLSAnalysisFailureReason.HLS_NO_VARIANTS,
                manifest_kind=HLSManifestKind.MASTER.value,
                variant_count=0,
            )

        selected_variant = sorted(
            variants,
            key=lambda variant: (variant.bandwidth or 0, variant.index),
            reverse=True,
        )[0]
        try:
            selected_stream = _stream_with_uri(stream, selected_variant.uri)
        except ValueError as exc:
            raise HLSAnalysisError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "HLS master playlist contained an unsafe variant URL.",
                reason=HLSAnalysisFailureReason.HLS_MANIFEST_PARSE_FAILED,
                manifest_kind=HLSManifestKind.MASTER.value,
                variant_count=len(variants),
            ) from exc
        return await self._fetch_manifest(selected_stream), selected_stream

    def _validate_hls_analysis(
        self,
        analysis: HLSManifestAnalysis | None,
        manifest_text: str | None,
        *,
        allow_master: bool,
    ) -> None:
        if manifest_text is None or analysis is None:
            return
        if (
            analysis.is_hls
            and analysis.kind is HLSManifestKind.UNKNOWN
            and _looks_like_empty_media_playlist(manifest_text)
        ):
            raise HLSAnalysisError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "HLS media playlist contained no segments.",
                reason=HLSAnalysisFailureReason.HLS_NO_SEGMENTS,
                manifest_kind=HLSManifestKind.MEDIA.value,
                segment_count=0,
                uses_init_map=_uses_init_map(manifest_text),
                uses_byterange=_uses_byterange(manifest_text),
                encrypted=False,
            )
        if not analysis.is_hls or analysis.kind is HLSManifestKind.UNKNOWN:
            raise HLSAnalysisError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "HLS manifest could not be parsed safely.",
                reason=HLSAnalysisFailureReason.HLS_MANIFEST_PARSE_FAILED,
                manifest_kind=None if analysis is None else analysis.kind.value,
            )
        if analysis.kind is HLSManifestKind.MEDIA and _uses_init_map(manifest_text):
            raise HLSAnalysisError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "HLS fMP4 init-map playlists are unsupported.",
                reason=HLSAnalysisFailureReason.HLS_FMP4_UNSUPPORTED,
                manifest_kind=analysis.kind.value,
                segment_count=analysis.segment_count,
                uses_init_map=True,
                uses_byterange=_uses_byterange(manifest_text),
                encrypted=False,
            )
        if analysis.kind is HLSManifestKind.MASTER and not allow_master:
            raise HLSAnalysisError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "Nested HLS master playlists are unsupported.",
                reason=HLSAnalysisFailureReason.HLS_MASTER_PLAYLIST_UNSUPPORTED,
                manifest_kind=analysis.kind.value,
                segment_count=0,
                encrypted=False,
            )
        if analysis.is_encrypted or analysis.drm_status is not DRMStatus.NONE:
            raise HLSAnalysisError(
                ErrorCode.ENCRYPTED_STREAM_UNSUPPORTED,
                "Encrypted HLS streams are unsupported.",
                reason=HLSAnalysisFailureReason.HLS_ENCRYPTED_STREAM_UNSUPPORTED,
                manifest_kind=analysis.kind.value,
                segment_count=analysis.segment_count,
                uses_init_map=_uses_init_map(manifest_text),
                uses_byterange=_uses_byterange(manifest_text),
                encrypted=True,
            )
        if analysis.kind is HLSManifestKind.MEDIA and analysis.segment_count == 0:
            raise HLSAnalysisError(
                ErrorCode.MANIFEST_UNSUPPORTED,
                "HLS media playlist contained no segments.",
                reason=HLSAnalysisFailureReason.HLS_NO_SEGMENTS,
                manifest_kind=analysis.kind.value,
                segment_count=0,
                uses_init_map=_uses_init_map(manifest_text),
                uses_byterange=_uses_byterange(manifest_text),
                encrypted=False,
            )


def _stream_analysis_request(
    stream: SoundCloudResolvedStream,
    *,
    manifest_text: str | None,
) -> StreamAnalysisRequest:
    protocol = _source_protocol(stream.kind)
    codec = _media_codec(stream.mime_type, stream.preset)
    return StreamAnalysisRequest(
        protocol=protocol,
        mime_type=stream.mime_type.value,
        codec=codec,
        container=_media_container(stream.mime_type, codec),
        declared_drm_status=_declared_drm_status(stream.kind),
        manifest_text=manifest_text,
    )


def _source_protocol(stream_kind: SoundCloudResolvedStreamKind) -> SourceProtocol:
    if stream_kind is SoundCloudResolvedStreamKind.HLS_MANIFEST:
        return SourceProtocol.HLS
    if stream_kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA:
        return SourceProtocol.PROGRESSIVE
    return SourceProtocol.UNKNOWN


def _declared_drm_status(stream_kind: SoundCloudResolvedStreamKind) -> DRMStatus:
    if stream_kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA:
        return DRMStatus.NONE
    return DRMStatus.UNKNOWN


def _media_codec(
    mime_type: SoundCloudTranscodingMimeType,
    preset: str | None,
) -> MediaCodec:
    preset_value = (preset or "").lower()
    if mime_type in {
        SoundCloudTranscodingMimeType.AUDIO_AAC,
        SoundCloudTranscodingMimeType.AUDIO_MP4,
    }:
        return MediaCodec.AAC
    if "aac" in preset_value:
        return MediaCodec.AAC
    if mime_type is SoundCloudTranscodingMimeType.AUDIO_MPEG or "mp3" in preset_value:
        return MediaCodec.MP3
    return MediaCodec.UNKNOWN


def _media_container(
    mime_type: SoundCloudTranscodingMimeType,
    codec: MediaCodec,
) -> MediaContainer:
    if mime_type is SoundCloudTranscodingMimeType.AUDIO_MPEG:
        return MediaContainer.MP3
    if mime_type in {
        SoundCloudTranscodingMimeType.AUDIO_AAC,
        SoundCloudTranscodingMimeType.AUDIO_MP4,
    }:
        return MediaContainer.M4A
    if codec is MediaCodec.AAC:
        return MediaContainer.M4A
    if codec is MediaCodec.MP3:
        return MediaContainer.MP3
    return MediaContainer.UNKNOWN


def _hls_reason_from_exception(exc: Exception) -> HLSAnalysisFailureReason:
    raw_reason = getattr(exc, "hls_analysis_reason", None)
    try:
        return HLSAnalysisFailureReason(raw_reason)
    except (TypeError, ValueError):
        return HLSAnalysisFailureReason.HLS_MANIFEST_FETCH_FAILED


class _HLSVariant(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    uri: str
    bandwidth: int | None = None


def _master_playlist_variants(manifest_text: str) -> tuple[_HLSVariant, ...]:
    lines = tuple(line.strip() for line in manifest_text.splitlines() if line.strip())
    variants: list[_HLSVariant] = []
    pending_bandwidth: int | None = None
    pending_stream_inf = False
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF"):
            pending_stream_inf = True
            pending_bandwidth = _bandwidth(line)
            continue
        if line.startswith("#"):
            continue
        if not pending_stream_inf:
            continue
        variants.append(
            _HLSVariant(
                index=len(variants),
                uri=line,
                bandwidth=pending_bandwidth,
            )
        )
        pending_stream_inf = False
        pending_bandwidth = None
    return tuple(variants)


def _bandwidth(stream_inf_line: str) -> int | None:
    if ":" not in stream_inf_line:
        return None
    for attribute in stream_inf_line.split(":", 1)[1].split(","):
        key, separator, value = attribute.partition("=")
        if separator and key.strip().upper() == "BANDWIDTH":
            try:
                parsed = int(value.strip())
            except ValueError:
                return None
            return parsed if parsed > 0 else None
    return None


def _stream_with_uri(
    stream: SoundCloudResolvedStream,
    uri: str,
) -> SoundCloudResolvedStream:
    return stream.model_copy(update={"url": _resolve_url(stream.url, uri)})


def _resolve_url(
    parent_url: SoundCloudResolvedStreamUrl,
    child_uri: str,
) -> SoundCloudResolvedStreamUrl:
    return SoundCloudResolvedStreamUrl(
        value=SecretStr(urljoin(parent_url.get_secret_value(), child_uri))
    )


def _uses_init_map(manifest_text: str) -> bool:
    return any(line.strip().startswith("#EXT-X-MAP") for line in manifest_text.splitlines())


def _uses_byterange(manifest_text: str) -> bool:
    return any(line.strip().startswith("#EXT-X-BYTERANGE") for line in manifest_text.splitlines())


def _looks_like_empty_media_playlist(manifest_text: str) -> bool:
    return any(
        line.strip().startswith(("#EXT-X-TARGETDURATION", "#EXT-X-MEDIA-SEQUENCE", "#EXTINF"))
        for line in manifest_text.splitlines()
    )
