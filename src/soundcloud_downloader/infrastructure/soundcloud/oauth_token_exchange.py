import json
from collections.abc import Mapping

from pydantic import SecretStr, ValidationError

from soundcloud_downloader.application import OAuthTokenExchangeRequestBuilder
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ErrorCode,
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClientId,
    OAuthClientSecret,
    OAuthCodeVerifier,
    OAuthRedirectUri,
    OAuthRefreshToken,
    OAuthTokenResponse,
    SoundcloudDownloaderError,
)
from soundcloud_downloader.infrastructure.http import HttpMethod, HttpRequest, SafeAsyncHttpClient


class OAuthTokenExchangeError(SoundcloudDownloaderError):
    pass


class OAuthTokenExchangeService:
    def __init__(
        self,
        settings: AppSettings,
        http_client: SafeAsyncHttpClient,
        request_builder: OAuthTokenExchangeRequestBuilder | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._request_builder = (
            request_builder if request_builder is not None else OAuthTokenExchangeRequestBuilder()
        )

    async def exchange_authorization_code(
        self,
        *,
        client_id: OAuthClientId,
        client_secret: OAuthClientSecret | None,
        redirect_uri: OAuthRedirectUri,
        code: OAuthAuthorizationCode,
        code_verifier: OAuthCodeVerifier,
    ) -> OAuthTokenResponse:
        token_request = self._request_builder.build_authorization_code_request(
            auth_base_url=self._settings.soundcloud_auth_base_url,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            code=code,
            code_verifier=code_verifier,
        )
        response = await self._http_client.request(
            HttpRequest(
                method=HttpMethod.POST,
                url=token_request.token_url,
                headers={
                    "accept": "application/json; charset=utf-8",
                    "content-type": "application/x-www-form-urlencoded",
                },
                form_data=token_request.to_form_data(),
            )
        )

        if 200 <= response.status_code <= 299:
            return self._parse_success_response(response.text)
        if response.status_code in {400, 401, 403}:
            raise OAuthTokenExchangeError(
                ErrorCode.NETWORK_PERMANENT,
                "OAuth token exchange was rejected by the authorization server.",
            )
        if response.status_code == 429:
            raise OAuthTokenExchangeError(
                ErrorCode.NETWORK_PERMANENT,
                "OAuth token exchange was rate limited by the authorization server.",
            )
        if 500 <= response.status_code <= 599:
            raise OAuthTokenExchangeError(
                ErrorCode.NETWORK_PERMANENT,
                "OAuth token exchange failed because the authorization server returned an error.",
            )
        raise OAuthTokenExchangeError(
            ErrorCode.NETWORK_PERMANENT,
            "OAuth token exchange failed with an unexpected HTTP status.",
        )

    def _parse_success_response(self, response_text: str) -> OAuthTokenResponse:
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise OAuthTokenExchangeError(
                ErrorCode.NETWORK_PERMANENT,
                "OAuth token exchange returned invalid JSON.",
            ) from exc

        if not isinstance(payload, Mapping):
            raise OAuthTokenExchangeError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth token exchange returned an invalid response payload.",
            )

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or access_token == "":
            raise OAuthTokenExchangeError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth token exchange response did not include a valid access token.",
            )

        refresh_token = payload.get("refresh_token")
        expires_in = payload.get("expires_in")
        scope = payload.get("scope")
        try:
            return OAuthTokenResponse(
                access_token=OAuthAccessToken(value=SecretStr(access_token)),
                refresh_token=(
                    OAuthRefreshToken(value=SecretStr(refresh_token))
                    if isinstance(refresh_token, str) and refresh_token != ""
                    else None
                ),
                expires_in=expires_in if isinstance(expires_in, int) else None,
                scope=scope if isinstance(scope, str) else None,
            )
        except ValidationError as exc:
            raise OAuthTokenExchangeError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth token exchange response failed safe validation.",
            ) from exc
