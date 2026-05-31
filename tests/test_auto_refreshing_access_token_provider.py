import asyncio
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from soundcloud_downloader.application.ports import (
    AccessTokenProviderError,
    AccessTokenProviderFailureReason,
    AccessTokenProviderPort,
    OAuthRefreshTokenPort,
)
from soundcloud_downloader.domain import (
    ErrorCode,
    OAuthAccessToken,
    OAuthClientId,
    OAuthClientSecret,
    OAuthRefreshToken,
    OAuthTokenProfileId,
    OAuthTokenResponse,
    SoundcloudDownloaderError,
    StoredOAuthTokenSet,
)
from soundcloud_downloader.infrastructure.oauth import AutoRefreshingAccessTokenProvider
from soundcloud_downloader.infrastructure.soundcloud import SoundCloudAccessToken


RAW_STORED_ACCESS_TOKEN = "stored-access-token-private"
RAW_OLD_REFRESH_TOKEN = "old-refresh-token-private"
RAW_REFRESHED_ACCESS_TOKEN = "refreshed-access-token-private"
RAW_NEW_REFRESH_TOKEN = "new-refresh-token-private"
RAW_CLIENT_SECRET = "client-secret-private"


class FakeOAuthTokenStore:
    def __init__(self) -> None:
        self.items: dict[str, StoredOAuthTokenSet] = {}
        self.saved: list[StoredOAuthTokenSet] = []
        self.fail_save = False

    def save(self, token_set: StoredOAuthTokenSet) -> None:
        if self.fail_save:
            raise RuntimeError("save failed without raw secrets")
        self.items[token_set.profile_id.value] = token_set
        self.saved.append(token_set)

    def get(self, profile_id: OAuthTokenProfileId) -> StoredOAuthTokenSet | None:
        return self.items.get(profile_id.value)

    def delete(self, profile_id: OAuthTokenProfileId) -> None:
        self.items.pop(profile_id.value, None)


class FakeRefreshService:
    def __init__(self, response: OAuthTokenResponse | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.response = response or _token_response()
        self.fail = False

    async def refresh_access_token(
        self,
        *,
        client_id: OAuthClientId,
        client_secret: OAuthClientSecret,
        refresh_token: OAuthRefreshToken,
    ) -> OAuthTokenResponse:
        self.calls.append(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }
        )
        if self.fail:
            raise SoundcloudDownloaderError(
                ErrorCode.NETWORK_PERMANENT,
                "OAuth refresh failed safely.",
            )
        return self.response


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_provider_satisfies_access_token_provider_port() -> None:
    provider = _provider(FakeOAuthTokenStore(), FakeRefreshService())

    assert isinstance(provider, AccessTokenProviderPort)


def test_provider_returns_stored_access_token_when_not_expired() -> None:
    store = _store_with(_token_set(expires_at=_future(seconds=3600)))
    refresh_service = FakeRefreshService()

    token = run(_provider(store, refresh_service).get_access_token())

    assert token.value.get_secret_value() == RAW_STORED_ACCESS_TOKEN


def test_provider_does_not_call_refresh_service_when_token_is_not_expired() -> None:
    store = _store_with(_token_set(expires_at=_future(seconds=3600)))
    refresh_service = FakeRefreshService()

    run(_provider(store, refresh_service).get_access_token())

    assert refresh_service.calls == []


def test_provider_treats_expires_at_none_as_valid_and_does_not_refresh() -> None:
    store = _store_with(_token_set(expires_at=None))
    refresh_service = FakeRefreshService()

    token = run(_provider(store, refresh_service).get_access_token())

    assert token.value.get_secret_value() == RAW_STORED_ACCESS_TOKEN
    assert refresh_service.calls == []


def test_provider_refreshes_when_token_is_expired() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    refresh_service = FakeRefreshService()

    run(_provider(store, refresh_service).get_access_token())

    assert len(refresh_service.calls) == 1


def test_provider_refreshes_when_token_expires_within_skew_window() -> None:
    store = _store_with(_token_set(expires_at=_future(seconds=30)))
    refresh_service = FakeRefreshService()

    run(_provider(store, refresh_service, refresh_skew_seconds=60).get_access_token())

    assert len(refresh_service.calls) == 1


def test_provider_does_not_refresh_when_token_expires_outside_skew_window() -> None:
    store = _store_with(_token_set(expires_at=_future(seconds=120)))
    refresh_service = FakeRefreshService()

    run(_provider(store, refresh_service, refresh_skew_seconds=60).get_access_token())

    assert refresh_service.calls == []


def test_provider_saves_refreshed_token_set_before_returning_token() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    refresh_service = FakeRefreshService()

    token = run(_provider(store, refresh_service).get_access_token())

    assert store.saved != []
    assert store.saved[-1].access_token.value.get_secret_value() == token.value.get_secret_value()


