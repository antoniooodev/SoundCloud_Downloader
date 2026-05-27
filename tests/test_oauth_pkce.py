import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr

from soundcloud_downloader.application import OAuthPKCEService
from soundcloud_downloader.domain import (
    OAuthClientId,
    OAuthCodeChallenge,
    OAuthCodeChallengeMethod,
    OAuthCodeVerifier,
    OAuthRedirectUri,
    OAuthState,
)


PKCE_ALLOWED_PATTERN = re.compile(r"^[A-Za-z0-9._~-]+$")


def test_generate_code_verifier_returns_allowed_pkce_chars_only() -> None:
    verifier = OAuthPKCEService().generate_code_verifier()

    assert PKCE_ALLOWED_PATTERN.fullmatch(verifier.value.get_secret_value()) is not None


def test_generate_code_verifier_default_length_is_valid() -> None:
    verifier = OAuthPKCEService().generate_code_verifier()

    assert len(verifier.value.get_secret_value()) == 64


def test_generate_code_verifier_rejects_length_less_than_43() -> None:
    with pytest.raises(ValueError, match="between 43 and 128"):
        OAuthPKCEService().generate_code_verifier(42)


def test_generate_code_verifier_rejects_length_greater_than_128() -> None:
    with pytest.raises(ValueError, match="between 43 and 128"):
        OAuthPKCEService().generate_code_verifier(129)


def test_derive_s256_challenge_matches_base64url_sha256_without_padding() -> None:
    raw_verifier = "A" * 43
    verifier = OAuthCodeVerifier(value=SecretStr(raw_verifier))

    challenge = OAuthPKCEService().derive_s256_challenge(verifier)

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(raw_verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    assert challenge == OAuthCodeChallenge(value=expected, method=OAuthCodeChallengeMethod.S256)


def test_code_verifier_raw_value_is_not_exposed_in_repr() -> None:
    raw_verifier = "B" * 43
    verifier = OAuthCodeVerifier(value=SecretStr(raw_verifier))

    assert raw_verifier not in repr(verifier)


def test_code_verifier_raw_value_is_masked_in_model_dump() -> None:
    raw_verifier = "C" * 43
    verifier = OAuthCodeVerifier(value=SecretStr(raw_verifier))

    assert verifier.model_dump() == {"value": "**********"}


def test_generate_state_returns_non_empty_secret_str() -> None:
    state = OAuthPKCEService().generate_state()

    assert isinstance(state.value, SecretStr)
    assert state.value.get_secret_value() != ""


def test_generate_state_uses_requested_valid_length() -> None:
    state = OAuthPKCEService().generate_state(48)

    assert len(state.value.get_secret_value()) == 48


def test_build_authorization_request_produces_url_under_authorize() -> None:
    request = _build_request()

    assert urlparse(request.authorization_url).path == "/oauth2/authorize"


def test_authorization_url_includes_response_type_code() -> None:
    query = _authorization_query()

    assert query["response_type"] == ["code"]


def test_authorization_url_includes_code_challenge_method_s256() -> None:
    query = _authorization_query()

    assert query["code_challenge_method"] == ["S256"]


def test_authorization_url_does_not_include_code_verifier() -> None:
    authorization_url = _build_request().authorization_url

    assert "code_verifier" not in authorization_url


def test_authorization_url_does_not_include_client_secret() -> None:
    authorization_url = _build_request().authorization_url

    assert "client_secret" not in authorization_url


def test_authorization_url_does_not_include_access_token_or_refresh_token() -> None:
    authorization_url = _build_request().authorization_url

    assert "access_token" not in authorization_url
    assert "refresh_token" not in authorization_url


def test_build_authorization_request_rejects_auth_base_url_with_query() -> None:
    with pytest.raises(ValueError, match="query"):
        _build_request(auth_base_url="https://secure.soundcloud.com?bad=true")


def test_build_authorization_request_rejects_auth_base_url_with_fragment() -> None:
    with pytest.raises(ValueError, match="fragment"):
        _build_request(auth_base_url="https://secure.soundcloud.com#bad")


def test_build_authorization_request_rejects_auth_base_url_with_userinfo() -> None:
    with pytest.raises(ValueError, match="userinfo"):
        _build_request(auth_base_url="https://user:pass@secure.soundcloud.com")


def test_oauth_redirect_uri_rejects_fragments() -> None:
    with pytest.raises(ValueError, match="fragment"):
        OAuthRedirectUri(value="http://localhost:8080/callback#bad")


def test_oauth_redirect_uri_accepts_localhost_http_redirect_uri() -> None:
    redirect_uri = OAuthRedirectUri(value="http://localhost:8080/callback")

    assert redirect_uri.value == "http://localhost:8080/callback"


def test_oauth_authorization_request_is_immutable() -> None:
    request = _build_request()

    with pytest.raises(Exception, match="frozen"):
        request.authorization_url = "https://example.test/other"


def test_no_test_performs_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in OAuth PKCE tests.")

    import socket

    monkeypatch.setattr(socket, "create_connection", fail_network_call)
    _build_request()


def test_no_test_writes_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("File writes are not allowed in OAuth PKCE tests.")

    monkeypatch.setattr("pathlib.Path.write_text", fail_file_write)
    monkeypatch.setattr("pathlib.Path.write_bytes", fail_file_write)
    _build_request()


def _authorization_query() -> dict[str, list[str]]:
    return parse_qs(urlparse(_build_request().authorization_url).query)


def _build_request(
    auth_base_url: str = "https://secure.soundcloud.com/oauth2/",
):
    return OAuthPKCEService().build_authorization_request(
        auth_base_url=auth_base_url,
        client_id=OAuthClientId(value=SecretStr("client-id")),
        redirect_uri=OAuthRedirectUri(value="http://localhost:8080/callback"),
        code_challenge=OAuthCodeChallenge(value="challenge"),
        state=OAuthState(value=SecretStr("state")),
    )
