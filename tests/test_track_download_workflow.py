import asyncio
import socket
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import TypeVar

import pytest
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.application import (
    HLSSegmentPlanner,
    PolicyEvaluationResponse,
    ReconstructionPlan,
    ResolvedStreamAnalysisRequest,
    ResolvedStreamAnalysisResult,
    SoundCloudMetadataNormalizer,
    TrackDownloadFailureReason,
    TrackDownloadFailureStage,
    TrackDownloadWorkflow,
    TrackDownloadWorkflowError,
    select_transcoding,
)
from soundcloud_downloader.application.ports import (
    SoundCloudPlaylistSummary,
    SoundCloudResolvedResource,
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
    SoundCloudTrackSummary,
    SoundCloudUserSummary,
)
from soundcloud_downloader.domain import (
    AccessMode,
    ArtifactChecksum,
    ArtifactFormat,
    ArtifactId,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
    AudioExportFormat,
    AudioExportRequest,
    AudioExportResult,
    DRMStatus,
    ErrorCode,
    HLSMediaAssemblyResult,
    HLSSegmentStagingResult,
    MediaCodec,
    MediaContainer,
    MediaSource,
    NormalizedResolverInput,
    OfflineDecision,
    OutputProfile,
    RemuxInputArtifact,
    RemuxOutputArtifact,
    RemuxResult,
    ResolverInputType,
    SourceProtocol,
    SoundcloudDownloaderError,
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamKind,
    SoundCloudResolvedStreamUrl,
    SoundCloudResourceType,
    SoundCloudTranscodingEndpointUrl,
    SoundCloudTranscodingFormat,
    SoundCloudTranscodingMetadata,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
    StagedHLSSegment,
    TrackDownloadRequest,
    TrackDownloadResult,
    TrackDownloadStatus,
    redact_track_download_result,
)
from soundcloud_downloader.infrastructure.soundcloud import SoundCloudResponseMapper
from soundcloud_downloader.infrastructure.soundcloud.api_contract import SoundCloudAccessToken

T = TypeVar("T")

RAW_SOURCE_URL = "https://soundcloud.com/artist/example-track"
UNSAFE_SOURCE_URL = "https://soundcloud.com/artist/example-track?access_token=raw-token"
RAW_ENDPOINT_URL = "https://api.soundcloud.test/tracks/123/transcodings/hls"
RAW_REAL_ENDPOINT_URL = (
    "https://api.soundcloud.test/media/soundcloud:tracks:123/abc/stream/hls"
    "?client_secret=SHOULD_NOT_LEAK"
)
RAW_STREAM_URL = "https://media.soundcloud.test/playlist.m3u8?Policy=stream-policy"
RAW_OFFICIAL_STREAM_URL = (
    "https://playback.media-streaming.soundcloud.cloud/track/aac_160k/uuid/playlist.m3u8"
    "?client_secret=SHOULD_NOT_LEAK"
)
RAW_PROGRESSIVE_URL = "https://media.soundcloud.test/audio.mp3?Policy=stream-policy"
RAW_SEGMENT_URL = "https://media.soundcloud.test/segment0.ts?Policy=segment-policy"
RAW_MANIFEST = f"""#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
{RAW_SEGMENT_URL}
#EXT-X-ENDLIST
"""
SHA256 = "a" * 64


def run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)


def test_workflow_resolves_source_url_through_injected_resolver() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request()))

    assert workflow.resolver.calls[0].normalized_url == RAW_SOURCE_URL


def test_workflow_normalizes_resolved_track_metadata() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request()))

    assert workflow.metadata_normalizer.calls == 1


def test_workflow_rejects_resolved_playlist() -> None:
    workflow = _workflow(resource=_resolved_playlist())

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert "Resolved resource is not a downloadable track." in str(exc_info.value)


def test_workflow_rejects_resolved_user_profile() -> None:
    workflow = _workflow(resource=_resolved_user())

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert "Resolved resource is not a downloadable track." in str(exc_info.value)


def test_workflow_rejects_track_with_no_transcodings() -> None:
    workflow = _workflow(resource=_resolved_track(transcodings=()))

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert "No safe HLS transcoding is available." in str(exc_info.value)
    assert exc_info.value.stage is TrackDownloadFailureStage.TRANSCODING_SELECTION
    assert exc_info.value.reason is TrackDownloadFailureReason.NO_TRANSCODINGS


