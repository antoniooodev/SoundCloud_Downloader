from urllib.parse import urlparse, urlunparse

from soundcloud_downloader.domain.oauth import OAuthClientId, OAuthCodeVerifier, OAuthRedirectUri
from soundcloud_downloader.domain.oauth_token import (
    OAuthAuthorizationCode,
    OAuthClientSecret,
    OAuthTokenExchangeRequest,
)


REDACTED_VALUE = "**********"


class OAuthTokenExchangeRequestBuilder:
    def build_authorization_code_request(
        self,
        *,
        auth_base_url: str,
        client_id: OAuthClientId,
        client_secret: OAuthClientSecret | None,
        redirect_uri: OAuthRedirectUri,
        code: OAuthAuthorizationCode,
        code_verifier: OAuthCodeVerifier,
    ) -> OAuthTokenExchangeRequest:
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
        return OAuthTokenExchangeRequest(
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            code=code,
            code_verifier=code_verifier,
        )


def redact_token_exchange_request(request: OAuthTokenExchangeRequest) -> dict[str, object]:
    redacted = request.model_dump(mode="json")
    redacted["code"] = {"value": REDACTED_VALUE}
    redacted["code_verifier"] = {"value": REDACTED_VALUE}
    if request.client_secret is not None:
        redacted["client_secret"] = {"value": REDACTED_VALUE}
    return redacted
