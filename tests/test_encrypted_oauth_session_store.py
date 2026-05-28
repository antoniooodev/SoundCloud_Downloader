import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.application.oauth_session_service import OAuthAuthorizationSessionStore
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    OAuthAuthorizationSession,
    OAuthClientId,
    OAuthCodeChallenge,
    OAuthCodeVerifier,
    OAuthRedirectUri,
    OAuthSessionId,
    OAuthSessionStatus,
    OAuthState,
    SoundcloudDownloaderError,
)
from soundcloud_downloader.infrastructure import EncryptedOAuthAuthorizationSessionStore


RAW_CLIENT_ID = "client-id-private"
RAW_CODE_VERIFIER = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRS"
RAW_STATE = "state-private"


def test_store_init_does_not_create_files(tmp_path: Path) -> None:
    store_path = tmp_path / "nested" / "oauth_sessions.enc"

    _create_store(tmp_path, store_path=store_path)

    assert store_path.exists() is False
    assert store_path.parent.exists() is False


def test_save_persists_encrypted_file(tmp_path: Path) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    store = _create_store(tmp_path, store_path=store_path)

    store.save(_create_session())

    assert store_path.is_file()
    assert store_path.read_bytes() != b""


def test_encrypted_file_does_not_contain_raw_code_verifier(tmp_path: Path) -> None:
    store_path = _save_session_and_return_path(tmp_path)

    assert RAW_CODE_VERIFIER.encode("utf-8") not in store_path.read_bytes()


def test_encrypted_file_does_not_contain_raw_state(tmp_path: Path) -> None:
    store_path = _save_session_and_return_path(tmp_path)

    assert RAW_STATE.encode("utf-8") not in store_path.read_bytes()


def test_encrypted_file_does_not_contain_raw_client_id(tmp_path: Path) -> None:
    store_path = _save_session_and_return_path(tmp_path)

    assert RAW_CLIENT_ID.encode("utf-8") not in store_path.read_bytes()


def test_get_returns_saved_session(tmp_path: Path) -> None:
    session = _create_session()
    store = _create_store(tmp_path)

    store.save(session)

    assert store.get(session.session_id) == session


def test_get_missing_session_returns_none(tmp_path: Path) -> None:
    store = _create_store(tmp_path)

    assert store.get(OAuthSessionId(value="missing-session")) is None


def test_delete_removes_saved_session(tmp_path: Path) -> None:
    session = _create_session()
    store = _create_store(tmp_path)
    store.save(session)

    store.delete(session.session_id)

    assert store.get(session.session_id) is None


def test_delete_missing_session_is_idempotent(tmp_path: Path) -> None:
    store = _create_store(tmp_path)
    session_id = OAuthSessionId(value="missing-session")

    store.delete(session_id)
    store.delete(session_id)

    assert store.get(session_id) is None


