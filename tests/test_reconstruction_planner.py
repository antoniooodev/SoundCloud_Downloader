import pytest
from pydantic import ValidationError

from soundcloud_downloader.application import (
    PolicyEvaluationRequest,
    PolicyEvaluationResponse,
    ReconstructionPlan,
    ReconstructionPlanner,
    ReconstructionPlanRequest,
    StreamAnalysisRequest,
    StreamAnalysisResult,
)
from soundcloud_downloader.domain import (
    AccessMode,
    DRMStatus,
    ErrorCode,
    HLSManifestAnalysis,
    HLSManifestKind,
    MediaCodec,
    MediaContainer,
    MediaSource,
    OfflineDecision,
    OutputProfile,
    SourceProtocol,
)


PLAIN_HLS = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
segment0.ts
#EXT-X-ENDLIST
"""

AES_HLS = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="https://example.test/key.bin?token=secret"
#EXTINF:10.0,
segment0.ts
"""


def stream(
    *,
    protocol: SourceProtocol = SourceProtocol.PROGRESSIVE,
    codec: MediaCodec = MediaCodec.MP3,
    container: MediaContainer = MediaContainer.MP3,
    is_downloadable: bool = False,
    declared_drm_status: DRMStatus = DRMStatus.NONE,
    manifest_text: str | None = None,
) -> StreamAnalysisRequest:
    return StreamAnalysisRequest(
        protocol=protocol,
        codec=codec,
        container=container,
        is_downloadable=is_downloadable,
        declared_drm_status=declared_drm_status,
        manifest_text=manifest_text,
    )


def plan(
    *,
    access_mode: AccessMode = AccessMode.PUBLIC,
    requested_profile: OutputProfile | None = None,
    is_authenticated: bool = False,
    has_go_plus: bool = False,
    offline_allowed: bool | None = None,
    stream_request: StreamAnalysisRequest,
) -> ReconstructionPlan:
    return ReconstructionPlanner().plan(
        ReconstructionPlanRequest(
            access_mode=access_mode,
            requested_profile=requested_profile,
            is_authenticated=is_authenticated,
            has_go_plus=has_go_plus,
            offline_allowed=offline_allowed,
            stream=stream_request,
        )
    )


def test_public_downloadable_progressive_mp3_allows_original_by_default() -> None:
    result = plan(stream_request=stream(is_downloadable=True))

    assert result.effective_drm_status is DRMStatus.NONE
    assert result.policy.allowed is True
    assert result.policy.decision is OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD
    assert result.policy.error_code is None
    assert result.policy.output_profile is OutputProfile.ORIGINAL


def test_public_downloadable_source_allows_mp3_128_when_requested() -> None:
    result = plan(
        requested_profile=OutputProfile.MP3_128,
        stream_request=stream(is_downloadable=True),
    )

    assert result.policy.allowed is True
    assert result.policy.decision is OfflineDecision.ALLOW_MP3_128_RECONSTRUCTION
    assert result.policy.output_profile is OutputProfile.MP3_128


def test_public_non_downloadable_source_denies_source_not_downloadable() -> None:
    result = plan(stream_request=stream(is_downloadable=False))

    assert result.policy.allowed is False
    assert result.policy.decision is OfflineDecision.DENY_SOURCE_NOT_DOWNLOADABLE
    assert result.policy.error_code is ErrorCode.SOURCE_NOT_DOWNLOADABLE
    assert result.policy.output_profile is None


def test_public_hls_source_without_manifest_fails_closed_through_policy() -> None:
    result = plan(
        stream_request=stream(
            protocol=SourceProtocol.HLS,
            codec=MediaCodec.AAC,
            container=MediaContainer.M4A,
            declared_drm_status=DRMStatus.UNKNOWN,
        )
    )

    assert result.effective_drm_status is DRMStatus.UNKNOWN
    assert result.source.drm_status is DRMStatus.UNKNOWN
    assert result.policy.allowed is False
    assert result.policy.decision is OfflineDecision.DENY_UNKNOWN_UNSAFE
    assert result.policy.error_code is ErrorCode.UNKNOWN_UNSAFE


