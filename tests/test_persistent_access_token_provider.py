import asyncio
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from soundcloud_downloader.application.ports import AccessTokenProviderPort
from soundcloud_downloader.domain import (
    OAuthAccessToken,
    OAuthRefreshToken,
    OAuthTokenProfileId,
    SoundcloudDownloaderError,
    StoredOAuthTokenSet,
)
from soundcloud_downloader.infrastructure import PersistentAccessTokenProvider
from soundcloud_downloader.infrastructure.soundcloud import SoundCloudAccessToken


RAW_ACCESS_TOKEN = "provider-access-token-private"
RAW_REFRESH_TOKEN = "provider-refresh-token-private"


class InMemoryOAuthTokenStore:
    def __init__(self) -> None:
        self.token_sets: dict[str, StoredOAuthTokenSet] = {}

    def save(self, token_set: StoredOAuthTokenSet) -> None:
        self.token_sets[token_set.profile_id.value] = token_set

    def get(self, profile_id: OAuthTokenProfileId) -> StoredOAuthTokenSet | None:
        return self.token_sets.get(profile_id.value)

    def delete(self, profile_id: OAuthTokenProfileId) -> None:
        self.token_sets.pop(profile_id.value, None)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_provider_satisfies_access_token_provider_port() -> None:
    provider = PersistentAccessTokenProvider(InMemoryOAuthTokenStore())

    assert isinstance(provider, AccessTokenProviderPort)


def test_provider_returns_stored_access_token_when_token_exists_and_is_not_expired() -> None:
    store = InMemoryOAuthTokenStore()
    store.save(_token_set())
    provider = PersistentAccessTokenProvider(store)

    token = run(provider.get_access_token())

    assert isinstance(token, SoundCloudAccessToken)
    assert token.value.get_secret_value() == RAW_ACCESS_TOKEN


def test_provider_maps_oauth_access_token_to_soundcloud_access_token_type() -> None:
    store = InMemoryOAuthTokenStore()
    store.save(_token_set())
    provider = PersistentAccessTokenProvider(store)

    token = run(provider.get_access_token())

    assert isinstance(token, SoundCloudAccessToken)
    assert token.token_type == "OAuth"


def test_provider_fails_closed_when_token_is_missing() -> None:
    provider = PersistentAccessTokenProvider(InMemoryOAuthTokenStore())

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(provider.get_access_token())

    _assert_safe_exception(exc_info.value)


def test_provider_fails_closed_when_token_is_expired() -> None:
    store = InMemoryOAuthTokenStore()
    store.save(_token_set(expires_at=_created_at() - timedelta(seconds=1)))
    provider = PersistentAccessTokenProvider(store)

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(provider.get_access_token())

    assert "Refresh is not implemented yet" in str(exc_info.value)
    _assert_safe_exception(exc_info.value)


def test_provider_does_not_expose_raw_access_token_in_exception_messages() -> None:
    store = InMemoryOAuthTokenStore()
    store.save(_token_set(expires_at=_created_at() - timedelta(seconds=1)))
    provider = PersistentAccessTokenProvider(store)

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(provider.get_access_token())

    _assert_safe_exception(exc_info.value)


def test_provider_does_not_expose_raw_refresh_token_in_exception_messages() -> None:
    store = InMemoryOAuthTokenStore()
    store.save(_token_set(expires_at=_created_at() - timedelta(seconds=1)))
    provider = PersistentAccessTokenProvider(store)

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(provider.get_access_token())

    _assert_safe_exception(exc_info.value)


def test_provider_does_not_implement_refresh() -> None:
    store = InMemoryOAuthTokenStore()
    store.save(_token_set(expires_at=_created_at() - timedelta(seconds=1)))
    provider = PersistentAccessTokenProvider(store)

    with pytest.raises(SoundcloudDownloaderError, match="Refresh is not implemented yet"):
        run(provider.get_access_token())


def test_provider_uses_default_profile_id() -> None:
    store = InMemoryOAuthTokenStore()
    store.save(_token_set(profile_id="default"))
    provider = PersistentAccessTokenProvider(store)

    token = run(provider.get_access_token())

    assert token.value.get_secret_value() == RAW_ACCESS_TOKEN


def test_provider_supports_custom_profile_id() -> None:
    store = InMemoryOAuthTokenStore()
    store.save(_token_set(profile_id="custom-profile", access_token="custom-access-token"))
    provider = PersistentAccessTokenProvider(
        store,
        profile_id=OAuthTokenProfileId(value="custom-profile"),
    )

    token = run(provider.get_access_token())

    assert token.value.get_secret_value() == "custom-access-token"


def test_tests_perform_no_network_calls(monkeypatch: Any) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in persistent token provider tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)
    store = InMemoryOAuthTokenStore()
    store.save(_token_set())
    provider = PersistentAccessTokenProvider(store)

    assert run(provider.get_access_token()).value.get_secret_value() == RAW_ACCESS_TOKEN


def test_tests_write_no_files_unless_using_tmp_path_store_explicitly(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("File writes are not allowed in persistent token provider tests.")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)
    store = InMemoryOAuthTokenStore()
    store.save(_token_set())
    provider = PersistentAccessTokenProvider(store)

    assert tmp_path.exists()
    assert run(provider.get_access_token()).value.get_secret_value() == RAW_ACCESS_TOKEN


def _token_set(
    *,
    profile_id: str = "default",
    access_token: str = RAW_ACCESS_TOKEN,
    expires_at: datetime | None = None,
) -> StoredOAuthTokenSet:
    effective_expires_at = expires_at or _created_at() + timedelta(hours=1)
    effective_created_at = (
        effective_expires_at - timedelta(hours=1)
        if effective_expires_at <= _created_at()
        else _created_at()
    )
    return StoredOAuthTokenSet(
        profile_id=OAuthTokenProfileId(value=profile_id),
        access_token=OAuthAccessToken(value=SecretStr(access_token)),
        refresh_token=OAuthRefreshToken(value=SecretStr(RAW_REFRESH_TOKEN)),
        created_at=effective_created_at,
        expires_at=effective_expires_at,
    )


def _created_at() -> datetime:
    return datetime.now(timezone.utc)


def _assert_safe_exception(error: SoundcloudDownloaderError) -> None:
    error_text = str(error)
    assert RAW_ACCESS_TOKEN not in error_text
    assert RAW_REFRESH_TOKEN not in error_text
