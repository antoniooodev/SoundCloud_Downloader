import json
from collections.abc import Mapping
from typing import Any

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


_ACCEPTED_RESPONSE_TOKEN_TYPES = {"OAuth", "Bearer", "bearer"}


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

        invalid_fields: list[str] = []
        access_token = _required_secret_string(payload, "access_token", invalid_fields)
        refresh_token = _optional_secret_string(payload, "refresh_token", invalid_fields)
        token_type = _token_type(payload, invalid_fields)
        expires_in = _expires_in(payload, invalid_fields)
        scope = _scope(payload, invalid_fields)
        if invalid_fields:
            raise _token_response_validation_error(invalid_fields)

        try:
            return OAuthTokenResponse(
                access_token=OAuthAccessToken(value=SecretStr(access_token), token_type=token_type),
                refresh_token=(
                    OAuthRefreshToken(value=SecretStr(refresh_token))
                    if refresh_token is not None
                    else None
                ),
                expires_in=expires_in,
                scope=scope,
            )
        except ValidationError as exc:
            raise _token_response_validation_error(_invalid_fields_from_validation_error(exc)) from exc


def _required_secret_string(
    payload: Mapping[str, Any],
    field_name: str,
    invalid_fields: list[str],
) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or value == "":
        invalid_fields.append(field_name)
        return ""
    return value


def _optional_secret_string(
    payload: Mapping[str, Any],
    field_name: str,
    invalid_fields: list[str],
) -> str | None:
    if field_name not in payload or payload[field_name] is None or payload[field_name] == "":
        return None
    value = payload[field_name]
    if not isinstance(value, str):
        invalid_fields.append(field_name)
        return None
    return value


def _token_type(payload: Mapping[str, Any], invalid_fields: list[str]) -> str:
    if "token_type" not in payload or payload["token_type"] is None:
        return "OAuth"
    value = payload["token_type"]
    if isinstance(value, str) and value in _ACCEPTED_RESPONSE_TOKEN_TYPES:
        return value
    invalid_fields.append("token_type")
    return "OAuth"


def _expires_in(payload: Mapping[str, Any], invalid_fields: list[str]) -> int | None:
    if "expires_in" not in payload or payload["expires_in"] is None:
        return None
    value = payload["expires_in"]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        invalid_fields.append("expires_in")
        return None
    return int(value)


def _scope(payload: Mapping[str, Any], invalid_fields: list[str]) -> str | None:
    if "scope" not in payload or payload["scope"] is None or payload["scope"] == "":
        return None
    value = payload["scope"]
    if not isinstance(value, str):
        invalid_fields.append("scope")
        return None
    return value


def _invalid_fields_from_validation_error(exc: ValidationError) -> list[str]:
    invalid_fields: list[str] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = error.get("loc", ())
        if isinstance(location, tuple) and location:
            invalid_fields.append(str(location[0]))
    return invalid_fields or ["response"]


def _token_response_validation_error(invalid_fields: list[str]) -> OAuthTokenExchangeError:
    safe_fields = sorted(set(invalid_fields))
    return OAuthTokenExchangeError(
        ErrorCode.UNKNOWN_UNSAFE,
        f"OAuth token response validation failed. invalid_fields={json.dumps(safe_fields)}",
    )
