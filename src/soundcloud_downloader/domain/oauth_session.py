from datetime import datetime, timezone
from enum import Enum
from typing import Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from soundcloud_downloader.domain.oauth import (
    OAuthClientId,
    OAuthCodeChallenge,
    OAuthCodeVerifier,
    OAuthRedirectUri,
    OAuthState,
)


SENSITIVE_AUTHORIZATION_URL_FIELDS = (
    "code_verifier",
    "access_token",
    "refresh_token",
    "client_secret",
)


class OAuthSessionStatus(str, Enum):
    PENDING = "pending"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class OAuthSessionId(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str

    @field_validator("value")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if value == "":
            raise ValueError("OAuth session ID must not be empty.")
        return value


class OAuthAuthorizationSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: OAuthSessionId
    client_id: OAuthClientId
    redirect_uri: OAuthRedirectUri
    authorization_url: str
    code_verifier: OAuthCodeVerifier
    state: OAuthState
    code_challenge: OAuthCodeChallenge
    created_at: datetime
    expires_at: datetime
    status: OAuthSessionStatus = OAuthSessionStatus.PENDING

    @field_validator("authorization_url")
    @classmethod
    def validate_authorization_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if value == "" or parsed.scheme not in {"http", "https"} or parsed.netloc == "":
            raise ValueError("OAuth authorization session URL must be an absolute http or https URL.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("OAuth authorization session URL must not contain userinfo credentials.")
        lowered_url = value.lower()
        for sensitive_field in SENSITIVE_AUTHORIZATION_URL_FIELDS:
            if sensitive_field in lowered_url:
                raise ValueError("OAuth authorization session URL contains a forbidden sensitive field.")
        return value

    @field_validator("created_at", "expires_at")
    @classmethod
    def validate_utc_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("OAuth authorization session datetimes must be timezone-aware UTC.")
        return value

    @model_validator(mode="after")
    def validate_expiry_order(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("OAuth authorization session expiry must be after creation time.")
        return self

    def is_expired(self, now: datetime | None = None) -> bool:
        checked_at = now if now is not None else datetime.now(timezone.utc)
        if checked_at.tzinfo is None or checked_at.utcoffset() != timezone.utc.utcoffset(checked_at):
            raise ValueError("OAuth authorization session expiry checks require timezone-aware UTC.")
        return checked_at >= self.expires_at or self.status is OAuthSessionStatus.EXPIRED

    def mark_consumed(self) -> "OAuthAuthorizationSession":
        return self.model_copy(update={"status": OAuthSessionStatus.CONSUMED})

    def mark_expired(self) -> "OAuthAuthorizationSession":
        return self.model_copy(update={"status": OAuthSessionStatus.EXPIRED})


class OAuthAuthorizationSessionPublic(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    authorization_url: str
    expires_at: datetime
    status: OAuthSessionStatus
    code_verifier_required_for_token_exchange: bool = True

    @classmethod
    def from_session(
        cls,
        session: OAuthAuthorizationSession,
    ) -> "OAuthAuthorizationSessionPublic":
        return cls(
            session_id=session.session_id.value,
            authorization_url=session.authorization_url,
            expires_at=session.expires_at,
            status=session.status,
        )