def test_workflow_calls_streams_endpoint_when_resolver_transcodings_are_empty() -> None:
    workflow = _workflow(
        resource=_resolved_track(transcodings=(), soundcloud_urn="soundcloud:tracks:123"),
        official_streams_resolver=FakeOfficialStreamsResolver(),
    )

    result = run(workflow.download_track(_request()))

    assert result.status is TrackDownloadStatus.SUCCEEDED
    assert workflow.official_streams_resolver is not None
    assert workflow.official_streams_resolver.calls == ["soundcloud:tracks:123"]
    assert workflow.endpoint_resolver.calls == []
    assert workflow.stream_analysis.calls[0].stream.url.get_secret_value() == RAW_OFFICIAL_STREAM_URL


def test_workflow_does_not_call_streams_endpoint_when_hls_transcoding_exists() -> None:
    streams_resolver = FakeOfficialStreamsResolver()
    workflow = _workflow(official_streams_resolver=streams_resolver)

    run(workflow.download_track(_request()))

    assert streams_resolver.calls == []
    assert workflow.endpoint_resolver.calls == [workflow.resolver.resource.track.transcodings[0]]


def test_workflow_streams_endpoint_uses_track_urn_when_available() -> None:
    workflow = _workflow(
        resource=_resolved_track(transcodings=(), soundcloud_urn="soundcloud:tracks:123"),
        official_streams_resolver=FakeOfficialStreamsResolver(),
    )

    run(workflow.download_track(_request()))

    assert workflow.official_streams_resolver is not None
    assert workflow.official_streams_resolver.calls == ["soundcloud:tracks:123"]


def test_workflow_streams_endpoint_falls_back_to_id_when_urn_missing() -> None:
    workflow = _workflow(
        resource=_resolved_track(transcodings=(), soundcloud_id="123", soundcloud_urn=None),
        official_streams_resolver=FakeOfficialStreamsResolver(),
    )

    run(workflow.download_track(_request()))

    assert workflow.official_streams_resolver is not None
    assert workflow.official_streams_resolver.calls == ["123"]


def test_workflow_streams_endpoint_failure_fails_safely() -> None:
    workflow = _workflow(
        resource=_resolved_track(transcodings=()),
        official_streams_resolver=FakeOfficialStreamsResolver(error=RuntimeError(RAW_OFFICIAL_STREAM_URL)),
    )

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert exc_info.value.stage is TrackDownloadFailureStage.STREAMS_ENDPOINT
    assert exc_info.value.reason is TrackDownloadFailureReason.STREAMS_ENDPOINT_FAILED
    assert RAW_OFFICIAL_STREAM_URL not in str(exc_info.value)


def test_workflow_no_official_hls_streams_fails_safely() -> None:
    workflow = _workflow(
        resource=_resolved_track(transcodings=()),
        official_streams_resolver=FakeOfficialStreamsResolver(
            error=SoundcloudDownloaderError(ErrorCode.SOURCE_NOT_DOWNLOADABLE, RAW_OFFICIAL_STREAM_URL)
        ),
    )

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert exc_info.value.stage is TrackDownloadFailureStage.STREAMS_SELECTION
    assert exc_info.value.reason is TrackDownloadFailureReason.NO_HLS_STREAMS
    assert RAW_OFFICIAL_STREAM_URL not in str(exc_info.value)


def test_workflow_reports_resolver_stage_when_resolver_fails() -> None:
    workflow = _workflow(resource=_unresolved_resource())

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert exc_info.value.stage is TrackDownloadFailureStage.RESOLVER
    assert exc_info.value.reason is TrackDownloadFailureReason.OFFICIAL_RESOLVER_PAYLOAD_INVALID
    assert exc_info.value.invalid_fields == ("media.transcodings.0.url",)


def test_workflow_selects_non_snipped_hls_transcoding_over_snipped_hls() -> None:
    snipped = _transcoding(preset="aac_snipped", snipped=True)
    full = _transcoding(preset="aac_full", snipped=False)

    selected = select_transcoding((snipped, full), output_format=AudioExportFormat.M4A)

    assert selected is full


def test_workflow_prefers_hls_aac_audio_mp4_for_m4a() -> None:
    mpeg = _transcoding(
        preset="mp3_hls",
        mime_type=SoundCloudTranscodingMimeType.AUDIO_MPEG,
    )
    mp4 = _transcoding(preset="aac_hls", mime_type=SoundCloudTranscodingMimeType.AUDIO_MP4)

    selected = select_transcoding((mpeg, mp4), output_format=AudioExportFormat.M4A)

    assert selected is mp4


