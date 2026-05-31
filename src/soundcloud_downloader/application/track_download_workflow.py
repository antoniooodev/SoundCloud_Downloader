from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from soundcloud_downloader.application.hls_segment_planner import (
    HLSSegmentPlanner,
    HLSSegmentPlanningRequest,
)
from soundcloud_downloader.application.metadata_normalizer import (
    SoundCloudMetadataNormalizationError,
    SoundCloudMetadataNormalizer,
)
from soundcloud_downloader.application.ports import (
    AccessTokenProviderPort,
    SoundCloudResolveStatus,
    SoundCloudResolverPort,
)
from soundcloud_downloader.application.resolved_stream_analysis_workflow import (
    ResolvedStreamAnalysisRequest,
    ResolvedStreamAnalysisWorkflow,
)
from soundcloud_downloader.application.resolver_input import ResolverInputNormalizer
from soundcloud_downloader.domain import (
    ArtifactMetadata,
    ArtifactRelativePath,
    AudioExportFormat,
    AudioExportMetadata,
    AudioExportRequest,
    AudioExportResult,
    ErrorCode,
    HLSMediaAssemblyResult,
    HLSSegmentPlan,
    HLSSegmentStagingResult,
    RemuxResult,
    SoundcloudDownloaderError,
    SoundCloudMetadataKind,
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamKind,
    SoundCloudTrackMetadata,
    SoundCloudTranscodingMetadata,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
    TrackDownloadRequest,
    TrackDownloadResult,
    TrackDownloadStatus,
)

if TYPE_CHECKING:
    from soundcloud_downloader.infrastructure.soundcloud.api_contract import SoundCloudAccessToken

_WORKFLOW_ERROR_MESSAGE = "Track download workflow failed."
_NON_TRACK_ERROR_MESSAGE = "Resolved resource is not a downloadable track."
_NO_HLS_ERROR_MESSAGE = "No safe HLS transcoding is available."
_POLICY_DENIED_ERROR_MESSAGE = "Track reconstruction was denied by policy."


class TrackDownloadFailureStage(str, Enum):
    RESOLVER = "resolver"
    METADATA_NORMALIZATION = "metadata_normalization"
    TRANSCODING_SELECTION = "transcoding_selection"
    TRANSCODING_ENDPOINT = "transcoding_endpoint"
    STREAM_ANALYSIS = "stream_analysis"
    SEGMENT_PLANNING = "segment_planning"
    SEGMENT_STAGING = "segment_staging"
    MEDIA_ASSEMBLY = "media_assembly"
    REMUX = "remux"
    EXPORT = "export"
    UNKNOWN = "unknown"


class TrackDownloadFailureReason(str, Enum):
    OFFICIAL_RESOLVER_PAYLOAD_INVALID = "official_resolver_payload_invalid"
    RESOLVED_RESOURCE_NOT_TRACK = "resolved_resource_not_track"
    NO_TRANSCODINGS = "no_transcodings"
    NO_SAFE_HLS_TRANSCODING = "no_safe_hls_transcoding"
    TRANSCODING_ENDPOINT_FAILED = "transcoding_endpoint_failed"
    STREAM_ANALYSIS_FAILED = "stream_analysis_failed"
    POLICY_DENIED = "policy_denied"
    SEGMENT_PLANNING_FAILED = "segment_planning_failed"
    SEGMENT_STAGING_FAILED = "segment_staging_failed"
    MEDIA_ASSEMBLY_FAILED = "media_assembly_failed"
    REMUX_FAILED = "remux_failed"
    EXPORT_FAILED = "export_failed"
    UNKNOWN = "unknown"


class TrackDownloadWorkflowError(SoundcloudDownloaderError):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        stage: TrackDownloadFailureStage = TrackDownloadFailureStage.UNKNOWN,
        reason: TrackDownloadFailureReason = TrackDownloadFailureReason.UNKNOWN,
        invalid_fields: tuple[str, ...] = (),
    ) -> None:
        self.stage = stage
        self.reason = reason
        self.invalid_fields = invalid_fields
        super().__init__(code, message)


@runtime_checkable
class TranscodingEndpointResolverPort(Protocol):
    async def resolve_stream_url(
        self,
        *,
        transcoding: SoundCloudTranscodingMetadata,
        access_token: SoundCloudAccessToken,
    ) -> SoundCloudResolvedStream: ...