def test_public_hls_plain_manifest_remains_denied_when_not_downloadable() -> None:
    result = plan(
        stream_request=stream(
            protocol=SourceProtocol.HLS,
            codec=MediaCodec.AAC,
            container=MediaContainer.M4A,
            manifest_text=PLAIN_HLS,
        )
    )

    assert result.effective_drm_status is DRMStatus.NONE
    assert result.policy.allowed is False
    assert result.policy.decision is OfflineDecision.DENY_SOURCE_NOT_DOWNLOADABLE
    assert result.policy.error_code is ErrorCode.SOURCE_NOT_DOWNLOADABLE


def test_go_plus_authenticated_hls_aac_plain_manifest_allows_aac_m4a_by_default() -> None:
    result = plan(
        access_mode=AccessMode.GO_PLUS,
        is_authenticated=True,
        has_go_plus=True,
        offline_allowed=True,
        stream_request=stream(
            protocol=SourceProtocol.HLS,
            codec=MediaCodec.AAC,
            container=MediaContainer.M4A,
            manifest_text=PLAIN_HLS,
        ),
    )

    assert result.policy.allowed is True
    assert result.policy.decision is OfflineDecision.ALLOW_AAC_M4A_REMUX
    assert result.policy.error_code is None
    assert result.policy.output_profile is OutputProfile.AAC_M4A


def test_go_plus_authenticated_hls_aes_128_manifest_denies_drm() -> None:
    result = plan(
        access_mode=AccessMode.GO_PLUS,
        is_authenticated=True,
        has_go_plus=True,
        offline_allowed=True,
        stream_request=stream(
            protocol=SourceProtocol.HLS,
            codec=MediaCodec.AAC,
            container=MediaContainer.M4A,
            manifest_text=AES_HLS,
        ),
    )

    assert result.effective_drm_status is DRMStatus.ENCRYPTED_HLS
    assert result.policy.allowed is False
    assert result.policy.decision is OfflineDecision.DENY_DRM
    assert result.policy.error_code is ErrorCode.ENCRYPTED_STREAM_UNSUPPORTED


@pytest.mark.parametrize(
    "key_format",
    [
        "com.apple.streamingkeydelivery",
        "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed:widevine",
        "com.microsoft.playready",
    ],
)
def test_go_plus_authenticated_hls_eme_manifest_denies_drm(key_format: str) -> None:
    manifest = f"""#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://asset",KEYFORMAT="{key_format}"
segment0.ts
"""
    result = plan(
        access_mode=AccessMode.GO_PLUS,
        is_authenticated=True,
        has_go_plus=True,
        offline_allowed=True,
        stream_request=stream(
            protocol=SourceProtocol.HLS,
            codec=MediaCodec.AAC,
            container=MediaContainer.M4A,
            manifest_text=manifest,
        ),
    )

    assert result.effective_drm_status is DRMStatus.EME_DRM
    assert result.policy.allowed is False
    assert result.policy.decision is OfflineDecision.DENY_DRM
    assert result.policy.error_code is ErrorCode.DRM_UNSUPPORTED


def test_go_plus_unauthenticated_source_denies_auth_required() -> None:
    result = plan(
        access_mode=AccessMode.GO_PLUS,
        stream_request=stream(is_downloadable=True),
    )

    assert result.policy.allowed is False
    assert result.policy.decision is OfflineDecision.DENY_AUTH_REQUIRED
    assert result.policy.error_code is ErrorCode.AUTH_REQUIRED


def test_go_plus_offline_allowed_false_denies_rights_restricted() -> None:
    result = plan(
        access_mode=AccessMode.GO_PLUS,
        is_authenticated=True,
        has_go_plus=True,
        offline_allowed=False,
        stream_request=stream(is_downloadable=True),
    )

    assert result.policy.allowed is False
    assert result.policy.decision is OfflineDecision.DENY_RIGHTS_RESTRICTED
    assert result.policy.error_code is ErrorCode.RIGHTS_RESTRICTED


def test_planner_propagates_stream_analysis_warnings() -> None:
    result = plan(
        stream_request=stream(
            protocol=SourceProtocol.HLS,
            codec=MediaCodec.AAC,
            container=MediaContainer.M4A,
            declared_drm_status=DRMStatus.UNKNOWN,
        )
    )

    assert result.warnings
    assert "Missing HLS manifest text" in result.warnings[0]


