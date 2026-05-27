import socket
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr

from soundcloud_downloader.application import (
    CreateOAuthAuthorizationSessionRequest,
    InMemoryOAuthAuthorizationSessionStore,
    OAuthAuthorizationSessionService,
)
from soundcloud_downloader.domain import (
    OAuthAuthorizationSession,
    OAuthAuthorizationSessionPublic,
    OAuthClientId,
    OAuthRedirectUri,
    OAuthSessionId,
    OAuthSessionStatus,
    OAuthState,
    SoundcloudDownloaderError,
)


def test_create_session_returns_public_dto_with_session_id_and_authorization_url() -> None:
    public_session, _store = _create_public_session_and_store()

    assert public_session.session_id != ""
    assert public_session.authorization_url.startswith("https://secure.soundcloud.com/authorize?")


def test_public_dto_does_not_include_code_verifier() -> None:
    public_session, _store = _create_public_session_and_store()

    assert "code_verifier" not in public_session.model_dump()


def test_public_dto_does_not_include_access_token_or_refresh_token() -> None:
    public_session, _store = _create_public_session_and_store()
    payload = public_session.model_dump()

    assert "access_token" not in payload
    assert "refresh_token" not in payload


def test_private_session_is_saved_in_in_memory_store() -> None:
    public_session, store = _create_public_session_and_store()

    assert store.get(OAuthSessionId(value=public_session.session_id)) is not None


def test_private_session_contains_code_verifier_as_secret_str() -> None:
    session = _create_private_session()

    assert isinstance(session.code_verifier.value, SecretStr)


def test_private_session_repr_does_not_expose_raw_verifier() -> None:
    session = _create_private_session()
    raw_verifier = session.code_verifier.value.get_secret_value()

    assert raw_verifier not in repr(session)


def test_private_session_model_dump_masks_raw_verifier() -> None:
    session = _create_private_session()
    raw_verifier = session.code_verifier.value.get_secret_value()
    dumped = session.model_dump()

    assert dumped["code_verifier"] == {"value": "**********"}
    assert raw_verifier not in str(dumped)


def test_authorization_url_does_not_include_code_verifier() -> None:
    session = _create_private_session()

    assert "code_verifier" not in session.authorization_url


def test_authorization_url_includes_code_challenge_and_state() -> None:
    session = _create_private_session()
    query = parse_qs(urlparse(session.authorization_url).query)

    assert query["code_challenge"] == [session.code_challenge.value]
    assert query["state"] == [session.state.value.get_secret_value()]


def test_session_datetimes_are_timezone_aware_utc() -> None:
    session = _create_private_session()

    assert session.created_at.tzinfo is not None
    assert session.expires_at.tzinfo is not None
    assert session.created_at.utcoffset() == timedelta(0)
    assert session.expires_at.utcoffset() == timedelta(0)


def test_session_is_expired_returns_false_before_expiry() -> None:
    session = _create_private_session()

    assert session.is_expired(session.expires_at - timedelta(seconds=1)) is False


def test_session_is_expired_returns_true_after_expiry() -> None:
    session = _create_private_session()

    assert session.is_expired(session.expires_at) is True


def test_consume_session_succeeds_with_matching_state() -> None:
    session = _create_private_session()
    service, _store = _create_service_with_existing_session(session)

    consumed = service.consume_session(session.session_id, returned_state=session.state)

    assert consumed.session_id == session.session_id


def test_consume_session_marks_session_as_consumed() -> None:
    session = _create_private_session()
    service, store = _create_service_with_existing_session(session)

    consumed = service.consume_session(session.session_id, returned_state=session.state)

    assert consumed.status is OAuthSessionStatus.CONSUMED
    assert store.get(session.session_id).status is OAuthSessionStatus.CONSUMED


def test_consume_session_rejects_wrong_state() -> None:
    session = _create_private_session()
    service, _store = _create_service_with_existing_session(session)

    with pytest.raises(SoundcloudDownloaderError, match="state did not match"):
        service.consume_session(
            session.session_id,
            returned_state=OAuthState(value=SecretStr("wrong-state")),
        )