def test_provider_returns_refreshed_access_token_after_refresh() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    refresh_service = FakeRefreshService()

    token = run(_provider(store, refresh_service).get_access_token())

    assert token.value.get_secret_value() == RAW_REFRESHED_ACCESS_TOKEN


def test_provider_replaces_old_refresh_token_with_new_refresh_token_from_response() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    refresh_service = FakeRefreshService()

    run(_provider(store, refresh_service).get_access_token())

    saved_token_set = store.saved[-1]
    assert saved_token_set.refresh_token is not None
    assert saved_token_set.refresh_token.value.get_secret_value() == RAW_NEW_REFRESH_TOKEN


def test_provider_keeps_old_refresh_token_when_refresh_response_omits_refresh_token() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    refresh_service = FakeRefreshService(response=_token_response(refresh_token=None))

    run(_provider(store, refresh_service).get_access_token())

    saved_token_set = store.saved[-1]
    assert saved_token_set.refresh_token is not None
    assert saved_token_set.refresh_token.value.get_secret_value() == RAW_OLD_REFRESH_TOKEN


def test_future_refresh_reuses_preserved_refresh_token_when_response_omits_refresh_token() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    refresh_service = FakeRefreshService(response=_token_response(refresh_token=None, expires_in=1))
    provider = _provider(store, refresh_service)
    run(provider.get_access_token())
    stored_with_preserved_refresh = store.saved[-1]
    store.items["default"] = stored_with_preserved_refresh.model_copy(
        update={"expires_at": _past(seconds=1)}
    )

    run(provider.get_access_token())

    assert len(refresh_service.calls) == 2
    assert refresh_service.calls[-1]["refresh_token"] == stored_with_preserved_refresh.refresh_token


def test_provider_persists_expires_at_from_refreshed_expires_in() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    refresh_service = FakeRefreshService(response=_token_response(expires_in=3599))
    before_refresh = _now()

    run(_provider(store, refresh_service).get_access_token())

    saved_token_set = store.saved[-1]
    assert saved_token_set.expires_at is not None
    assert saved_token_set.expires_at > before_refresh + timedelta(seconds=3500)
    assert saved_token_set.expires_at < before_refresh + timedelta(seconds=3700)
    assert saved_token_set.is_expired() is False


def test_provider_fails_closed_when_token_is_missing() -> None:
    provider = _provider(FakeOAuthTokenStore(), FakeRefreshService())

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(provider.get_access_token())

    _assert_safe_exception(exc_info.value)


def test_provider_fails_closed_when_expired_token_has_no_refresh_token() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1), refresh_token=None))
    provider = _provider(store, FakeRefreshService())

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(provider.get_access_token())

    _assert_safe_exception(exc_info.value)


def test_provider_propagates_or_wraps_refresh_service_failure_safely() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    refresh_service = FakeRefreshService()
    refresh_service.fail = True

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(_provider(store, refresh_service).get_access_token())

    _assert_safe_exception(exc_info.value)
    assert isinstance(exc_info.value, AccessTokenProviderError)
    assert exc_info.value.reason is AccessTokenProviderFailureReason.TOKEN_REFRESH_FAILED


def test_provider_classifies_invalid_refresh_response() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    refresh_service = FakeRefreshService(response=object())  # type: ignore[arg-type]

    with pytest.raises(AccessTokenProviderError) as exc_info:
        run(_provider(store, refresh_service).get_access_token())

    assert exc_info.value.reason is AccessTokenProviderFailureReason.TOKEN_REFRESH_RESPONSE_INVALID
    _assert_safe_exception(exc_info.value)


def test_provider_fails_closed_when_token_store_save_fails() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    store.fail_save = True

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(_provider(store, FakeRefreshService()).get_access_token())

    _assert_safe_exception(exc_info.value)
    assert isinstance(exc_info.value, AccessTokenProviderError)
    assert exc_info.value.reason is AccessTokenProviderFailureReason.TOKEN_REFRESH_PERSIST_FAILED


def test_provider_uses_default_profile_id() -> None:
    store = _store_with(_token_set(profile_id="default", expires_at=_future(seconds=3600)))

    token = run(_provider(store, FakeRefreshService()).get_access_token())

    assert token.value.get_secret_value() == RAW_STORED_ACCESS_TOKEN


def test_provider_supports_custom_profile_id() -> None:
    store = _store_with(
        _token_set(
            profile_id="custom-profile",
            access_token="custom-access-token-private",
            expires_at=_future(seconds=3600),
        )
    )
    provider = _provider(
        store,
        FakeRefreshService(),
        profile_id=OAuthTokenProfileId(value="custom-profile"),
    )

    token = run(provider.get_access_token())

    assert token.value.get_secret_value() == "custom-access-token-private"