def test_workflow_fails_closed_when_no_hls_transcoding_exists() -> None:
    progressive = _transcoding(
        protocol=SoundCloudTranscodingProtocol.PROGRESSIVE,
        mime_type=SoundCloudTranscodingMimeType.AUDIO_MPEG,
    )

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        select_transcoding((progressive,), output_format=AudioExportFormat.MP3)

    assert exc_info.value.stage is TrackDownloadFailureStage.TRANSCODING_SELECTION
    assert exc_info.value.reason is TrackDownloadFailureReason.NO_SAFE_HLS_TRANSCODING


def test_workflow_gets_access_token_through_injected_provider() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request()))

    assert workflow.access_token_provider.calls == 1


def test_workflow_resolves_transcoding_endpoint_through_injected_service() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request()))

    assert workflow.endpoint_resolver.calls == [workflow.resolver.resource.track.transcodings[0]]


def test_workflow_sees_real_like_resolver_media_transcodings() -> None:
    resource = _real_like_resolved_track()
    workflow = _workflow(resource=resource)

    run(workflow.download_track(_request()))

    assert resource.track is not None
    assert len(resource.track.transcodings) == 1
    assert workflow.endpoint_resolver.calls == [resource.track.transcodings[0]]
    assert workflow.endpoint_resolver.calls[0].endpoint_url.get_secret_value() == RAW_REAL_ENDPOINT_URL


def test_workflow_does_not_report_no_transcodings_for_real_like_payload() -> None:
    workflow = _workflow(resource=_real_like_resolved_track())

    result = run(workflow.download_track(_request()))

    assert result.status is TrackDownloadStatus.SUCCEEDED
    assert result.selected_transcoding.format.protocol is SoundCloudTranscodingProtocol.HLS


def test_workflow_rejects_progressive_resolved_stream_for_this_mvp() -> None:
    workflow = _workflow(stream=_stream(SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA))

    with pytest.raises(TrackDownloadWorkflowError):
        run(workflow.download_track(_request()))


def test_workflow_invokes_resolved_stream_analysis_workflow() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request()))

    assert workflow.stream_analysis.calls[0].stream == workflow.endpoint_resolver.stream


def test_workflow_rejects_denied_reconstruction_plan() -> None:
    workflow = _workflow(stream_analysis=FakeStreamAnalysis(allowed=False))

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert "Track reconstruction was denied by policy." in str(exc_info.value)


def test_workflow_builds_hls_segment_plan() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request()))

    assert workflow.segment_fetcher.plan is not None
    assert workflow.segment_fetcher.plan.segment_count == 1


def test_workflow_stages_segments_through_injected_fetcher() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request()))

    assert workflow.segment_fetcher.calls == 1


def test_workflow_assembles_staged_media_through_injected_assembler() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request()))

    assert workflow.media_assembler.calls == 1


def test_m4a_output_uses_m4a_remuxer() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request(output_format=AudioExportFormat.M4A)))

    assert workflow.remuxer.calls == 1
    assert workflow.audio_exporter.calls == 0


def test_mp3_output_uses_m4a_remuxer_then_audio_exporter() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request(output_format=AudioExportFormat.MP3)))

    assert workflow.remuxer.calls == 1
    assert workflow.audio_exporter.calls == 1
    assert workflow.audio_exporter.requests[0].output_format is AudioExportFormat.MP3


def test_wav_output_uses_m4a_remuxer_then_audio_exporter() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request(output_format=AudioExportFormat.WAV)))

    assert workflow.remuxer.calls == 1
    assert workflow.audio_exporter.calls == 1
    assert workflow.audio_exporter.requests[0].output_format is AudioExportFormat.WAV


def test_workflow_returns_track_download_result_with_status_succeeded() -> None:
    result = run(_workflow().download_track(_request()))

    assert result.status is TrackDownloadStatus.SUCCEEDED


def test_result_includes_safe_track_metadata() -> None:
    result = run(_workflow().download_track(_request()))

    assert result.metadata.title == "Example Track"
    assert result.metadata.user is not None
    assert result.metadata.user.username == "artist"


def test_result_includes_final_artifact_metadata() -> None:
    result = run(_workflow().download_track(_request()))

    assert result.final_artifact.kind is ArtifactKind.FINAL_AUDIO
    assert result.final_artifact.relative_path.value == "audio/final.m4a"


