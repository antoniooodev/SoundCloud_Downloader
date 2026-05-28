import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.application import OAuthTokenStore
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    OAuthAccessToken,
    OAuthRefreshToken,
    OAuthTokenProfileId,
    OAuthTokenResponse,
    SoundcloudDownloaderError,
    StoredOAuthTokenSet,
)
from soundcloud_downloader.infrastructure import EncryptedOAuthTokenStore


RAW_ACCESS_TOKEN = "test-access-token-private"
RAW_REFRESH_TOKEN = "test-refresh-token-private"
RAW_PROFILE_ID = "profile-private"


def test_store_init_does_not_create_files(tmp_path: Path) -> None:
    store_path = tmp_path / "nested" / "oauth_tokens.enc"

    _create_store(tmp_path, store_path=store_path)

    assert store_path.exists() is False
    assert store_path.parent.exists() is False


def test_save_persists_encrypted_token_file(tmp_path: Path) -> None:
    store_path = tmp_path / "oauth_tokens.enc"
    store = _create_store(tmp_path, store_path=store_path)

    store.save(_token_set())

    assert store_path.is_file()
    assert store_path.read_bytes() != b""


def test_encrypted_file_does_not_contain_raw_access_token(tmp_path: Path) -> None:
    store_path = _save_token_set_and_return_path(tmp_path)

    assert RAW_ACCESS_TOKEN.encode("utf-8") not in store_path.read_bytes()


def test_encrypted_file_does_not_contain_raw_refresh_token(tmp_path: Path) -> None:
    store_path = _save_token_set_and_return_path(tmp_path)

    assert RAW_REFRESH_TOKEN.encode("utf-8") not in store_path.read_bytes()


def test_encrypted_file_does_not_contain_raw_profile_id(tmp_path: Path) -> None:
    store_path = _save_token_set_and_return_path(tmp_path)

    assert RAW_PROFILE_ID.encode("utf-8") not in store_path.read_bytes()


def test_get_returns_saved_token_set(tmp_path: Path) -> None:
    token_set = _token_set()
    store = _create_store(tmp_path)

    store.save(token_set)

    assert store.get(token_set.profile_id) == token_set


def test_get_missing_profile_returns_none(tmp_path: Path) -> None:
    store = _create_store(tmp_path)

    assert store.get(OAuthTokenProfileId(value="missing-profile")) is None


def test_delete_removes_saved_token_set(tmp_path: Path) -> None:
    token_set = _token_set()
    store = _create_store(tmp_path)
    store.save(token_set)

    store.delete(token_set.profile_id)

    assert store.get(token_set.profile_id) is None


def test_delete_missing_profile_is_idempotent(tmp_path: Path) -> None:
    store = _create_store(tmp_path)
    profile_id = OAuthTokenProfileId(value="missing-profile")

    store.delete(profile_id)
    store.delete(profile_id)

    assert store.get(profile_id) is None