def test_provider_validates_refresh_skew_seconds_non_negative() -> None:
    with pytest.raises(ValueError):
        _provider(FakeOAuthTokenStore(), FakeRefreshService(), refresh_skew_seconds=-1)


def test_provider_maps_oauth_access_token_to_soundcloud_access_token() -> None:
    store = _store_with(_token_set(expires_at=_future(seconds=3600)))

    token = run(_provider(store, FakeRefreshService()).get_access_token())

    assert isinstance(token, SoundCloudAccessToken)
    assert token.token_type == "OAuth"


def test_exception_messages_do_not_contain_raw_access_token() -> None:
    provider = _provider(FakeOAuthTokenStore(), FakeRefreshService())

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(provider.get_access_token())

    _assert_safe_exception(exc_info.value)


def test_exception_messages_do_not_contain_raw_refresh_token() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1), refresh_token=None))

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(_provider(store, FakeRefreshService()).get_access_token())

    _assert_safe_exception(exc_info.value)


def test_exception_messages_do_not_contain_raw_client_secret() -> None:
    store = _store_with(_token_set(expires_at=_past(seconds=1)))
    refresh_service = FakeRefreshService()
    refresh_service.fail = True

    with pytest.raises(SoundcloudDownloaderError) as exc_info:
        run(_provider(store, refresh_service).get_access_token())

    _assert_safe_exception(exc_info.value)


def test_fake_refresh_service_satisfies_oauth_refresh_token_port() -> None:
    assert isinstance(FakeRefreshService(), OAuthRefreshTokenPort)


def test_tests_perform_no_real_network_calls(monkeypatch: Any) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in auto-refresh provider tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)
    store = _store_with(_token_set(expires_at=_future(seconds=3600)))

    assert run(_provider(store, FakeRefreshService()).get_access_token()).value.get_secret_value()


def test_tests_write_no_files(monkeypatch: Any) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("File writes are not allowed in auto-refresh provider tests.")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)
    store = _store_with(_token_set(expires_at=_future(seconds=3600)))

    assert run(_provider(store, FakeRefreshService()).get_access_token()).value.get_secret_value()


def _provider(
    store: FakeOAuthTokenStore,
    refresh_service: FakeRefreshService,
    *,
    profile_id: OAuthTokenProfileId | None = None,
    refresh_skew_seconds: int = 60,
) -> AutoRefreshingAccessTokenProvider:
    return AutoRefreshingAccessTokenProvider(
        token_store=store,
        refresh_service=refresh_service,
        client_id=OAuthClientId(value=SecretStr("client-id")),
        client_secret=OAuthClientSecret(value=SecretStr(RAW_CLIENT_SECRET)),
        profile_id=profile_id,
        refresh_skew_seconds=refresh_skew_seconds,
    )


def _store_with(token_set: StoredOAuthTokenSet) -> FakeOAuthTokenStore:
    store = FakeOAuthTokenStore()
    store.items[token_set.profile_id.value] = token_set
    return store


def _token_set(
    *,
    profile_id: str = "default",
    access_token: str = RAW_STORED_ACCESS_TOKEN,
    refresh_token: str | None = RAW_OLD_REFRESH_TOKEN,
    expires_at: datetime | None,
) -> StoredOAuthTokenSet:
    effective_created_at = _now() - timedelta(hours=2)
    return StoredOAuthTokenSet(
        profile_id=OAuthTokenProfileId(value=profile_id),
        access_token=OAuthAccessToken(value=SecretStr(access_token)),
        refresh_token=(
            OAuthRefreshToken(value=SecretStr(refresh_token))
            if refresh_token is not None
            else None
        ),
        created_at=effective_created_at,
        expires_at=expires_at,
    )


def _token_response(
    *,
    access_token: str = RAW_REFRESHED_ACCESS_TOKEN,
    refresh_token: str | None = RAW_NEW_REFRESH_TOKEN,
    expires_in: int = 3600,
) -> OAuthTokenResponse:
    return OAuthTokenResponse(
        access_token=OAuthAccessToken(value=SecretStr(access_token)),
        refresh_token=(
            OAuthRefreshToken(value=SecretStr(refresh_token))
            if refresh_token is not None
            else None
        ),
        expires_in=expires_in,
        scope="read",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _future(*, seconds: int) -> datetime:
    return _now() + timedelta(seconds=seconds)


def _past(*, seconds: int) -> datetime:
    return _now() - timedelta(seconds=seconds)


def _assert_safe_exception(error: SoundcloudDownloaderError) -> None:
    error_text = str(error)
    assert RAW_STORED_ACCESS_TOKEN not in error_text
    assert RAW_REFRESHED_ACCESS_TOKEN not in error_text
    assert RAW_OLD_REFRESH_TOKEN not in error_text
    assert RAW_NEW_REFRESH_TOKEN not in error_text
    assert RAW_CLIENT_SECRET not in error_text