def test_wrong_state_does_not_consume_session() -> None:
    session = _create_private_session()
    service, store = _create_service_with_existing_session(session)

    with pytest.raises(SoundcloudDownloaderError):
        service.consume_session(
            session.session_id,
            returned_state=OAuthState(value=SecretStr("wrong-state")),
        )

    assert store.get(session.session_id).status is OAuthSessionStatus.PENDING


def test_consume_session_rejects_expired_session() -> None:
    session = _create_private_session()
    service, _store = _create_service_with_existing_session(session)

    with pytest.raises(SoundcloudDownloaderError, match="expired"):
        service.consume_session(
            session.session_id,
            returned_state=session.state,
            now=session.expires_at,
        )


def test_consume_session_rejects_already_consumed_session() -> None:
    session = _create_private_session().mark_consumed()
    service, _store = _create_service_with_existing_session(session)

    with pytest.raises(SoundcloudDownloaderError, match="already been consumed"):
        service.consume_session(session.session_id, returned_state=session.state)


def test_consume_session_rejects_missing_session() -> None:
    service = OAuthAuthorizationSessionService(store=InMemoryOAuthAuthorizationSessionStore())

    with pytest.raises(SoundcloudDownloaderError, match="not found"):
        service.consume_session(
            OAuthSessionId(value="missing-session"),
            returned_state=OAuthState(value=SecretStr("state")),
        )


def test_in_memory_store_delete_is_idempotent() -> None:
    store = InMemoryOAuthAuthorizationSessionStore()
    session_id = OAuthSessionId(value="missing-session")

    store.delete(session_id)
    store.delete(session_id)

    assert store.get(session_id) is None


def test_session_model_is_immutable() -> None:
    session = _create_private_session()

    with pytest.raises(Exception, match="frozen"):
        session.status = OAuthSessionStatus.CONSUMED


def test_public_dto_is_immutable() -> None:
    public_session, _store = _create_public_session_and_store()

    with pytest.raises(Exception, match="frozen"):
        public_session.status = OAuthSessionStatus.CONSUMED


def test_create_oauth_authorization_session_request_rejects_invalid_ttl_seconds() -> None:
    with pytest.raises(ValueError, match="positive"):
        _create_request(ttl_seconds=0)


def test_create_oauth_authorization_session_request_rejects_invalid_verifier_length() -> None:
    with pytest.raises(ValueError, match="between 43 and 128"):
        _create_request(verifier_length=42)


def test_service_uses_injected_store() -> None:
    store = InMemoryOAuthAuthorizationSessionStore()
    service = OAuthAuthorizationSessionService(store=store)

    public_session = service.create_session(_create_request())

    assert store.get(OAuthSessionId(value=public_session.session_id)) is not None


def test_no_test_performs_network_calls(monkeypatch: Any) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in OAuth session tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)

    _create_private_session()


def test_no_test_writes_files(monkeypatch: Any) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("File writes are not allowed in OAuth session tests.")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    _create_private_session()


def _create_public_session_and_store() -> tuple[
    OAuthAuthorizationSessionPublic,
    InMemoryOAuthAuthorizationSessionStore,
]:
    store = InMemoryOAuthAuthorizationSessionStore()
    service = OAuthAuthorizationSessionService(store=store)
    public_session = service.create_session(_create_request())
    return public_session, store


def _create_private_session() -> OAuthAuthorizationSession:
    public_session, store = _create_public_session_and_store()
    session = store.get(OAuthSessionId(value=public_session.session_id))
    assert session is not None
    return session


def _create_service_with_existing_session(
    session: OAuthAuthorizationSession,
) -> tuple[OAuthAuthorizationSessionService, InMemoryOAuthAuthorizationSessionStore]:
    store = InMemoryOAuthAuthorizationSessionStore()
    store.save(session)
    return OAuthAuthorizationSessionService(store=store), store


def _create_request(
    *,
    verifier_length: int = 64,
    state_length: int = 32,
    ttl_seconds: int = 600,
) -> CreateOAuthAuthorizationSessionRequest:
    return CreateOAuthAuthorizationSessionRequest(
        client_id=OAuthClientId(value=SecretStr("client-id")),
        redirect_uri=OAuthRedirectUri(value="http://localhost:8765/callback"),
        auth_base_url="https://secure.soundcloud.com",
        verifier_length=verifier_length,
        state_length=state_length,
        ttl_seconds=ttl_seconds,
    )
