import json
from collections.abc import Mapping

from pydantic import SecretStr, ValidationError

from soundcloud_downloader.application import OAuthRefreshTokenRequestBuilder
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ErrorCode,
    OAuthAccessToken,
    OAuthClientId,
    OAuthClientSecret,
    OAuthRefreshToken,
    OAuthTokenResponse,
    SoundcloudDownloaderError,
)
from soundcloud_downloader.infrastructure.http import HttpMethod, HttpRequest, SafeAsyncHttpClient


class OAuthRefreshTokenError(SoundcloudDownloaderError):
    pass


class OAuthRefreshTokenService:
    def __init__(
        self,
        settings: AppSettings,
        http_client: SafeAsyncHttpClient,
        request_builder: OAuthRefreshTokenRequestBuilder | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._request_builder = (
            request_builder if request_builder is not None else OAuthRefreshTokenRequestBuilder()
        )

    async def refresh_access_token(
        self,
        *,
        client_id: OAuthClientId,
        client_secret: OAuthClientSecret,
        refresh_token: OAuthRefreshToken,
    ) -> OAuthTokenResponse:
        refresh_request = self._request_builder.build_refresh_token_request(
            auth_base_url=self._settings.soundcloud_auth_base_url,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )
        response = await self._http_client.request(
            HttpRequest(
                method=HttpMethod.POST,
                url=refresh_request.token_url,
                headers={
                    "accept": "application/json; charset=utf-8",
                    "content-type": "application/x-www-form-urlencoded",
                },
                form_data=refresh_request.to_form_data(),
            )
        )

        if 200 <= response.status_code <= 299:
            return self._parse_success_response(response.text)
        if response.status_code in {400, 401, 403}:
            raise OAuthRefreshTokenError(
                ErrorCode.NETWORK_PERMANENT,
                "OAuth token refresh was rejected by the authorization server.",
            )
        if response.status_code == 429:
            raise OAuthRefreshTokenError(
                ErrorCode.NETWORK_PERMANENT,
                "OAuth token refresh was rate limited by the authorization server.",
            )
        if 500 <= response.status_code <= 599:
            raise OAuthRefreshTokenError(
                ErrorCode.NETWORK_PERMANENT,
                "OAuth token refresh failed because the authorization server returned an error.",
            )
        raise OAuthRefreshTokenError(
            ErrorCode.NETWORK_PERMANENT,
            "OAuth token refresh failed with an unexpected HTTP status.",
        )

    def _parse_success_response(self, response_text: str) -> OAuthTokenResponse:
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise OAuthRefreshTokenError(
                ErrorCode.NETWORK_PERMANENT,
                "OAuth token refresh returned invalid JSON.",
            ) from exc

        if not isinstance(payload, Mapping):
            raise OAuthRefreshTokenError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth token refresh returned an invalid response payload.",
            )

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or access_token == "":
            raise OAuthRefreshTokenError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth token refresh response did not include a valid access token.",
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
            raise OAuthRefreshTokenError(
                ErrorCode.UNKNOWN_UNSAFE,
                "OAuth token refresh response failed safe validation.",
            ) from exc
