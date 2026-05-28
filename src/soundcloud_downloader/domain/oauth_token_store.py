from datetime import datetime, timedelta, timezone
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from soundcloud_downloader.domain.oauth_token import (
    OAuthAccessToken,
    OAuthRefreshToken,
    OAuthTokenResponse,
)


class OAuthTokenProfileId(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str

    @field_validator("value")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if value == "":
            raise ValueError("OAuth token profile ID must not be empty.")
        return value


class StoredOAuthTokenSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: OAuthTokenProfileId
    access_token: OAuthAccessToken
    refresh_token: OAuthRefreshToken | None = None
    token_type: str = "OAuth"
    scope: str | None = None
    created_at: datetime
    expires_at: datetime | None = None

    @field_validator("created_at", "expires_at")
    @classmethod
    def validate_utc_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("Stored OAuth token datetimes must be timezone-aware UTC.")
        return value

    @field_validator("token_type")
    @classmethod
    def validate_token_type(cls, value: str) -> str:
        if value != "OAuth":
            raise ValueError("Stored OAuth token type must be 'OAuth'.")
        return value

    @model_validator(mode="after")
    def validate_expiry_order(self) -> Self:
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("Stored OAuth token expiry must be after creation time.")
        return self

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        checked_at = now if now is not None else datetime.now(timezone.utc)
        if checked_at.tzinfo is None or checked_at.utcoffset() != timezone.utc.utcoffset(checked_at):
            raise ValueError("Stored OAuth token expiry checks require timezone-aware UTC.")
        return checked_at >= self.expires_at

    @classmethod
    def from_token_response(
        cls,
        *,
        profile_id: OAuthTokenProfileId,
        token_response: OAuthTokenResponse,
        created_at: datetime | None = None,
    ) -> "StoredOAuthTokenSet":
        effective_created_at = created_at if created_at is not None else datetime.now(timezone.utc)
        expires_at = (
            effective_created_at + timedelta(seconds=token_response.expires_in)
            if token_response.expires_in is not None
            else None
        )
        return cls(
            profile_id=profile_id,
            access_token=token_response.access_token,
            refresh_token=token_response.refresh_token,
            token_type=token_response.access_token.token_type,
            scope=token_response.scope,
            created_at=effective_created_at,
            expires_at=expires_at,
        )