def test_result_output_format_matches_request() -> None:
    result = run(_workflow().download_track(_request(output_format=AudioExportFormat.WAV)))

    assert result.output_format is AudioExportFormat.WAV


def test_result_repr_does_not_expose_transcoding_endpoint_url() -> None:
    result = run(_workflow().download_track(_request()))

    assert RAW_ENDPOINT_URL not in repr(result)


def test_result_repr_does_not_expose_stream_url() -> None:
    result = run(_workflow().download_track(_request()))

    assert RAW_STREAM_URL not in repr(result)


def test_result_repr_does_not_expose_manifest_url() -> None:
    result = run(_workflow().download_track(_request()))

    assert RAW_STREAM_URL not in repr(result.segment_plan)


def test_result_repr_does_not_expose_segment_url() -> None:
    result = run(_workflow().download_track(_request()))

    assert RAW_SEGMENT_URL not in repr(result)


def test_result_model_dump_does_not_expose_urls_or_secrets() -> None:
    result = run(_workflow().download_track(_request()))
    dumped = str(result.model_dump(mode="json"))

    assert RAW_ENDPOINT_URL not in dumped
    assert RAW_STREAM_URL not in dumped
    assert RAW_SEGMENT_URL not in dumped
    assert "access_token" not in dumped


def test_redact_track_download_result_returns_safe_shape() -> None:
    result = run(_workflow().download_track(_request()))

    assert redact_track_download_result(result) == {
        "status": "succeeded",
        "track": {
            "id": "track-1",
            "title": "Example Track",
            "user": "artist",
        },
        "output": {
            "format": "m4a",
            "artifact_id": "final-m4a",
            "relative_path": "audio/final.m4a",
            "size_bytes": 12,
            "checksum": SHA256,
        },
        "segments": {
            "count": 1,
            "total_bytes": 7,
        },
    }


def test_endpoint_failure_fails_safely() -> None:
    workflow = _workflow(
        endpoint_resolver=FakeEndpointResolver(error=RuntimeError(RAW_ENDPOINT_URL))
    )

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert RAW_ENDPOINT_URL not in str(exc_info.value)


def test_manifest_analysis_failure_fails_safely() -> None:
    workflow = _workflow(stream_analysis=FakeStreamAnalysis(error=RuntimeError(RAW_MANIFEST)))

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert RAW_MANIFEST not in str(exc_info.value)


def test_segment_planning_failure_fails_safely() -> None:
    workflow = _workflow(segment_planner=FailingSegmentPlanner(RuntimeError(RAW_MANIFEST)))

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert RAW_MANIFEST not in str(exc_info.value)


def test_segment_fetch_failure_fails_safely() -> None:
    workflow = _workflow(segment_fetcher=FakeSegmentFetcher(error=RuntimeError(RAW_SEGMENT_URL)))

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert RAW_SEGMENT_URL not in str(exc_info.value)


def test_assembly_failure_fails_safely() -> None:
    workflow = _workflow(media_assembler=FakeMediaAssembler(error=RuntimeError("assembly failed")))

    with pytest.raises(TrackDownloadWorkflowError):
        run(workflow.download_track(_request()))


def test_remux_failure_fails_safely() -> None:
    workflow = _workflow(remuxer=FakeM4ARemuxer(error=RuntimeError("remux failed")))

    with pytest.raises(TrackDownloadWorkflowError):
        run(workflow.download_track(_request()))


def test_export_failure_fails_safely() -> None:
    workflow = _workflow(audio_exporter=FakeAudioExporter(error=RuntimeError("export failed")))

    with pytest.raises(TrackDownloadWorkflowError):
        run(workflow.download_track(_request(output_format=AudioExportFormat.MP3)))


def test_error_messages_do_not_contain_source_url_with_unsafe_query() -> None:
    request = TrackDownloadRequest.model_construct(
        source_url=UNSAFE_SOURCE_URL,
        output_format=AudioExportFormat.M4A,
        access_mode=AccessMode.PUBLIC,
        output_profile=OutputProfile.AAC_M4A,
        metadata=None,
    )
    workflow = _workflow(endpoint_resolver=FakeEndpointResolver(error=RuntimeError("failed")))

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(request))

    assert UNSAFE_SOURCE_URL not in str(exc_info.value)
    assert "raw-token" not in str(exc_info.value)


def test_error_messages_do_not_contain_manifest_text() -> None:
    workflow = _workflow(stream_analysis=FakeStreamAnalysis(error=RuntimeError(RAW_MANIFEST)))

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert RAW_MANIFEST not in str(exc_info.value)


