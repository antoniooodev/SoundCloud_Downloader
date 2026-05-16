from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soundcloud_downloader.domain.enums import OfflineDecision, OutputProfile
from soundcloud_downloader.domain.errors import ErrorCode


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: OfflineDecision
    allowed: bool
    reason: str = Field(min_length=1)
    error_code: ErrorCode | None = None
    output_profile: OutputProfile | None = None

    @model_validator(mode="after")
    def validate_decision_state(self) -> Self:
        if self.allowed:
            if self.error_code is not None:
                raise ValueError("Allowed policy decisions must not include an error code.")
            if self.output_profile is None:
                raise ValueError("Allowed policy decisions must include an output profile.")
            return self

        if self.output_profile is not None:
            raise ValueError("Denied policy decisions must not include an output profile.")
        return self

    @classmethod
    def allow(
        cls,
        *,
        decision: OfflineDecision,
        output_profile: OutputProfile,
        reason: str,
    ) -> Self:
        return cls(
            decision=decision,
            allowed=True,
            reason=reason,
            output_profile=output_profile,
        )

    @classmethod
    def deny(
        cls,
        *,
        decision: OfflineDecision,
        error_code: ErrorCode,
        reason: str,
    ) -> Self:
        return cls(
            decision=decision,
            allowed=False,
            reason=reason,
            error_code=error_code,
        )
