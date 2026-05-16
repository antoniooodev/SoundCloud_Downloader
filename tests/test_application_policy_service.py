import pytest
from pydantic import ValidationError

from soundcloud_downloader.application import (
    PolicyEvaluationRequest,
    PolicyEvaluationResponse,
    PolicyEvaluationService,
)
from soundcloud_downloader.domain import (
    AccessMode,
    DRMStatus,
    ErrorCode,
    MediaCodec,
    MediaSource,
    OfflineDecision,
    OutputProfile,
    PolicyDecision,
    ReconstructionPolicyEngine,
    SourceProtocol,
    TrackAccessContext,
)


def source(
    *,
    protocol: SourceProtocol = SourceProtocol.DOWNLOAD,
    codec: MediaCodec = MediaCodec.MP3,
    is_downloadable: bool = False,
    drm_status: DRMStatus = DRMStatus.NONE,
) -> MediaSource:
    return MediaSource(
        protocol=protocol,
        codec=codec,
        is_downloadable=is_downloadable,
        drm_status=drm_status,
    )


def test_policy_evaluation_request_to_context_builds_domain_context() -> None:
    media_source = source(is_downloadable=True)
    request = PolicyEvaluationRequest(
        access_mode=AccessMode.GO_PLUS,
        requested_profile=OutputProfile.AAC_M4A,
        is_authenticated=True,
        has_go_plus=True,
        is_public=False,
        is_go_plus_track=True,
        is_preview_only=False,
        is_downloadable=True,
        is_own_track=False,
        offline_allowed=True,
        source=media_source,
    )

    context = request.to_context()

    assert context.access_mode is AccessMode.GO_PLUS
    assert context.is_authenticated is True
    assert context.has_go_plus is True
    assert context.is_public is False
    assert context.is_go_plus_track is True
    assert context.is_preview_only is False
    assert context.is_downloadable is True
    assert context.is_own_track is False
    assert context.offline_allowed is True
    assert context.source is media_source


def test_policy_evaluation_service_allows_public_original_for_downloadable_source() -> None:
    service = PolicyEvaluationService()
    request = PolicyEvaluationRequest(
        access_mode=AccessMode.PUBLIC,
        requested_profile=OutputProfile.ORIGINAL,
        source=source(is_downloadable=True),
    )

    response = service.evaluate(request)

    assert response.allowed is True
    assert response.decision is OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD
    assert response.error_code is None
    assert response.output_profile is OutputProfile.ORIGINAL
    assert response.reason.strip()


def test_policy_evaluation_service_denies_missing_source() -> None:
    service = PolicyEvaluationService()
    request = PolicyEvaluationRequest(access_mode=AccessMode.PUBLIC)

    response = service.evaluate(request)

    assert response.allowed is False
    assert response.decision is OfflineDecision.DENY_SOURCE_NOT_DOWNLOADABLE
    assert response.error_code is ErrorCode.SOURCE_NOT_DOWNLOADABLE
    assert response.output_profile is None
    assert response.reason.strip()


def test_policy_evaluation_service_denies_unknown_drm_as_unknown_unsafe() -> None:
    service = PolicyEvaluationService()
    request = PolicyEvaluationRequest(
        access_mode=AccessMode.PUBLIC,
        source=source(drm_status=DRMStatus.UNKNOWN),
    )

    response = service.evaluate(request)

    assert response.allowed is False
    assert response.decision is OfflineDecision.DENY_UNKNOWN_UNSAFE
    assert response.error_code is ErrorCode.UNKNOWN_UNSAFE
    assert response.output_profile is None
    assert response.reason.strip()


def test_policy_evaluation_service_allows_go_plus_aac_m4a_for_hls_aac_non_drm_source() -> None:
    service = PolicyEvaluationService()
    request = PolicyEvaluationRequest(
        access_mode=AccessMode.GO_PLUS,
        requested_profile=OutputProfile.AAC_M4A,
        is_authenticated=True,
        has_go_plus=True,
        offline_allowed=True,
        source=source(protocol=SourceProtocol.HLS, codec=MediaCodec.AAC),
    )

    response = service.evaluate(request)

    assert response.allowed is True
    assert response.decision is OfflineDecision.ALLOW_AAC_M4A_REMUX
    assert response.error_code is None
    assert response.output_profile is OutputProfile.AAC_M4A
    assert response.reason.strip()


def test_policy_evaluation_service_delegates_to_reconstruction_policy_engine() -> None:
    class RecordingEngine(ReconstructionPolicyEngine):
        def __init__(self) -> None:
            self.context: TrackAccessContext | None = None
            self.requested_profile: OutputProfile | None = None

        def decide(
            self,
            context: TrackAccessContext,
            requested_profile: OutputProfile | None = None,
        ) -> PolicyDecision:
            self.context = context
            self.requested_profile = requested_profile
            return PolicyDecision.deny(
                decision=OfflineDecision.DENY_SOURCE_NOT_DOWNLOADABLE,
                error_code=ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                reason="Recorded delegation.",
            )

    engine = RecordingEngine()
    service = PolicyEvaluationService(engine)
    request = PolicyEvaluationRequest(
        access_mode=AccessMode.PUBLIC,
        requested_profile=OutputProfile.ORIGINAL,
        source=source(is_downloadable=True),
    )

    response = service.evaluate(request)

    assert engine.context == request.to_context()
    assert engine.requested_profile is OutputProfile.ORIGINAL
    assert response.decision is OfflineDecision.DENY_SOURCE_NOT_DOWNLOADABLE
    assert response.error_code is ErrorCode.SOURCE_NOT_DOWNLOADABLE


def test_policy_evaluation_response_from_decision_preserves_all_fields() -> None:
    decision = PolicyDecision.deny(
        decision=OfflineDecision.DENY_DRM,
        error_code=ErrorCode.DRM_UNSUPPORTED,
        reason="DRM-protected streams are denied.",
    )

    response = PolicyEvaluationResponse.from_decision(decision)

    assert response.decision is decision.decision
    assert response.allowed is decision.allowed
    assert response.reason == decision.reason
    assert response.error_code is decision.error_code
    assert response.output_profile is decision.output_profile


def test_policy_evaluation_request_is_immutable() -> None:
    request = PolicyEvaluationRequest(access_mode=AccessMode.PUBLIC)

    with pytest.raises(ValidationError):
        request.is_authenticated = True


def test_policy_evaluation_response_is_immutable() -> None:
    response = PolicyEvaluationResponse.from_decision(
        PolicyDecision.allow(
            decision=OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD,
            output_profile=OutputProfile.ORIGINAL,
            reason="Original download is allowed.",
        )
    )

    with pytest.raises(ValidationError):
        response.allowed = False
