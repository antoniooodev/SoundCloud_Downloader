from typing import Protocol, runtime_checkable

from soundcloud_downloader.domain import (
    OAuthAuthorizationCode,
    OAuthClientId,
    OAuthClientSecret,
    OAuthCodeVerifier,
    OAuthRedirectUri,
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
