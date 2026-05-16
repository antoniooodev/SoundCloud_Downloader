from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from soundcloud_downloader.application.policy_service import (
    PolicyEvaluationRequest,
    PolicyEvaluationResponse,
    PolicyEvaluationService,
)
from soundcloud_downloader.application.stream_analysis_service import (
    StreamAnalysisRequest,
    StreamAnalysisResult,
    StreamAnalysisService,
)
from soundcloud_downloader.domain import (
    AccessMode,
    DRMStatus,
    HLSManifestAnalysis,
    MediaSource,
    OutputProfile,
)


class _StreamAnalysisService(Protocol):
    def analyze(self, request: StreamAnalysisRequest) -> StreamAnalysisResult:
        ...


class _PolicyEvaluationService(Protocol):
    def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResponse:
        ...


class ReconstructionPlanRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_mode: AccessMode
    requested_profile: OutputProfile | None = None
    is_authenticated: bool = False
    has_go_plus: bool = False
    is_public: bool = False
    is_go_plus_track: bool = False
    is_preview_only: bool = False
    is_downloadable: bool = False
    is_own_track: bool = False
    offline_allowed: bool | None = None
    stream: StreamAnalysisRequest


class ReconstructionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: MediaSource
    hls_analysis: HLSManifestAnalysis | None = None
    effective_drm_status: DRMStatus
    policy: PolicyEvaluationResponse
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_stream_analysis_consistency(self) -> Self:
        if self.source.drm_status is not self.effective_drm_status:
            raise ValueError("Plan source DRM status must match the effective DRM status.")
        if (
            self.hls_analysis is not None
            and self.hls_analysis.drm_status is not self.effective_drm_status
        ):
            raise ValueError("Plan HLS analysis DRM status must match the effective DRM status.")
        return self


class ReconstructionPlanner:
    def __init__(
        self,
        stream_analysis_service: _StreamAnalysisService | None = None,
        policy_evaluation_service: _PolicyEvaluationService | None = None,
    ) -> None:
        self._stream_analysis_service = stream_analysis_service or StreamAnalysisService()
        self._policy_evaluation_service = policy_evaluation_service or PolicyEvaluationService()

    def plan(self, request: ReconstructionPlanRequest) -> ReconstructionPlan:
        stream_result = self._stream_analysis_service.analyze(request.stream)
        policy_request = PolicyEvaluationRequest(
            access_mode=request.access_mode,
            requested_profile=request.requested_profile,
            is_authenticated=request.is_authenticated,
            has_go_plus=request.has_go_plus,
            is_public=request.is_public,
            is_go_plus_track=request.is_go_plus_track,
            is_preview_only=request.is_preview_only,
            is_downloadable=request.is_downloadable,
            is_own_track=request.is_own_track,
            offline_allowed=request.offline_allowed,
            source=stream_result.source,
        )
        policy_response = self._policy_evaluation_service.evaluate(policy_request)
        return ReconstructionPlan(
            source=stream_result.source,
            hls_analysis=stream_result.hls_analysis,
            effective_drm_status=stream_result.effective_drm_status,
            policy=policy_response,
            warnings=stream_result.warnings,
        )
