import base64
import hashlib
import secrets
import string
from urllib.parse import urlencode, urlparse, urlunparse

from pydantic import SecretStr

from soundcloud_downloader.domain.oauth import (
    OAuthAuthorizationRequest,
    OAuthClientId,
    OAuthCodeChallenge,
    OAuthCodeChallengeMethod,
    OAuthCodeVerifier,
    OAuthRedirectUri,
    OAuthResponseType,
    OAuthState,
)


PKCE_ALLOWED_CHARACTERS = string.ascii_letters + string.digits + "-._~"


class OAuthPKCEService:
    def generate_code_verifier(self, length: int = 64) -> OAuthCodeVerifier:
        if not 43 <= length <= 128:
            raise ValueError("OAuth code verifier length must be between 43 and 128 characters.")
        return OAuthCodeVerifier(
            value=SecretStr("".join(secrets.choice(PKCE_ALLOWED_CHARACTERS) for _ in range(length)))
        )

    def derive_s256_challenge(
        self,
        verifier: OAuthCodeVerifier,
    ) -> OAuthCodeChallenge:
        digest = hashlib.sha256(verifier.value.get_secret_value().encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return OAuthCodeChallenge(value=challenge, method=OAuthCodeChallengeMethod.S256)

    def generate_state(self, length: int = 32) -> OAuthState:
        if length < 1:
            raise ValueError("OAuth state length must be positive.")
        return OAuthState(value=SecretStr(secrets.token_urlsafe(length)[:length]))

    def build_authorization_request(
        self,
        *,
        auth_base_url: str,
        client_id: OAuthClientId,
        redirect_uri: OAuthRedirectUri,
        code_challenge: OAuthCodeChallenge,
        state: OAuthState,
    ) -> OAuthAuthorizationRequest:
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

        authorize_path = f"{parsed_base_url.path}/authorize"
        query = urlencode(
            {
                "client_id": client_id.value.get_secret_value(),
                "redirect_uri": redirect_uri.value,
                "response_type": OAuthResponseType.CODE.value,
                "code_challenge": code_challenge.value,
                "code_challenge_method": code_challenge.method.value,
                "state": state.value.get_secret_value(),
            }
        )
        authorization_url = urlunparse(
            (
                parsed_base_url.scheme,
                parsed_base_url.netloc,
                authorize_path,
                "",
                query,
                "",
            )
        )
        return OAuthAuthorizationRequest(
            authorization_url=authorization_url,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=state,
        )