@runtime_checkable
class HLSSegmentFetcherPort(Protocol):
    async def stage_segments(
        self,
        *,
        plan: HLSSegmentPlan,
    ) -> HLSSegmentStagingResult: ...


@runtime_checkable
class HLSMediaAssemblerPort(Protocol):
    def assemble(
        self,
        *,
        staging_result: HLSSegmentStagingResult,
    ) -> HLSMediaAssemblyResult: ...


@runtime_checkable
class M4ARemuxerPort(Protocol):
    def remux_to_m4a(
        self,
        *,
        input_artifact: ArtifactMetadata,
    ) -> RemuxResult: ...


@runtime_checkable
class AudioExporterPort(Protocol):
    def export(
        self,
        request: AudioExportRequest,
    ) -> AudioExportResult: ...


class TrackDownloadWorkflow:
    def __init__(
        self,
        *,
        resolver: SoundCloudResolverPort,
        access_token_provider: AccessTokenProviderPort,
        metadata_normalizer: SoundCloudMetadataNormalizer,
        transcoding_endpoint_resolver: TranscodingEndpointResolverPort,
        stream_analysis_workflow: ResolvedStreamAnalysisWorkflow,
        hls_segment_planner: HLSSegmentPlanner,
        hls_segment_fetcher: HLSSegmentFetcherPort,
        hls_media_assembler: HLSMediaAssemblerPort,
        m4a_remuxer: M4ARemuxerPort,
        audio_exporter: AudioExporterPort,
    ) -> None:
        self._resolver = resolver
        self._access_token_provider = access_token_provider
        self._metadata_normalizer = metadata_normalizer
        self._transcoding_endpoint_resolver = transcoding_endpoint_resolver
        self._stream_analysis_workflow = stream_analysis_workflow
        self._hls_segment_planner = hls_segment_planner
        self._hls_segment_fetcher = hls_segment_fetcher
        self._hls_media_assembler = hls_media_assembler
        self._m4a_remuxer = m4a_remuxer
        self._audio_exporter = audio_exporter
        self._resolver_input_normalizer = ResolverInputNormalizer()

    async def download_track(
        self,
        request: TrackDownloadRequest,
    ) -> TrackDownloadResult:
        try:
            try:
                normalized_input = self._resolver_input_normalizer.normalize(request.source_url)
                resolved_resource = await self._resolver.resolve(normalized_input)
            except Exception as exc:
                raise TrackDownloadWorkflowError(
                    ErrorCode.UNKNOWN_UNSAFE,
                    _WORKFLOW_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.RESOLVER,
                    reason=TrackDownloadFailureReason.OFFICIAL_RESOLVER_PAYLOAD_INVALID,
                    invalid_fields=("unknown",),
                ) from exc
            if resolved_resource.status is not SoundCloudResolveStatus.RESOLVED:
                raise TrackDownloadWorkflowError(
                    ErrorCode.UNKNOWN_UNSAFE,
                    _WORKFLOW_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.RESOLVER,
                    reason=TrackDownloadFailureReason.OFFICIAL_RESOLVER_PAYLOAD_INVALID,
                    invalid_fields=resolved_resource.invalid_fields or ("unknown",),
                )

            try:
                metadata = self._metadata_normalizer.normalize(resolved_resource)
            except SoundCloudMetadataNormalizationError as exc:
                raise TrackDownloadWorkflowError(
                    ErrorCode.UNKNOWN_UNSAFE,
                    _WORKFLOW_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.METADATA_NORMALIZATION,
                    reason=TrackDownloadFailureReason.RESOLVED_RESOURCE_NOT_TRACK,
                ) from exc
            if (
                metadata.kind is not SoundCloudMetadataKind.TRACK
                or not isinstance(metadata, SoundCloudTrackMetadata)
                or resolved_resource.track is None
            ):
                raise TrackDownloadWorkflowError(
                    ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                    _NON_TRACK_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.METADATA_NORMALIZATION,
                    reason=TrackDownloadFailureReason.RESOLVED_RESOURCE_NOT_TRACK,
                )

            try:
                transcoding = select_transcoding(
                    tuple(
                        item
                        for item in resolved_resource.track.transcodings
                        if isinstance(item, SoundCloudTranscodingMetadata)
                    ),
                    output_format=request.output_format,
                )
            except TrackDownloadWorkflowError:
                raise
            except Exception as exc:
                raise TrackDownloadWorkflowError(
                    ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                    _NO_HLS_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.TRANSCODING_SELECTION,
                    reason=TrackDownloadFailureReason.NO_SAFE_HLS_TRANSCODING,
                ) from exc

            try:
                access_token = await self._access_token_provider.get_access_token()
                stream = await self._transcoding_endpoint_resolver.resolve_stream_url(
                    transcoding=transcoding,
                    access_token=access_token,
                )
            except Exception as exc:
                raise TrackDownloadWorkflowError(
                    ErrorCode.UNKNOWN_UNSAFE,
                    _WORKFLOW_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.TRANSCODING_ENDPOINT,
                    reason=TrackDownloadFailureReason.TRANSCODING_ENDPOINT_FAILED,
                ) from exc
            if stream.kind is not SoundCloudResolvedStreamKind.HLS_MANIFEST:
                raise TrackDownloadWorkflowError(
                    ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                    _NO_HLS_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.TRANSCODING_ENDPOINT,
                    reason=TrackDownloadFailureReason.NO_SAFE_HLS_TRANSCODING,
                )

            try:
                stream_analysis = await self._stream_analysis_workflow.analyze(
                    ResolvedStreamAnalysisRequest(
                        stream=stream,
                        access_mode=request.access_mode,
                        output_profile=request.output_profile,
                    )
                )
            except Exception as exc:
                raise TrackDownloadWorkflowError(
                    ErrorCode.UNKNOWN_UNSAFE,
                    _WORKFLOW_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.STREAM_ANALYSIS,
                    reason=TrackDownloadFailureReason.STREAM_ANALYSIS_FAILED,
                ) from exc
            if not stream_analysis.plan.policy.allowed:
                raise TrackDownloadWorkflowError(
                    stream_analysis.plan.policy.error_code or ErrorCode.UNKNOWN_UNSAFE,
                    _POLICY_DENIED_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.STREAM_ANALYSIS,
                    reason=TrackDownloadFailureReason.POLICY_DENIED,
                )
            if stream_analysis.manifest_text is None:
                raise TrackDownloadWorkflowError(
                    ErrorCode.UNKNOWN_UNSAFE,
                    _WORKFLOW_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.STREAM_ANALYSIS,
                    reason=TrackDownloadFailureReason.STREAM_ANALYSIS_FAILED,
                )

            try:
                segment_plan = self._hls_segment_planner.build_plan(
                    HLSSegmentPlanningRequest(
                        manifest_url=stream.url,
                        manifest_text=stream_analysis.manifest_text,
                    )
                )
            except Exception as exc:
                raise TrackDownloadWorkflowError(
                    ErrorCode.UNKNOWN_UNSAFE,
                    _WORKFLOW_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.SEGMENT_PLANNING,
                    reason=TrackDownloadFailureReason.SEGMENT_PLANNING_FAILED,
                ) from exc
            try:
                staging_result = await self._hls_segment_fetcher.stage_segments(plan=segment_plan)
            except Exception as exc:
                raise TrackDownloadWorkflowError(
                    ErrorCode.UNKNOWN_UNSAFE,
                    _WORKFLOW_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.SEGMENT_STAGING,
                    reason=TrackDownloadFailureReason.SEGMENT_STAGING_FAILED,
                ) from exc
            try:
                assembly_result = self._hls_media_assembler.assemble(
                    staging_result=staging_result
                )
            except Exception as exc:
                raise TrackDownloadWorkflowError(
                    ErrorCode.UNKNOWN_UNSAFE,
                    _WORKFLOW_ERROR_MESSAGE,
                    stage=TrackDownloadFailureStage.MEDIA_ASSEMBLY,
                    reason=TrackDownloadFailureReason.MEDIA_ASSEMBLY_FAILED,
                ) from exc
            final_artifact = self._final_artifact(
                output_format=request.output_format,
                assembly_artifact=assembly_result.artifact,
                metadata=request.metadata,
            )
            return TrackDownloadResult(
                status=TrackDownloadStatus.SUCCEEDED,
                metadata=metadata,
                selected_transcoding=transcoding,
                stream_analysis=stream_analysis,
                segment_plan=segment_plan,
                staging_result=staging_result,
                assembly_result=assembly_result,
                final_artifact=final_artifact,
                output_format=request.output_format,
            )
        except TrackDownloadWorkflowError:
            raise
        except Exception as exc:
            raise TrackDownloadWorkflowError(
                ErrorCode.UNKNOWN_UNSAFE,
                _WORKFLOW_ERROR_MESSAGE,
                stage=TrackDownloadFailureStage.UNKNOWN,
                reason=TrackDownloadFailureReason.UNKNOWN,
            ) from exc

    def _final_artifact(
        self,
        *,
        output_format: AudioExportFormat,
        assembly_artifact: ArtifactMetadata,
        metadata: AudioExportMetadata | None,
    ) -> ArtifactMetadata:
        try:
            remux_result = self._m4a_remuxer.remux_to_m4a(input_artifact=assembly_artifact)
        except Exception as exc:
            raise TrackDownloadWorkflowError(
                ErrorCode.FFMPEG_FAILED,
                _WORKFLOW_ERROR_MESSAGE,
                stage=TrackDownloadFailureStage.REMUX,
                reason=TrackDownloadFailureReason.REMUX_FAILED,
            ) from exc
        m4a_artifact = remux_result.output_artifact.artifact
        if output_format is AudioExportFormat.M4A:
            return m4a_artifact
        try:
            export_result = self._audio_exporter.export(
                AudioExportRequest(
                    input_artifact=m4a_artifact,
                    output_format=output_format,
                    output_path=_output_path(output_format),
                    metadata=metadata,
                )
            )
        except Exception as exc:
            raise TrackDownloadWorkflowError(
                ErrorCode.FFMPEG_FAILED,
                _WORKFLOW_ERROR_MESSAGE,
                stage=TrackDownloadFailureStage.EXPORT,
                reason=TrackDownloadFailureReason.EXPORT_FAILED,
            ) from exc
        return export_result.output_artifact


