from pydantic import BaseModel, ConfigDict, Field

from soundcloud_downloader.domain import (
    AccessMode,
    ErrorCode,
    MediaSource,
    OfflineDecision,
    OutputProfile,
    PolicyDecision,
    ReconstructionPolicyEngine,
    TrackAccessContext,
)


class PolicyEvaluationRequest(BaseModel):
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
    source: MediaSource | None = None

    def to_context(self) -> TrackAccessContext:
        return TrackAccessContext(
            access_mode=self.access_mode,
            is_authenticated=self.is_authenticated,
            has_go_plus=self.has_go_plus,
            is_public=self.is_public,
            is_go_plus_track=self.is_go_plus_track,
            is_preview_only=self.is_preview_only,
            is_downloadable=self.is_downloadable,
            is_own_track=self.is_own_track,
            offline_allowed=self.offline_allowed,
            source=self.source,
        )


class PolicyEvaluationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: OfflineDecision
    allowed: bool
    reason: str = Field(min_length=1)
    error_code: ErrorCode | None = None
    output_profile: OutputProfile | None = None

    @classmethod
    def from_decision(cls, decision: PolicyDecision) -> "PolicyEvaluationResponse":
        return cls(
            decision=decision.decision,
            allowed=decision.allowed,
            reason=decision.reason,
            error_code=decision.error_code,
            output_profile=decision.output_profile,
        )


class PolicyEvaluationService:
    def __init__(self, engine: ReconstructionPolicyEngine | None = None) -> None:
        self._engine = engine or ReconstructionPolicyEngine()

    def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResponse:
        decision = self._engine.decide(
            request.to_context(),
            request.requested_profile,
        )
        return PolicyEvaluationResponse.from_decision(decision)
