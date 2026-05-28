import socket
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.application import (
    OAuthRefreshTokenRequestBuilder,
    redact_refresh_token_request,
)
from soundcloud_downloader.domain import (
    OAuthClientId,
    OAuthClientSecret,
    OAuthGrantType,
    OAuthRefreshToken,
    OAuthRefreshTokenRequest,
)


RAW_CLIENT_ID = "client-id"
RAW_CLIENT_SECRET = "dummy-client-secret"
RAW_REFRESH_TOKEN = "dummy-refresh-token"


def test_refresh_token_request_repr_does_not_expose_raw_refresh_token() -> None:
    assert RAW_REFRESH_TOKEN not in repr(_request())


def test_refresh_token_request_repr_does_not_expose_raw_client_secret() -> None:
    assert RAW_CLIENT_SECRET not in repr(_request())


def test_refresh_token_request_model_dump_masks_raw_refresh_token() -> None:
    dumped = _request().model_dump(mode="json")

    assert dumped["refresh_token"]["value"] == "**********"
    assert RAW_REFRESH_TOKEN not in str(dumped)


def test_refresh_token_request_model_dump_masks_raw_client_secret() -> None:
    dumped = _request().model_dump(mode="json")

    assert dumped["client_secret"]["value"] == "**********"
    assert RAW_CLIENT_SECRET not in str(dumped)


def test_refresh_token_request_rejects_token_url_with_query() -> None:
    with pytest.raises(ValidationError):
        _request(token_url="https://secure.soundcloud.com/oauth/token?bad=true")


def test_refresh_token_request_rejects_token_url_with_fragment() -> None:
    with pytest.raises(ValidationError):
        _request(token_url="https://secure.soundcloud.com/oauth/token#fragment")


def test_refresh_token_request_rejects_token_url_with_userinfo() -> None:
    with pytest.raises(ValidationError):
        _request(token_url="https://user:pass@secure.soundcloud.com/oauth/token")


def test_refresh_token_request_requires_grant_type_refresh_token() -> None:
    with pytest.raises(ValidationError):
        _request(grant_type=OAuthGrantType.AUTHORIZATION_CODE)


def test_to_form_data_includes_grant_type_refresh_token() -> None:
    assert _request().to_form_data()["grant_type"] == "refresh_token"


def test_to_form_data_includes_client_id() -> None:
    assert _request().to_form_data()["client_id"] == RAW_CLIENT_ID


def test_to_form_data_includes_client_secret() -> None:
    assert _request().to_form_data()["client_secret"] == RAW_CLIENT_SECRET


def test_to_form_data_includes_refresh_token() -> None:
    assert _request().to_form_data()["refresh_token"] == RAW_REFRESH_TOKEN


def test_to_form_data_does_not_include_authorization_code() -> None:
    assert "code" not in _request().to_form_data()


def test_to_form_data_does_not_include_code_verifier() -> None:
    assert "code_verifier" not in _request().to_form_data()


def test_to_form_data_does_not_include_access_token() -> None:
    assert "access_token" not in _request().to_form_data()


def test_refresh_token_request_builder_creates_oauth_token_url() -> None:
    request = _builder_request(auth_base_url="https://secure.soundcloud.com")

    assert request.token_url == "https://secure.soundcloud.com/oauth/token"


def test_builder_strips_trailing_slash_from_auth_base_url() -> None:
    request = _builder_request(auth_base_url="https://secure.soundcloud.com/")

    assert request.token_url == "https://secure.soundcloud.com/oauth/token"


def test_builder_rejects_auth_base_url_with_query() -> None:
    with pytest.raises(ValueError):
        _builder_request(auth_base_url="https://secure.soundcloud.com?bad=true")


def test_builder_rejects_auth_base_url_with_fragment() -> None:
    with pytest.raises(ValueError):
        _builder_request(auth_base_url="https://secure.soundcloud.com#fragment")


def test_builder_rejects_auth_base_url_with_userinfo() -> None:
    with pytest.raises(ValueError):
        _builder_request(auth_base_url="https://user:pass@secure.soundcloud.com")


def test_redaction_helper_redacts_client_secret() -> None:
    redacted = redact_refresh_token_request(_request())

    assert redacted["client_secret"] == {"value": "**********"}
    assert RAW_CLIENT_SECRET not in str(redacted)


def test_redaction_helper_redacts_refresh_token() -> None:
    redacted = redact_refresh_token_request(_request())

    assert redacted["refresh_token"] == {"value": "**********"}
    assert RAW_REFRESH_TOKEN not in str(redacted)


def test_request_model_is_immutable() -> None:
    request = _request()

    with pytest.raises(Exception, match="frozen"):
        request.token_url = "https://example.test/oauth/token"


def test_tests_perform_no_network_calls(monkeypatch: Any) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in OAuth refresh contract tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)

    assert _request().to_form_data()["grant_type"] == "refresh_token"


def test_tests_write_no_files(monkeypatch: Any) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("File writes are not allowed in OAuth refresh contract tests.")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert _request().to_form_data()["grant_type"] == "refresh_token"


def _request(
    *,
    token_url: str = "https://secure.soundcloud.com/oauth/token",
    grant_type: OAuthGrantType = OAuthGrantType.REFRESH_TOKEN,
) -> OAuthRefreshTokenRequest:
    return OAuthRefreshTokenRequest(
        token_url=token_url,
        grant_type=grant_type,
        client_id=OAuthClientId(value=SecretStr(RAW_CLIENT_ID)),
        client_secret=OAuthClientSecret(value=SecretStr(RAW_CLIENT_SECRET)),
        refresh_token=OAuthRefreshToken(value=SecretStr(RAW_REFRESH_TOKEN)),
    )


def _builder_request(*, auth_base_url: str) -> OAuthRefreshTokenRequest:
    return OAuthRefreshTokenRequestBuilder().build_refresh_token_request(
        auth_base_url=auth_base_url,
        client_id=OAuthClientId(value=SecretStr(RAW_CLIENT_ID)),
        client_secret=OAuthClientSecret(value=SecretStr(RAW_CLIENT_SECRET)),
        refresh_token=OAuthRefreshToken(value=SecretStr(RAW_REFRESH_TOKEN)),
    )
