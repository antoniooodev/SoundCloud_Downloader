import socket
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from soundcloud_downloader.application import (
    OAuthTokenExchangeRequestBuilder,
    redact_token_exchange_request,
)
from soundcloud_downloader.domain import (
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClientId,
    OAuthClientSecret,
    OAuthCodeVerifier,
    OAuthGrantType,
    OAuthRedirectUri,
    OAuthRefreshToken,
    OAuthTokenExchangeRequest,
    OAuthTokenResponse,
)


RAW_CODE = "authorization-code"
RAW_CLIENT_ID = "client-id"
RAW_CLIENT_SECRET = "client-secret"
RAW_CODE_VERIFIER = "A" * 64
RAW_ACCESS_TOKEN = "access-token"
RAW_REFRESH_TOKEN = "refresh-token"


def test_oauth_authorization_code_repr_does_not_expose_raw_code() -> None:
    code = OAuthAuthorizationCode(value=SecretStr(RAW_CODE))

    assert RAW_CODE not in repr(code)


def test_oauth_authorization_code_model_dump_masks_raw_code() -> None:
    code = OAuthAuthorizationCode(value=SecretStr(RAW_CODE))

    assert code.model_dump() == {"value": "**********"}


def test_oauth_client_secret_repr_does_not_expose_raw_secret() -> None:
    secret = OAuthClientSecret(value=SecretStr(RAW_CLIENT_SECRET))

    assert RAW_CLIENT_SECRET not in repr(secret)


def test_oauth_access_token_repr_does_not_expose_raw_token() -> None:
    token = OAuthAccessToken(value=SecretStr(RAW_ACCESS_TOKEN))

    assert RAW_ACCESS_TOKEN not in repr(token)


def test_oauth_refresh_token_repr_does_not_expose_raw_token() -> None:
    token = OAuthRefreshToken(value=SecretStr(RAW_REFRESH_TOKEN))

    assert RAW_REFRESH_TOKEN not in repr(token)


def test_oauth_token_response_repr_and_model_dump_do_not_expose_raw_tokens() -> None:
    response = _create_token_response()
    dumped = response.model_dump()

    assert RAW_ACCESS_TOKEN not in repr(response)
    assert RAW_REFRESH_TOKEN not in repr(response)
    assert RAW_ACCESS_TOKEN not in str(dumped)
    assert RAW_REFRESH_TOKEN not in str(dumped)
    assert dumped["access_token"]["value"] == "**********"
    assert dumped["refresh_token"]["value"] == "**********"


def test_oauth_token_response_rejects_non_positive_expires_in() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        OAuthTokenResponse(access_token=OAuthAccessToken(value=SecretStr(RAW_ACCESS_TOKEN)), expires_in=0)


def test_token_exchange_request_builder_creates_oauth_token_url() -> None:
    request = _build_request(auth_base_url="https://secure.soundcloud.com")

    assert request.token_url == "https://secure.soundcloud.com/oauth/token"


def test_token_exchange_request_builder_strips_trailing_slash_from_auth_base_url() -> None:
    request = _build_request(auth_base_url="https://secure.soundcloud.com/")

    assert request.token_url == "https://secure.soundcloud.com/oauth/token"


def test_token_exchange_request_builder_rejects_auth_base_url_with_query() -> None:
    with pytest.raises(ValueError, match="query"):
        _build_request(auth_base_url="https://secure.soundcloud.com?bad=true")


def test_token_exchange_request_builder_rejects_auth_base_url_with_fragment() -> None:
    with pytest.raises(ValueError, match="fragment"):
        _build_request(auth_base_url="https://secure.soundcloud.com#bad")


def test_token_exchange_request_builder_rejects_auth_base_url_with_userinfo() -> None:
    with pytest.raises(ValueError, match="userinfo|credentials"):
        _build_request(auth_base_url="https://user:pass@secure.soundcloud.com")


def test_oauth_token_exchange_request_rejects_token_url_with_query() -> None:
    with pytest.raises(ValueError, match="query"):
        _create_request(token_url="https://secure.soundcloud.com/oauth/token?bad=true")


def test_oauth_token_exchange_request_rejects_token_url_with_fragment() -> None:
    with pytest.raises(ValueError, match="fragment"):
        _create_request(token_url="https://secure.soundcloud.com/oauth/token#bad")