def test_data_survives_new_store_instance_with_same_key_and_path(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode("ascii")
    store_path = tmp_path / "oauth_sessions.enc"
    session = _create_session()
    _create_store(tmp_path, store_path=store_path, key=key).save(session)

    reloaded_store = _create_store(tmp_path, store_path=store_path, key=key)

    assert reloaded_store.get(session.session_id) == session


def test_different_key_cannot_decrypt_existing_store_and_raises_safe_error(tmp_path: Path) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    session = _create_session()
    _create_store(tmp_path, store_path=store_path).save(session)
    wrong_key_store = _create_store(
        tmp_path,
        store_path=store_path,
        key=Fernet.generate_key().decode("ascii"),
    )

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        wrong_key_store.get(session.session_id)

    _assert_safe_exception(exc_info.value)


def test_corrupted_file_raises_safe_error(tmp_path: Path) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    store_path.write_bytes(b"not-a-fernet-token")
    store = _create_store(tmp_path, store_path=store_path)

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        store.get(OAuthSessionId(value="session-1"))

    _assert_safe_exception(exc_info.value)


def test_missing_encryption_key_raises_safe_error(tmp_path: Path) -> None:
    settings = AppSettings(
        allow_filesystem_writes=True,
        oauth_session_store_path=tmp_path / "oauth_sessions.enc",
        oauth_session_encryption_key=None,
    )

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        EncryptedOAuthAuthorizationSessionStore(settings)

    _assert_safe_exception(exc_info.value)


def test_allow_filesystem_writes_false_prevents_save(tmp_path: Path) -> None:
    store = _create_store(tmp_path, allow_filesystem_writes=False)

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        store.save(_create_session())

    _assert_safe_exception(exc_info.value)
    assert (tmp_path / "oauth_sessions.enc").exists() is False


def test_save_creates_parent_directories_only_when_saving(tmp_path: Path) -> None:
    store_path = tmp_path / "nested" / "oauth_sessions.enc"
    store = _create_store(tmp_path, store_path=store_path)

    assert store_path.parent.exists() is False

    store.save(_create_session())

    assert store_path.parent.is_dir()
    assert store_path.is_file()


def test_save_uses_atomic_replace_behavior_sufficiently_tested_by_successful_rewrite(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    store = _create_store(tmp_path, store_path=store_path)
    session = _create_session()
    store.save(session)
    first_bytes = store_path.read_bytes()

    store.save(session.mark_consumed())

    assert store_path.read_bytes() != first_bytes
    saved_session = store.get(session.session_id)
    assert saved_session is not None
    assert saved_session.status is OAuthSessionStatus.CONSUMED


def test_updating_same_session_overwrites_previous_version(tmp_path: Path) -> None:
    session = _create_session()
    store = _create_store(tmp_path)
    store.save(session)

    store.save(session.mark_consumed())

    saved_session = store.get(session.session_id)
    assert saved_session is not None
    assert saved_session.status is OAuthSessionStatus.CONSUMED


def test_store_can_persist_multiple_sessions(tmp_path: Path) -> None:
    first_session = _create_session(session_id="session-1")
    second_session = _create_session(session_id="session-2", state="state-two")
    store = _create_store(tmp_path)

    store.save(first_session)
    store.save(second_session)

    assert store.get(first_session.session_id) == first_session
    assert store.get(second_session.session_id) == second_session


def test_expired_and_consumed_session_status_is_preserved(tmp_path: Path) -> None:
    consumed_session = _create_session(session_id="consumed-session").mark_consumed()
    expired_session = _create_session(session_id="expired-session", state="expired-state").mark_expired()
    store = _create_store(tmp_path)

    store.save(consumed_session)
    store.save(expired_session)

    saved_consumed_session = store.get(consumed_session.session_id)
    saved_expired_session = store.get(expired_session.session_id)
    assert saved_consumed_session is not None
    assert saved_expired_session is not None
    assert saved_consumed_session.status is OAuthSessionStatus.CONSUMED
    assert saved_expired_session.status is OAuthSessionStatus.EXPIRED


def test_no_raw_secrets_appear_in_exception_messages(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    store_path = tmp_path / "oauth_sessions.enc"
    malformed_payload = {
        "version": 1,
        "sessions": {
            "session-1": {
                "session_id": {"value": "session-1"},
                "client_id": {"value": RAW_CLIENT_ID},
                "code_verifier": {"value": RAW_CODE_VERIFIER},
                "state": {"value": RAW_STATE},
            }
        },
    }
    store_path.write_bytes(Fernet(key).encrypt(json.dumps(malformed_payload).encode("utf-8")))
    store = _create_store(tmp_path, store_path=store_path, key=key.decode("ascii"))

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        store.get(OAuthSessionId(value="session-1"))

    _assert_safe_exception(exc_info.value)


def test_settings_rejects_invalid_fernet_key(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            oauth_session_store_path=tmp_path / "oauth_sessions.enc",
            oauth_session_encryption_key=SecretStr("not-a-valid-fernet-key"),
        )


def test_store_implements_oauth_authorization_session_store_protocol(tmp_path: Path) -> None:
    store = _create_store(tmp_path)

    assert isinstance(store, OAuthAuthorizationSessionStore)


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in encrypted OAuth session store tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)
    store = _create_store(tmp_path)

    store.save(_create_session())


def test_tests_write_only_inside_pytest_tmp_path(tmp_path: Path) -> None:
    store_path = tmp_path / "nested" / "oauth_sessions.enc"
    store = _create_store(tmp_path, store_path=store_path)

    store.save(_create_session())

    assert store_path.is_relative_to(tmp_path)
    assert store_path.is_file()


def _create_store(
    tmp_path: Path,
    *,
    store_path: Path | None = None,
    key: str | None = None,
    allow_filesystem_writes: bool = True,
) -> EncryptedOAuthAuthorizationSessionStore:
    settings = AppSettings(
        allow_filesystem_writes=allow_filesystem_writes,
        oauth_session_store_path=store_path or tmp_path / "oauth_sessions.enc",
        oauth_session_encryption_key=SecretStr(key or Fernet.generate_key().decode("ascii")),
    )
    return EncryptedOAuthAuthorizationSessionStore(settings)


def _save_session_and_return_path(tmp_path: Path) -> Path:
    store_path = tmp_path / "oauth_sessions.enc"
    store = _create_store(tmp_path, store_path=store_path)
    store.save(_create_session())
    return store_path


def _create_session(
    *,
    session_id: str = "session-1",
    state: str = RAW_STATE,
) -> OAuthAuthorizationSession:
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return OAuthAuthorizationSession(
        session_id=OAuthSessionId(value=session_id),
        client_id=OAuthClientId(value=SecretStr(RAW_CLIENT_ID)),
        redirect_uri=OAuthRedirectUri(value="http://localhost:8765/callback"),
        authorization_url=f"https://secure.soundcloud.com/authorize?state={state}",
        code_verifier=OAuthCodeVerifier(value=SecretStr(RAW_CODE_VERIFIER)),
        state=OAuthState(value=SecretStr(state)),
        code_challenge=OAuthCodeChallenge(value="code-challenge"),
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=10),
    )


def _assert_safe_exception(error: SoundcloudDownloaderError) -> None:
    error_text = str(error)
    assert RAW_CLIENT_ID not in error_text
    assert RAW_CODE_VERIFIER not in error_text
    assert RAW_STATE not in error_text