def test_error_messages_do_not_contain_segment_url() -> None:
    workflow = _workflow(segment_fetcher=FakeSegmentFetcher(error=RuntimeError(RAW_SEGMENT_URL)))

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert RAW_SEGMENT_URL not in str(exc_info.value)


def test_error_messages_do_not_contain_access_token() -> None:
    workflow = _workflow(
        endpoint_resolver=FakeEndpointResolver(error=RuntimeError("raw-access-token"))
    )

    with pytest.raises(TrackDownloadWorkflowError) as exc_info:
        run(workflow.download_track(_request()))

    assert "raw-access-token" not in str(exc_info.value)


def test_request_model_rejects_source_url_with_access_token_query() -> None:
    with pytest.raises(ValidationError):
        _request(source_url=UNSAFE_SOURCE_URL)


def test_request_model_rejects_source_url_with_userinfo() -> None:
    with pytest.raises(ValidationError):
        _request(source_url="https://user:pass@soundcloud.com/artist/track")


def test_request_and_result_models_are_immutable() -> None:
    request = _request()
    result = run(_workflow().download_track(request))

    with pytest.raises(ValidationError):
        request.output_format = AudioExportFormat.MP3
    with pytest.raises(ValidationError):
        result.status = TrackDownloadStatus.FAILED


def test_tests_perform_no_real_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert run(_workflow().download_track(_request())).status is TrackDownloadStatus.SUCCEEDED


def test_tests_do_not_execute_real_ffmpeg() -> None:
    workflow = _workflow()

    run(workflow.download_track(_request(output_format=AudioExportFormat.MP3)))

    assert workflow.remuxer.calls == 1
    assert workflow.audio_exporter.calls == 1


class WorkflowFixture:
    def __init__(
        self,
        *,
        resolver: "FakeResolver",
        access_token_provider: "FakeAccessTokenProvider",
        metadata_normalizer: "CountingMetadataNormalizer",
        endpoint_resolver: "FakeEndpointResolver",
        official_streams_resolver: "FakeOfficialStreamsResolver | None",
        stream_analysis: "FakeStreamAnalysis",
        segment_fetcher: "FakeSegmentFetcher",
        media_assembler: "FakeMediaAssembler",
        remuxer: "FakeM4ARemuxer",
        audio_exporter: "FakeAudioExporter",
        workflow: TrackDownloadWorkflow,
    ) -> None:
        self.resolver = resolver
        self.access_token_provider = access_token_provider
        self.metadata_normalizer = metadata_normalizer
        self.endpoint_resolver = endpoint_resolver
        self.official_streams_resolver = official_streams_resolver
        self.stream_analysis = stream_analysis
        self.segment_fetcher = segment_fetcher
        self.media_assembler = media_assembler
        self.remuxer = remuxer
        self.audio_exporter = audio_exporter
        self._workflow = workflow

    async def download_track(self, request: TrackDownloadRequest) -> TrackDownloadResult:
        return await self._workflow.download_track(request)


class FakeResolver:
    def __init__(self, resource: SoundCloudResolvedResource) -> None:
        self.resource = resource
        self.calls: list[NormalizedResolverInput] = []

    async def resolve(self, normalized: NormalizedResolverInput) -> SoundCloudResolvedResource:
        self.calls.append(normalized)
        return self.resource


class FakeAccessTokenProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_access_token(self) -> SoundCloudAccessToken:
        self.calls += 1
        return SoundCloudAccessToken(value=SecretStr("raw-access-token"))


class CountingMetadataNormalizer(SoundCloudMetadataNormalizer):
    def __init__(self) -> None:
        self.calls = 0

    def normalize(self, resource: SoundCloudResolvedResource):  # type: ignore[no-untyped-def]
        self.calls += 1
        return super().normalize(resource)


class FakeEndpointResolver:
    def __init__(
        self,
        *,
        stream: SoundCloudResolvedStream | None = None,
        error: Exception | None = None,
    ) -> None:
        self.stream = stream or _stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)
        self.error = error
        self.calls: list[SoundCloudTranscodingMetadata] = []

    async def resolve_stream_url(
        self,
        *,
        transcoding: SoundCloudTranscodingMetadata,
        access_token: SoundCloudAccessToken,
    ) -> SoundCloudResolvedStream:
        self.calls.append(transcoding)
        if self.error is not None:
            raise self.error
        return self.stream