def test_oauth_token_exchange_request_rejects_token_url_with_userinfo() -> None:
    with pytest.raises(ValueError, match="userinfo|credentials"):
        _create_request(token_url="https://user:pass@secure.soundcloud.com/oauth/token")


def test_to_form_data_includes_grant_type_authorization_code() -> None:
    form_data = _create_request().to_form_data()

    assert form_data["grant_type"] == OAuthGrantType.AUTHORIZATION_CODE.value


def test_to_form_data_includes_client_id() -> None:
    form_data = _create_request().to_form_data()

    assert form_data["client_id"] == RAW_CLIENT_ID


def test_to_form_data_includes_redirect_uri() -> None:
    form_data = _create_request().to_form_data()

    assert form_data["redirect_uri"] == "http://localhost:8765/callback"


def test_to_form_data_includes_code() -> None:
    form_data = _create_request().to_form_data()

    assert form_data["code"] == RAW_CODE


def test_to_form_data_includes_code_verifier() -> None:
    form_data = _create_request().to_form_data()

    assert form_data["code_verifier"] == RAW_CODE_VERIFIER


def test_to_form_data_includes_client_secret_only_when_provided() -> None:
    form_data = _create_request(client_secret=OAuthClientSecret(value=SecretStr(RAW_CLIENT_SECRET))).to_form_data()

    assert form_data["client_secret"] == RAW_CLIENT_SECRET


def test_to_form_data_omits_client_secret_when_none() -> None:
    form_data = _create_request(client_secret=None).to_form_data()

    assert "client_secret" not in form_data


def test_redaction_helper_redacts_code_code_verifier_and_client_secret() -> None:
    request = _create_request(client_secret=OAuthClientSecret(value=SecretStr(RAW_CLIENT_SECRET)))
    redacted = redact_token_exchange_request(request)

    assert redacted["code"] == {"value": "**********"}
    assert redacted["code_verifier"] == {"value": "**********"}
    assert redacted["client_secret"] == {"value": "**********"}
    assert RAW_CODE not in str(redacted)
    assert RAW_CODE_VERIFIER not in str(redacted)
    assert RAW_CLIENT_SECRET not in str(redacted)


def test_request_model_is_immutable() -> None:
    request = _create_request()

    with pytest.raises(Exception, match="frozen"):
        request.token_url = "https://example.test/oauth/token"


def test_response_model_is_immutable() -> None:
    response = _create_token_response()

    with pytest.raises(Exception, match="frozen"):
        response.scope = "other"


def test_no_test_performs_network_calls(monkeypatch: Any) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in OAuth token exchange tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)

    _create_request()


def test_no_test_writes_files(monkeypatch: Any) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("File writes are not allowed in OAuth token exchange tests.")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    _create_request()


def _build_request(auth_base_url: str) -> OAuthTokenExchangeRequest:
    return OAuthTokenExchangeRequestBuilder().build_authorization_code_request(
        auth_base_url=auth_base_url,
        client_id=OAuthClientId(value=SecretStr(RAW_CLIENT_ID)),
        client_secret=None,
        redirect_uri=OAuthRedirectUri(value="http://localhost:8765/callback"),
        code=OAuthAuthorizationCode(value=SecretStr(RAW_CODE)),
        code_verifier=OAuthCodeVerifier(value=SecretStr(RAW_CODE_VERIFIER)),
    )


def _create_request(
    *,
    token_url: str = "https://secure.soundcloud.com/oauth/token",
    client_secret: OAuthClientSecret | None = None,
) -> OAuthTokenExchangeRequest:
    return OAuthTokenExchangeRequest(
        token_url=token_url,
        client_id=OAuthClientId(value=SecretStr(RAW_CLIENT_ID)),
        client_secret=client_secret,
        redirect_uri=OAuthRedirectUri(value="http://localhost:8765/callback"),
        code=OAuthAuthorizationCode(value=SecretStr(RAW_CODE)),
        code_verifier=OAuthCodeVerifier(value=SecretStr(RAW_CODE_VERIFIER)),
    )


def _create_token_response() -> OAuthTokenResponse:
    return OAuthTokenResponse(
        access_token=OAuthAccessToken(value=SecretStr(RAW_ACCESS_TOKEN)),
        refresh_token=OAuthRefreshToken(value=SecretStr(RAW_REFRESH_TOKEN)),
        expires_in=3600,
        scope="read",
    )
