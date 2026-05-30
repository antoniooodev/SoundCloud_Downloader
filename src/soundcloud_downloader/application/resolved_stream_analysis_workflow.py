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
    HLSManifestAnalysis,
    MediaCodec,
    MediaContainer,
    OutputProfile,
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamKind,
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
        if request.stream.kind is SoundCloudResolvedStreamKind.HLS_MANIFEST:
            manifest_text = await self._manifest_fetcher.fetch_manifest(stream=request.stream)

        stream_request = _stream_analysis_request(
            request.stream,
            manifest_text=manifest_text,
        )
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
        return ResolvedStreamAnalysisResult(
            stream=request.stream,
            manifest_analysis=plan.hls_analysis,
            plan=plan,
            manifest_text=None if manifest_text is None else SecretStr(manifest_text),
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