class FakeOfficialStreamsResolver:
    def __init__(
        self,
        *,
        stream: SoundCloudResolvedStream | None = None,
        error: Exception | None = None,
    ) -> None:
        self.stream = stream or _official_stream()
        self.error = error
        self.calls: list[str] = []

    async def resolve_hls_stream(
        self,
        *,
        track_urn: str,
        access_token: SoundCloudAccessToken,
    ) -> SoundCloudResolvedStream:
        self.calls.append(track_urn)
        if self.error is not None:
            raise self.error
        return self.stream


class FakeStreamAnalysis:
    def __init__(
        self,
        *,
        allowed: bool = True,
        error: Exception | None = None,
        manifest_text: str = RAW_MANIFEST,
    ) -> None:
        self.allowed = allowed
        self.error = error
        self.manifest_text = manifest_text
        self.calls: list[ResolvedStreamAnalysisRequest] = []

    async def analyze(
        self,
        request: ResolvedStreamAnalysisRequest,
    ) -> ResolvedStreamAnalysisResult:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return ResolvedStreamAnalysisResult(
            stream=request.stream,
            manifest_analysis=None,
            plan=_plan(allowed=self.allowed),
            manifest_text=SecretStr(self.manifest_text),
        )


class FailingSegmentPlanner:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def build_plan(self, request):  # type: ignore[no-untyped-def]
        raise self.error


class FakeSegmentFetcher:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0
        self.plan = None

    async def stage_segments(self, *, plan):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.plan = plan
        if self.error is not None:
            raise self.error
        return _staging_result(plan.manifest_url)


class FakeMediaAssembler:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def assemble(self, *, staging_result: HLSSegmentStagingResult) -> HLSMediaAssemblyResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return HLSMediaAssemblyResult(
            artifact=_artifact(
                artifact_id="assembled-media",
                kind=ArtifactKind.STAGED_MEDIA,
                format=ArtifactFormat.AAC,
                relative_path="assembled/media.aac",
                size_bytes=7,
            ),
            source_segment_count=staging_result.segment_count,
            total_duration_seconds=staging_result.total_duration_seconds,
            total_bytes=staging_result.total_bytes,
        )


class FakeM4ARemuxer:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def remux_to_m4a(self, *, input_artifact: ArtifactMetadata) -> RemuxResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return RemuxResult(
            input_artifact=RemuxInputArtifact(artifact=input_artifact),
            output_artifact=RemuxOutputArtifact(
                artifact=_artifact(
                    artifact_id="final-m4a",
                    kind=ArtifactKind.FINAL_AUDIO,
                    format=ArtifactFormat.M4A,
                    relative_path="audio/final.m4a",
                    size_bytes=12,
                )
            ),
        )


class FakeAudioExporter:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0
        self.requests: list[AudioExportRequest] = []

    def export(self, request: AudioExportRequest) -> AudioExportResult:
        self.calls += 1
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return AudioExportResult(
            input_artifact=request.input_artifact,
            output_artifact=_artifact(
                artifact_id=f"final-{request.output_format.value}",
                kind=ArtifactKind.FINAL_AUDIO,
                format=(
                    ArtifactFormat.MP3
                    if request.output_format is AudioExportFormat.MP3
                    else ArtifactFormat.WAV
                ),
                relative_path=f"audio/final.{request.output_format.value}",
                size_bytes=13,
            ),
            output_format=request.output_format,
            metadata_embedded=request.metadata is not None,
        )


