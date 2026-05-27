import secrets
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator

from soundcloud_downloader.application.oauth_pkce import OAuthPKCEService
from soundcloud_downloader.domain.errors import ErrorCode, SoundcloudDownloaderError
from soundcloud_downloader.domain.oauth import OAuthClientId, OAuthRedirectUri, OAuthState
from soundcloud_downloader.domain.oauth_session import (
    OAuthAuthorizationSession,
    OAuthAuthorizationSessionPublic,
    OAuthSessionId,
    OAuthSessionStatus,
)


@runtime_checkable
class OAuthAuthorizationSessionStore(Protocol):
    def save(self, session: OAuthAuthorizationSession) -> None:
        ...

    def get(self, session_id: OAuthSessionId) -> OAuthAuthorizationSession | None:
        ...

    def delete(self, session_id: OAuthSessionId) -> None:
        ...


class InMemoryOAuthAuthorizationSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, OAuthAuthorizationSession] = {}

    def save(self, session: OAuthAuthorizationSession) -> None:
        self._sessions[session.session_id.value] = session

    def get(self, session_id: OAuthSessionId) -> OAuthAuthorizationSession | None:
        return self._sessions.get(session_id.value)

    def delete(self, session_id: OAuthSessionId) -> None:
        self._sessions.pop(session_id.value, None)


class CreateOAuthAuthorizationSessionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    client_id: OAuthClientId
    redirect_uri: OAuthRedirectUri
    auth_base_url: str
    verifier_length: int = 64
    state_length: int = 32
    ttl_seconds: int = 600

    @field_validator("verifier_length")
    @classmethod
    def validate_verifier_length(cls, value: int) -> int:
        if not 43 <= value <= 128:
            raise ValueError("OAuth code verifier length must be between 43 and 128 characters.")
        return value

    @field_validator("state_length", "ttl_seconds")
    @classmethod
    def validate_positive_integer(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("OAuth session numeric settings must be positive.")
        return value


class OAuthAuthorizationSessionService:
    def __init__(
        self,
        pkce_service: OAuthPKCEService | None = None,
        store: OAuthAuthorizationSessionStore | None = None,
    ) -> None:
        self._pkce_service = pkce_service if pkce_service is not None else OAuthPKCEService()
        self._store = store if store is not None else InMemoryOAuthAuthorizationSessionStore()

    def create_session(
        self,
        request: CreateOAuthAuthorizationSessionRequest,
    ) -> OAuthAuthorizationSessionPublic:
        code_verifier = self._pkce_service.generate_code_verifier(request.verifier_length)
        code_challenge = self._pkce_service.derive_s256_challenge(code_verifier)
        state = self._pkce_service.generate_state(request.state_length)
        authorization_request = self._pkce_service.build_authorization_request(
            auth_base_url=request.auth_base_url,
            client_id=request.client_id,
            redirect_uri=request.redirect_uri,
            code_challenge=code_challenge,
            state=state,
        )
        created_at = datetime.now(timezone.utc)
        session = OAuthAuthorizationSession(
            session_id=OAuthSessionId(value=str(uuid4())),
            client_id=request.client_id,
            redirect_uri=request.redirect_uri,
            authorization_url=authorization_request.authorization_url,
            code_verifier=code_verifier,
            state=state,
            code_challenge=code_challenge,
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=request.ttl_seconds),
        )
        self._store.save(session)
        return OAuthAuthorizationSessionPublic.from_session(session)

    def get_session(
        self,
        session_id: OAuthSessionId,
    ) -> OAuthAuthorizationSession | None:
        return self._store.get(session_id)

    def consume_session(
        self,
        session_id: OAuthSessionId,
        *,
        returned_state: OAuthState,
        now: datetime | None = None,
    ) -> OAuthAuthorizationSession:
        session = self._store.get(session_id)
        if session is None:
            raise SoundcloudDownloaderError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth authorization session was not found.",
            )
        if session.status is OAuthSessionStatus.CONSUMED:
            raise SoundcloudDownloaderError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth authorization session has already been consumed.",
            )
        if session.is_expired(now):
            expired_session = session.mark_expired()
            self._store.save(expired_session)
            raise SoundcloudDownloaderError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth authorization session has expired.",
            )
        if not secrets.compare_digest(
            returned_state.value.get_secret_value(),
            session.state.value.get_secret_value(),
        ):
            raise SoundcloudDownloaderError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth authorization state did not match.",
            )

        consumed_session = session.mark_consumed()
        self._store.save(consumed_session)
        return consumed_session