def test_plan_source_drm_status_equals_effective_drm_status() -> None:
    result = plan(stream_request=stream(is_downloadable=True))

    assert result.source.drm_status is result.effective_drm_status


def test_reconstruction_plan_is_immutable() -> None:
    result = plan(stream_request=stream(is_downloadable=True))

    with pytest.raises(ValidationError):
        result.effective_drm_status = DRMStatus.UNKNOWN


def test_reconstruction_plan_request_is_immutable() -> None:
    request = ReconstructionPlanRequest(
        access_mode=AccessMode.PUBLIC,
        stream=stream(is_downloadable=True),
    )

    with pytest.raises(ValidationError):
        request.is_authenticated = True


def test_planner_delegates_to_injected_services() -> None:
    class RecordingStreamService:
        def __init__(self) -> None:
            self.request: StreamAnalysisRequest | None = None
            self.source = MediaSource(
                protocol=SourceProtocol.DOWNLOAD,
                codec=MediaCodec.MP3,
                is_downloadable=True,
                drm_status=DRMStatus.NONE,
            )

        def analyze(self, request: StreamAnalysisRequest) -> StreamAnalysisResult:
            self.request = request
            return StreamAnalysisResult(
                source=self.source,
                effective_drm_status=DRMStatus.NONE,
                warnings=("stream warning",),
            )

    class RecordingPolicyService:
        def __init__(self) -> None:
            self.request: PolicyEvaluationRequest | None = None

        def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResponse:
            self.request = request
            return PolicyEvaluationResponse(
                decision=OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD,
                allowed=True,
                reason="Injected policy response.",
                output_profile=OutputProfile.ORIGINAL,
            )

    stream_service = RecordingStreamService()
    policy_service = RecordingPolicyService()
    request = ReconstructionPlanRequest(
        access_mode=AccessMode.PUBLIC,
        requested_profile=OutputProfile.ORIGINAL,
        stream=stream(protocol=SourceProtocol.HLS, manifest_text=PLAIN_HLS),
    )

    result = ReconstructionPlanner(stream_service, policy_service).plan(request)

    assert stream_service.request is request.stream
    assert policy_service.request is not None
    assert policy_service.request.source is stream_service.source
    assert policy_service.request.requested_profile is OutputProfile.ORIGINAL
    assert result.policy.allowed is True
    assert result.warnings == ("stream warning",)


def test_reconstruction_plan_rejects_mismatched_source_and_effective_drm_status() -> None:
    source = MediaSource(protocol=SourceProtocol.DOWNLOAD, drm_status=DRMStatus.NONE)
    policy = PolicyEvaluationResponse(
        decision=OfflineDecision.DENY_UNKNOWN_UNSAFE,
        allowed=False,
        reason="Denied.",
        error_code=ErrorCode.UNKNOWN_UNSAFE,
    )

    with pytest.raises(ValidationError):
        ReconstructionPlan(
            source=source,
            effective_drm_status=DRMStatus.UNKNOWN,
            policy=policy,
        )


def test_reconstruction_plan_rejects_mismatched_hls_analysis_and_effective_drm_status() -> None:
    source = MediaSource(protocol=SourceProtocol.HLS, drm_status=DRMStatus.UNKNOWN)
    hls_analysis = HLSManifestAnalysis(
        kind=HLSManifestKind.MEDIA,
        is_hls=True,
        is_encrypted=False,
        drm_status=DRMStatus.NONE,
        has_ext_x_key=False,
        has_ext_x_session_key=False,
        has_stream_inf=False,
        has_media_sequence=True,
        has_endlist=True,
        segment_count=1,
    )
    policy = PolicyEvaluationResponse(
        decision=OfflineDecision.DENY_UNKNOWN_UNSAFE,
        allowed=False,
        reason="Denied.",
        error_code=ErrorCode.UNKNOWN_UNSAFE,
    )

    with pytest.raises(ValidationError):
        ReconstructionPlan(
            source=source,
            hls_analysis=hls_analysis,
            effective_drm_status=DRMStatus.UNKNOWN,
            policy=policy,
        )