def test_data_survives_new_store_instance_with_same_key_and_path(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode("ascii")
    store_path = tmp_path / "oauth_tokens.enc"
    token_set = _token_set()
    _create_store(tmp_path, store_path=store_path, key=key).save(token_set)

    reloaded_store = _create_store(tmp_path, store_path=store_path, key=key)

    assert reloaded_store.get(token_set.profile_id) == token_set


def test_different_key_cannot_decrypt_existing_store_and_raises_safe_error(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "oauth_tokens.enc"
    token_set = _token_set()
    _create_store(tmp_path, store_path=store_path).save(token_set)
    wrong_key_store = _create_store(
        tmp_path,
        store_path=store_path,
        key=Fernet.generate_key().decode("ascii"),
    )

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        wrong_key_store.get(token_set.profile_id)

    _assert_safe_exception(exc_info.value)


def test_corrupted_file_raises_safe_error(tmp_path: Path) -> None:
    store_path = tmp_path / "oauth_tokens.enc"
    store_path.write_bytes(b"not-a-fernet-token")
    store = _create_store(tmp_path, store_path=store_path)

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        store.get(OAuthTokenProfileId(value=RAW_PROFILE_ID))

    _assert_safe_exception(exc_info.value)


def test_missing_encryption_key_raises_safe_error(tmp_path: Path) -> None:
    settings = AppSettings(
        allow_filesystem_writes=True,
        oauth_token_store_path=tmp_path / "oauth_tokens.enc",
        oauth_token_encryption_key=None,
    )

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        EncryptedOAuthTokenStore(settings)

    _assert_safe_exception(exc_info.value)


def test_allow_filesystem_writes_false_prevents_save(tmp_path: Path) -> None:
    store = _create_store(tmp_path, allow_filesystem_writes=False)

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        store.save(_token_set())

    _assert_safe_exception(exc_info.value)
    assert (tmp_path / "oauth_tokens.enc").exists() is False


def test_get_can_read_existing_store_when_filesystem_writes_are_disabled(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode("ascii")
    store_path = tmp_path / "oauth_tokens.enc"
    token_set = _token_set()
    _create_store(tmp_path, store_path=store_path, key=key).save(token_set)
    read_only_store = _create_store(
        tmp_path,
        store_path=store_path,
        key=key,
        allow_filesystem_writes=False,
    )

    assert read_only_store.get(token_set.profile_id) == token_set


def test_delete_with_filesystem_writes_disabled_fails_closed(tmp_path: Path) -> None:
    store = _create_store(tmp_path, allow_filesystem_writes=False)

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        store.delete(OAuthTokenProfileId(value=RAW_PROFILE_ID))

    _assert_safe_exception(exc_info.value)


def test_save_creates_parent_directories_only_when_saving(tmp_path: Path) -> None:
    store_path = tmp_path / "nested" / "oauth_tokens.enc"
    store = _create_store(tmp_path, store_path=store_path)

    assert store_path.parent.exists() is False

    store.save(_token_set())

    assert store_path.parent.is_dir()
    assert store_path.is_file()


def test_updating_same_profile_overwrites_previous_token_set(tmp_path: Path) -> None:
    token_set = _token_set()
    replacement = _token_set(access_token="replacement-access-token-private")
    store = _create_store(tmp_path)
    store.save(token_set)

    store.save(replacement)

    saved_token_set = store.get(token_set.profile_id)
    assert saved_token_set is not None
    assert saved_token_set.access_token.value.get_secret_value() == "replacement-access-token-private"


def test_store_can_persist_multiple_token_profiles(tmp_path: Path) -> None:
    first_token_set = _token_set(profile_id="profile-one")
    second_token_set = _token_set(profile_id="profile-two", access_token="second-access-token")
    store = _create_store(tmp_path)

    store.save(first_token_set)
    store.save(second_token_set)

    assert store.get(first_token_set.profile_id) == first_token_set
    assert store.get(second_token_set.profile_id) == second_token_set


def test_expires_at_is_preserved(tmp_path: Path) -> None:
    token_set = _token_set(expires_at=_created_at() + timedelta(hours=2))
    store = _create_store(tmp_path)

    store.save(token_set)

    saved_token_set = store.get(token_set.profile_id)
    assert saved_token_set is not None
    assert saved_token_set.expires_at == token_set.expires_at


def test_refresh_token_none_is_preserved(tmp_path: Path) -> None:
    token_set = _token_set(refresh_token=None)
    store = _create_store(tmp_path)

    store.save(token_set)

    saved_token_set = store.get(token_set.profile_id)
    assert saved_token_set is not None
    assert saved_token_set.refresh_token is None


def test_no_raw_secrets_appear_in_exception_messages(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    store_path = tmp_path / "oauth_tokens.enc"
    malformed_payload = {
        "version": 1,
        "profiles": {
            RAW_PROFILE_ID: {
                "profile_id": {"value": RAW_PROFILE_ID},
                "access_token": {"value": RAW_ACCESS_TOKEN},
                "refresh_token": {"value": RAW_REFRESH_TOKEN},
            }
        },
    }
    store_path.write_bytes(Fernet(key).encrypt(json.dumps(malformed_payload).encode("utf-8")))
    store = _create_store(tmp_path, store_path=store_path, key=key.decode("ascii"))

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        store.get(OAuthTokenProfileId(value=RAW_PROFILE_ID))

    _assert_safe_exception(exc_info.value)


def test_settings_rejects_invalid_fernet_token_key(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            oauth_token_store_path=tmp_path / "oauth_tokens.enc",
            oauth_token_encryption_key=SecretStr("not-a-valid-fernet-key"),
        )


def test_store_implements_oauth_token_store_protocol(tmp_path: Path) -> None:
    store = _create_store(tmp_path)

    assert isinstance(store, OAuthTokenStore)


def test_stored_oauth_token_set_from_token_response_computes_expires_at() -> None:
    created_at = _created_at()
    token_set = StoredOAuthTokenSet.from_token_response(
        profile_id=OAuthTokenProfileId(value=RAW_PROFILE_ID),
        token_response=_token_response(expires_in=3600),
        created_at=created_at,
    )

    assert token_set.expires_at == created_at + timedelta(seconds=3600)


def test_stored_oauth_token_set_is_expired_returns_false_before_expiry() -> None:
    token_set = _token_set(expires_at=_created_at() + timedelta(seconds=60))

    assert token_set.is_expired(_created_at() + timedelta(seconds=59)) is False


def test_stored_oauth_token_set_is_expired_returns_true_after_expiry() -> None:
    token_set = _token_set(expires_at=_created_at() + timedelta(seconds=60))

    assert token_set.is_expired(_created_at() + timedelta(seconds=60)) is True


def test_stored_oauth_token_set_repr_and_model_dump_do_not_expose_raw_access_token() -> None:
    token_set = _token_set()
    dumped = token_set.model_dump(mode="json")

    assert RAW_ACCESS_TOKEN not in repr(token_set)
    assert dumped["access_token"]["value"] == "**********"
    assert RAW_ACCESS_TOKEN not in json.dumps(dumped)


def test_stored_oauth_token_set_repr_and_model_dump_do_not_expose_raw_refresh_token() -> None:
    token_set = _token_set()
    dumped = token_set.model_dump(mode="json")

    assert RAW_REFRESH_TOKEN not in repr(token_set)
    assert dumped["refresh_token"]["value"] == "**********"
    assert RAW_REFRESH_TOKEN not in json.dumps(dumped)


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in encrypted OAuth token store tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)
    store = _create_store(tmp_path)

    store.save(_token_set())


def test_tests_write_only_inside_pytest_tmp_path(tmp_path: Path) -> None:
    store_path = tmp_path / "nested" / "oauth_tokens.enc"
    store = _create_store(tmp_path, store_path=store_path)

    store.save(_token_set())

    assert store_path.is_relative_to(tmp_path)
    assert store_path.is_file()


def _create_store(
    tmp_path: Path,
    *,
    store_path: Path | None = None,
    key: str | None = None,
    allow_filesystem_writes: bool = True,
) -> EncryptedOAuthTokenStore:
    settings = AppSettings(
        allow_filesystem_writes=allow_filesystem_writes,
        oauth_token_store_path=store_path or tmp_path / "oauth_tokens.enc",
        oauth_token_encryption_key=SecretStr(key or Fernet.generate_key().decode("ascii")),
    )
    return EncryptedOAuthTokenStore(settings)


def _save_token_set_and_return_path(tmp_path: Path) -> Path:
    store_path = tmp_path / "oauth_tokens.enc"
    store = _create_store(tmp_path, store_path=store_path)
    store.save(_token_set())
    return store_path


def _token_set(
    *,
    profile_id: str = RAW_PROFILE_ID,
    access_token: str = RAW_ACCESS_TOKEN,
    refresh_token: str | None = RAW_REFRESH_TOKEN,
    expires_at: datetime | None = None,
) -> StoredOAuthTokenSet:
    return StoredOAuthTokenSet(
        profile_id=OAuthTokenProfileId(value=profile_id),
        access_token=OAuthAccessToken(value=SecretStr(access_token)),
        refresh_token=(
            OAuthRefreshToken(value=SecretStr(refresh_token))
            if refresh_token is not None
            else None
        ),
        scope="read",
        created_at=_created_at(),
        expires_at=expires_at or _created_at() + timedelta(hours=1),
    )


def _token_response(*, expires_in: int | None) -> OAuthTokenResponse:
    return OAuthTokenResponse(
        access_token=OAuthAccessToken(value=SecretStr(RAW_ACCESS_TOKEN)),
        refresh_token=OAuthRefreshToken(value=SecretStr(RAW_REFRESH_TOKEN)),
        expires_in=expires_in,
        scope="read",
    )


def _created_at() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _assert_safe_exception(error: SoundcloudDownloaderError) -> None:
    error_text = str(error)
    assert RAW_ACCESS_TOKEN not in error_text
    assert RAW_REFRESH_TOKEN not in error_text
