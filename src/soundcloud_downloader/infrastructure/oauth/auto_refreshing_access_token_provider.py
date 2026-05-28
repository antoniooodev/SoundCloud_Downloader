from datetime import datetime, timedelta, timezone

from pydantic import SecretStr

from soundcloud_downloader.application import OAuthTokenStore
from soundcloud_downloader.application.ports import AccessTokenProviderPort, OAuthRefreshTokenPort
from soundcloud_downloader.domain import (
    ErrorCode,
    OAuthClientId,
    OAuthClientSecret,
    OAuthTokenProfileId,
    SoundcloudDownloaderError,
    StoredOAuthTokenSet,
)
from soundcloud_downloader.infrastructure.soundcloud import SoundCloudAccessToken


class AutoRefreshingAccessTokenProvider:
    def __init__(
        self,
        *,
        token_store: OAuthTokenStore,
        refresh_service: OAuthRefreshTokenPort,
        client_id: OAuthClientId,
        client_secret: OAuthClientSecret,
        profile_id: OAuthTokenProfileId | None = None,
        refresh_skew_seconds: int = 60,
    ) -> None:
        if refresh_skew_seconds < 0:
            raise ValueError("OAuth token refresh skew must be non-negative.")
        self._token_store = token_store
        self._refresh_service = refresh_service
        self._client_id = client_id
        self._client_secret = client_secret
        self._profile_id = profile_id if profile_id is not None else OAuthTokenProfileId(value="default")
        self._refresh_skew_seconds = refresh_skew_seconds

    async def get_access_token(self) -> SoundCloudAccessToken:
        token_set = self._token_store.get(self._profile_id)
        if token_set is None:
            raise SoundcloudDownloaderError(
                ErrorCode.AUTH_REQUIRED,
                "Stored OAuth access token was not found.",
            )
        if not _token_set_needs_refresh(
            token_set,
            now=datetime.now(timezone.utc),
            skew_seconds=self._refresh_skew_seconds,
        ):
            return _to_soundcloud_access_token(token_set)
        if token_set.refresh_token is None:
            raise SoundcloudDownloaderError(
                ErrorCode.AUTH_REQUIRED,
                "Stored OAuth access token is expired and no refresh token is available.",
            )

        try:
            token_response = await self._refresh_service.refresh_access_token(
                client_id=self._client_id,
                client_secret=self._client_secret,
                refresh_token=token_set.refresh_token,
            )
        except SoundcloudDownloaderError:
            raise
        except Exception as exc:
            raise SoundcloudDownloaderError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth token refresh failed safely.",
            ) from exc

        try:
            refreshed_token_set = StoredOAuthTokenSet.from_token_response(
                profile_id=self._profile_id,
                token_response=token_response,
            )
        except Exception as exc:
            raise SoundcloudDownloaderError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth token refresh produced an invalid token response.",
            ) from exc

        try:
            self._token_store.save(refreshed_token_set)
        except Exception as exc:
            raise SoundcloudDownloaderError(
                ErrorCode.STORAGE_FAILED,
                "OAuth token refresh could not be persisted safely.",
            ) from exc

        return _to_soundcloud_access_token(refreshed_token_set)


def _token_set_needs_refresh(
    token_set: StoredOAuthTokenSet,
    *,
    now: datetime,
    skew_seconds: int,
) -> bool:
    if token_set.expires_at is None:
        return False
    return token_set.expires_at <= now + timedelta(seconds=skew_seconds)


def _to_soundcloud_access_token(token_set: StoredOAuthTokenSet) -> SoundCloudAccessToken:
    return SoundCloudAccessToken(
        value=SecretStr(token_set.access_token.value.get_secret_value()),
        token_type=token_set.token_type,
    )


_protocol_check: type[AccessTokenProviderPort] = AutoRefreshingAccessTokenProvider