def _workflow(
    *,
    resource: SoundCloudResolvedResource | None = None,
    stream: SoundCloudResolvedStream | None = None,
    endpoint_resolver: FakeEndpointResolver | None = None,
    official_streams_resolver: FakeOfficialStreamsResolver | None = None,
    stream_analysis: FakeStreamAnalysis | None = None,
    segment_planner=None,  # type: ignore[no-untyped-def]
    segment_fetcher: FakeSegmentFetcher | None = None,
    media_assembler: FakeMediaAssembler | None = None,
    remuxer: FakeM4ARemuxer | None = None,
    audio_exporter: FakeAudioExporter | None = None,
) -> WorkflowFixture:
    resolver = FakeResolver(resource or _resolved_track())
    access_token_provider = FakeAccessTokenProvider()
    metadata_normalizer = CountingMetadataNormalizer()
    endpoint_resolver = endpoint_resolver or FakeEndpointResolver(stream=stream)
    stream_analysis = stream_analysis or FakeStreamAnalysis()
    segment_fetcher = segment_fetcher or FakeSegmentFetcher()
    media_assembler = media_assembler or FakeMediaAssembler()
    remuxer = remuxer or FakeM4ARemuxer()
    audio_exporter = audio_exporter or FakeAudioExporter()
    workflow = TrackDownloadWorkflow(
        resolver=resolver,
        access_token_provider=access_token_provider,
        metadata_normalizer=metadata_normalizer,
        transcoding_endpoint_resolver=endpoint_resolver,
        official_streams_resolver=official_streams_resolver,
        stream_analysis_workflow=stream_analysis,  # type: ignore[arg-type]
        hls_segment_planner=segment_planner or HLSSegmentPlanner(),
        hls_segment_fetcher=segment_fetcher,
        hls_media_assembler=media_assembler,
        m4a_remuxer=remuxer,
        audio_exporter=audio_exporter,
    )
    return WorkflowFixture(
        resolver=resolver,
        access_token_provider=access_token_provider,
        metadata_normalizer=metadata_normalizer,
        endpoint_resolver=endpoint_resolver,
        official_streams_resolver=official_streams_resolver,
        stream_analysis=stream_analysis,
        segment_fetcher=segment_fetcher,
        media_assembler=media_assembler,
        remuxer=remuxer,
        audio_exporter=audio_exporter,
        workflow=workflow,
    )


def _request(
    *,
    source_url: str = RAW_SOURCE_URL,
    output_format: AudioExportFormat = AudioExportFormat.M4A,
) -> TrackDownloadRequest:
    return TrackDownloadRequest(
        source_url=source_url,
        output_format=output_format,
        access_mode=AccessMode.PUBLIC,
        output_profile=OutputProfile.AAC_M4A,
    )


def _resolved_track(
    *,
    soundcloud_id: str = "track-1",
    soundcloud_urn: str | None = None,
    transcodings: tuple[SoundCloudTranscodingMetadata, ...] | None = None,
) -> SoundCloudResolvedResource:
    return SoundCloudResolvedResource(
        status=SoundCloudResolveStatus.RESOLVED,
        kind=SoundCloudResourceKind.TRACK,
        normalized=_normalized(),
        track=SoundCloudTrackSummary(
            soundcloud_id=soundcloud_id,
            soundcloud_urn=soundcloud_urn,
            title="Example Track",
            duration_ms=123_000,
            permalink_url_redacted=RAW_SOURCE_URL,
            user=_user_summary(),
            is_public=True,
            is_downloadable=True,
            transcodings=(_transcoding(),) if transcodings is None else transcodings,
        ),
    )


def _resolved_playlist() -> SoundCloudResolvedResource:
    return SoundCloudResolvedResource(
        status=SoundCloudResolveStatus.RESOLVED,
        kind=SoundCloudResourceKind.PLAYLIST,
        normalized=_normalized(),
        playlist=SoundCloudPlaylistSummary(
            soundcloud_id="playlist-1",
            title="Playlist",
            tracks=(),
        ),
    )


def _resolved_user() -> SoundCloudResolvedResource:
    return SoundCloudResolvedResource(
        status=SoundCloudResolveStatus.RESOLVED,
        kind=SoundCloudResourceKind.USER,
        normalized=_normalized(),
        user=_user_summary(),
    )


def _unresolved_resource() -> SoundCloudResolvedResource:
    return SoundCloudResolvedResource(
        status=SoundCloudResolveStatus.ERROR,
        kind=SoundCloudResourceKind.UNKNOWN,
        normalized=_normalized(),
        warnings=("SoundCloud resolver payload was malformed.",),
        invalid_fields=("media.transcodings.0.url",),
    )


def _real_like_resolved_track() -> SoundCloudResolvedResource:
    resource = SoundCloudResponseMapper().map_resolved_resource(
        {
            "kind": "track",
            "id": 123,
            "title": "Real Like Track",
            "duration": 30_000,
            "sharing": "public",
            "downloadable": False,
            "media": {
                "transcodings": [
                    {
                        "url": RAW_REAL_ENDPOINT_URL,
                        "preset": "mp3_0_1",
                        "duration": 12_345,
                        "snipped": False,
                        "format": {
                            "protocol": "hls",
                            "mime_type": "audio/mpeg",
                        },
                        "quality": "sq",
                    }
                ]
            },
        },
        _normalized(),
    )
    assert resource.status is SoundCloudResolveStatus.RESOLVED
    return resource


