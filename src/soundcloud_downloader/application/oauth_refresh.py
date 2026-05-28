from urllib.parse import urlparse, urlunparse

from soundcloud_downloader.application.oauth_token_exchange import REDACTED_VALUE
from soundcloud_downloader.domain import (
    OAuthClientId,
    OAuthClientSecret,
    OAuthRefreshToken,
    OAuthRefreshTokenRequest,
)


class OAuthRefreshTokenRequestBuilder:
    def build_refresh_token_request(
        self,
        *,
        auth_base_url: str,
        client_id: OAuthClientId,
        client_secret: OAuthClientSecret,
        refresh_token: OAuthRefreshToken,
    ) -> OAuthRefreshTokenRequest:
        parsed_base_url = urlparse(auth_base_url.rstrip("/"))
        if (
            auth_base_url == ""
            or parsed_base_url.scheme not in {"http", "https"}
            or parsed_base_url.netloc == ""
        ):
            raise ValueError("OAuth auth base URL must be a non-empty absolute URL.")
        if parsed_base_url.query != "":
            raise ValueError("OAuth auth base URL must not contain a query string.")
        if parsed_base_url.fragment != "":
            raise ValueError("OAuth auth base URL must not contain a fragment.")
        if parsed_base_url.username is not None or parsed_base_url.password is not None:
            raise ValueError("OAuth auth base URL must not contain userinfo credentials.")

        token_path = f"{parsed_base_url.path}/oauth/token"
        token_url = urlunparse(
            (
                parsed_base_url.scheme,
                parsed_base_url.netloc,
                token_path,
                "",
                "",
                "",
            )
        )
        return OAuthRefreshTokenRequest(
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )


def redact_refresh_token_request(request: OAuthRefreshTokenRequest) -> dict[str, object]:
    redacted = request.model_dump(mode="json")
    redacted["client_secret"] = {"value": REDACTED_VALUE}
    redacted["refresh_token"] = {"value": REDACTED_VALUE}
    return redacted
