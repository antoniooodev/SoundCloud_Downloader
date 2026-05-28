from pydantic import SecretStr

from soundcloud_downloader.application import OAuthTokenStore
from soundcloud_downloader.application.ports import AccessTokenProviderPort
from soundcloud_downloader.domain import ErrorCode, OAuthTokenProfileId, SoundcloudDownloaderError
from soundcloud_downloader.infrastructure.soundcloud import SoundCloudAccessToken


class PersistentAccessTokenProvider:
    def __init__(
        self,
        token_store: OAuthTokenStore,
        profile_id: OAuthTokenProfileId | None = None,
    ) -> None:
        self._token_store = token_store
        self._profile_id = profile_id if profile_id is not None else OAuthTokenProfileId(value="default")

    async def get_access_token(self) -> SoundCloudAccessToken:
        token_set = self._token_store.get(self._profile_id)
        if token_set is None:
            raise SoundcloudDownloaderError(
                ErrorCode.AUTH_REQUIRED,
                "Stored OAuth access token was not found.",
            )
        if token_set.is_expired():
            raise SoundcloudDownloaderError(
                ErrorCode.AUTH_REQUIRED,
                "Stored OAuth access token is expired. Refresh is not implemented yet.",
            )
        return SoundCloudAccessToken(
            value=SecretStr(token_set.access_token.value.get_secret_value()),
            token_type=token_set.token_type,
        )


_protocol_check: type[AccessTokenProviderPort] = PersistentAccessTokenProvider
