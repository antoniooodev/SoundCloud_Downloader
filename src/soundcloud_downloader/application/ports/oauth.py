from typing import Protocol, runtime_checkable

from soundcloud_downloader.domain import (
    OAuthAuthorizationCode,
    OAuthClientId,
    OAuthClientSecret,
    OAuthCodeVerifier,
    OAuthRedirectUri,
    OAuthRefreshToken,
    OAuthTokenResponse,
)


@runtime_checkable
class OAuthTokenExchangePort(Protocol):
    async def exchange_authorization_code(
        self,
        *,
        client_id: OAuthClientId,
        client_secret: OAuthClientSecret | None,
        redirect_uri: OAuthRedirectUri,
        code: OAuthAuthorizationCode,
        code_verifier: OAuthCodeVerifier,
    ) -> OAuthTokenResponse:
        ...


@runtime_checkable
class OAuthRefreshTokenPort(Protocol):
    async def refresh_access_token(
        self,
        *,
        client_id: OAuthClientId,
        client_secret: OAuthClientSecret,
        refresh_token: OAuthRefreshToken,
    ) -> OAuthTokenResponse:
        ...