def _user_summary() -> SoundCloudUserSummary:
    return SoundCloudUserSummary(
        soundcloud_id="user-1",
        username="artist",
        permalink_url_redacted="https://soundcloud.com/artist",
    )


def _normalized() -> NormalizedResolverInput:
    return NormalizedResolverInput(
        input_type=ResolverInputType.URL,
        resource_type=SoundCloudResourceType.TRACK,
        normalized_url=RAW_SOURCE_URL,
        normalized_path="/artist/example-track",
        host="soundcloud.com",
        path_parts=("artist", "example-track"),
    )


def _transcoding(
    *,
    preset: str = "aac_0_1",
    protocol: SoundCloudTranscodingProtocol = SoundCloudTranscodingProtocol.HLS,
    mime_type: SoundCloudTranscodingMimeType = SoundCloudTranscodingMimeType.AUDIO_MP4,
    snipped: bool | None = False,
) -> SoundCloudTranscodingMetadata:
    return SoundCloudTranscodingMetadata(
        preset=preset,
        quality="sq",
        snipped=snipped,
        format=SoundCloudTranscodingFormat(protocol=protocol, mime_type=mime_type),
        endpoint_url=SoundCloudTranscodingEndpointUrl(value=SecretStr(RAW_ENDPOINT_URL)),
    )


def _stream(kind: SoundCloudResolvedStreamKind) -> SoundCloudResolvedStream:
    protocol = (
        SoundCloudTranscodingProtocol.PROGRESSIVE
        if kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA
        else SoundCloudTranscodingProtocol.HLS
    )
    raw_url = (
        RAW_PROGRESSIVE_URL
        if kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA
        else RAW_STREAM_URL
    )
    return SoundCloudResolvedStream(
        kind=kind,
        url=SoundCloudResolvedStreamUrl(value=SecretStr(raw_url)),
        protocol=protocol,
        mime_type=SoundCloudTranscodingMimeType.AUDIO_MP4,
        preset="aac_0_1",
        quality="sq",
        snipped=False,
    )


def _official_stream() -> SoundCloudResolvedStream:
    return SoundCloudResolvedStream(
        kind=SoundCloudResolvedStreamKind.HLS_MANIFEST,
        url=SoundCloudResolvedStreamUrl(value=SecretStr(RAW_OFFICIAL_STREAM_URL)),
        protocol=SoundCloudTranscodingProtocol.HLS,
        mime_type=SoundCloudTranscodingMimeType.AUDIO_MP4,
        preset="hls_aac_160",
        quality="sq",
        snipped=False,
    )


def _plan(*, allowed: bool) -> ReconstructionPlan:
    return ReconstructionPlan(
        source=MediaSource(
            protocol=SourceProtocol.HLS,
            mime_type="audio/mp4",
            codec=MediaCodec.AAC,
            container=MediaContainer.M4A,
            drm_status=DRMStatus.NONE,
        ),
        effective_drm_status=DRMStatus.NONE,
        policy=PolicyEvaluationResponse(
            decision=(OfflineDecision.ALLOW_AAC_M4A_REMUX if allowed else OfflineDecision.DENY_DRM),
            allowed=allowed,
            reason="allowed" if allowed else "denied",
            error_code=None if allowed else ErrorCode.DRM_UNSUPPORTED,
            output_profile=OutputProfile.AAC_M4A,
        ),
    )


def _staging_result(manifest_url: SoundCloudResolvedStreamUrl) -> HLSSegmentStagingResult:
    return HLSSegmentStagingResult(
        manifest_url=manifest_url,
        segments=(
            StagedHLSSegment(
                index=0,
                artifact=_artifact(
                    artifact_id="segment-0",
                    kind=ArtifactKind.HLS_SEGMENT,
                    format=ArtifactFormat.AAC,
                    relative_path="segments/0.aac",
                    size_bytes=7,
                ),
                duration_seconds=10.0,
            ),
        ),
        total_bytes=7,
    )


def _artifact(
    *,
    artifact_id: str,
    kind: ArtifactKind,
    format: ArtifactFormat,
    relative_path: str,
    size_bytes: int,
) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=ArtifactId(value=artifact_id),
        kind=kind,
        format=format,
        relative_path=ArtifactRelativePath(value=relative_path),
        size_bytes=size_bytes,
        checksum=ArtifactChecksum(value=SHA256),
        created_at=datetime(2026, 5, 30, tzinfo=UTC),
    )