def select_transcoding(
    transcodings: tuple[SoundCloudTranscodingMetadata, ...],
    *,
    output_format: AudioExportFormat,
) -> SoundCloudTranscodingMetadata:
    hls_transcodings = tuple(
        transcoding
        for transcoding in transcodings
        if transcoding.format.protocol is SoundCloudTranscodingProtocol.HLS
    )
    non_snipped_hls = tuple(
        transcoding for transcoding in hls_transcodings if transcoding.snipped is not True
    )
    if not hls_transcodings or not non_snipped_hls:
        reason = (
            TrackDownloadFailureReason.NO_TRANSCODINGS
            if not transcodings
            else TrackDownloadFailureReason.NO_SAFE_HLS_TRANSCODING
        )
        raise TrackDownloadWorkflowError(
            ErrorCode.SOURCE_NOT_DOWNLOADABLE,
            _NO_HLS_ERROR_MESSAGE,
            stage=TrackDownloadFailureStage.TRANSCODING_SELECTION,
            reason=reason,
        )

    ranked = sorted(
        non_snipped_hls,
        key=lambda transcoding: _transcoding_rank(transcoding, output_format=output_format),
    )
    return ranked[0]


def _transcoding_rank(
    transcoding: SoundCloudTranscodingMetadata,
    *,
    output_format: AudioExportFormat,
) -> tuple[int, str, str]:
    mime_type = transcoding.format.mime_type
    if output_format is AudioExportFormat.M4A:
        priority = 0 if mime_type is SoundCloudTranscodingMimeType.AUDIO_MP4 else 1
    elif mime_type is SoundCloudTranscodingMimeType.AUDIO_MP4:
        priority = 0
    elif mime_type is SoundCloudTranscodingMimeType.AUDIO_MPEG:
        priority = 1
    else:
        priority = 2
    return priority, transcoding.preset or "", transcoding.quality or ""


def _output_path(output_format: AudioExportFormat) -> ArtifactRelativePath:
    if output_format is AudioExportFormat.MP3:
        return ArtifactRelativePath(value="audio/final.mp3")
    if output_format is AudioExportFormat.WAV:
        return ArtifactRelativePath(value="audio/final.wav")
    return ArtifactRelativePath(value="audio/final.m4a")
