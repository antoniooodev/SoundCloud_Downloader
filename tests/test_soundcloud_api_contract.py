import json

import pytest
from pydantic import ValidationError

from soundcloud_downloader.application.resolver_input import ResolverInputNormalizer
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.infrastructure.http import HttpMethod
from soundcloud_downloader.infrastructure.soundcloud import (
    SoundCloudAccessToken,
    SoundCloudApiRequest,
    SoundCloudResolveRequestBuilder,
)


def normalized_track():
    return ResolverInputNormalizer().normalize(
        "https://soundcloud.com/user/track?token=secret#fragment"
    )


def token() -> SoundCloudAccessToken:
    return SoundCloudAccessToken(value="test-secret-token")


def test_default_official_api_base_url() -> None:
    assert AppSettings().soundcloud_api_base_url == "https://api.soundcloud.com"


def test_default_official_auth_base_url() -> None:
    assert AppSettings().soundcloud_auth_base_url == "https://secure.soundcloud.com"


def test_official_api_and_auth_base_urls_reject_query_strings() -> None:
    for field in ("soundcloud_api_base_url", "soundcloud_auth_base_url"):
        with pytest.raises(ValidationError):
            AppSettings(**{field: "https://soundcloud.com/base?token=secret"})


def test_official_api_and_auth_base_urls_reject_fragments() -> None:
    for field in ("soundcloud_api_base_url", "soundcloud_auth_base_url"):
        with pytest.raises(ValidationError):
            AppSettings(**{field: "https://soundcloud.com/base#fragment"})


def test_official_api_and_auth_base_urls_reject_userinfo_credentials() -> None:
    for field in ("soundcloud_api_base_url", "soundcloud_auth_base_url"):
        with pytest.raises(ValidationError):
            AppSettings(**{field: "https://user:pass@soundcloud.com"})


def test_official_api_and_auth_base_urls_strip_trailing_slash() -> None:
    settings = AppSettings(soundcloud_api_base_url="https://api.soundcloud.com/")

    assert settings.soundcloud_api_base_url == "https://api.soundcloud.com"


def test_soundcloud_access_token_does_not_expose_raw_token_in_repr() -> None:
    model = token()

    assert "test-secret-token" not in repr(model)


def test_soundcloud_access_token_model_dump_uses_safe_secret_representation() -> None:
    payload = token().model_dump(mode="json")

    assert payload["value"] == "**********"
    assert "test-secret-token" not in json.dumps(payload)


def test_resolve_request_builder_creates_resolve_url() -> None:
    request = SoundCloudResolveRequestBuilder(AppSettings()).build(normalized_track(), token())

    assert request.method is HttpMethod.GET
    assert request.url == "https://api.soundcloud.com/resolve"


def test_resolve_request_builder_uses_sanitized_normalized_url_param() -> None:
    request = SoundCloudResolveRequestBuilder(AppSettings()).build(normalized_track(), token())

    assert request.params["url"] == "https://soundcloud.com/user/track"
    assert "secret" not in request.params["url"]
    assert "fragment" not in request.params["url"]


def test_resolve_request_builder_rejects_missing_normalized_url() -> None:
    normalized = ResolverInputNormalizer().normalize("raw search text")

    with pytest.raises(ValueError):
        SoundCloudResolveRequestBuilder(AppSettings()).build(normalized, token())


def test_resolve_request_builder_rejects_normalized_url_with_query_string() -> None:
    normalized = normalized_track().model_copy(
        update={"normalized_url": "https://soundcloud.com/user/track?token=secret"}
    )

    with pytest.raises(ValueError):
        SoundCloudResolveRequestBuilder(AppSettings()).build(normalized, token())


def test_resolve_request_builder_adds_authorization_header_internally() -> None:
    request = SoundCloudResolveRequestBuilder(AppSettings()).build(normalized_track(), token())

    assert request.headers["Authorization"] == "OAuth test-secret-token"
    assert "test-secret-token" not in repr(request)
    assert "test-secret-token" not in json.dumps(request.model_dump(mode="json"))


def test_resolve_request_builder_does_not_add_cookies() -> None:
    request = SoundCloudResolveRequestBuilder(AppSettings()).build(normalized_track(), token())

    assert "cookie" not in {key.lower() for key in request.headers}


def test_resolve_request_builder_does_not_add_client_credentials_params() -> None:
    request = SoundCloudResolveRequestBuilder(AppSettings()).build(normalized_track(), token())

    assert "client_id" not in request.params
    assert "client_secret" not in request.params


def test_api_request_rejects_sensitive_params() -> None:
    for key in ("token", "cookie", "authorization", "client_id", "client_secret"):
        with pytest.raises(ValidationError):
            SoundCloudApiRequest(
                method=HttpMethod.GET,
                url="https://api.soundcloud.com/resolve",
                params={key: "secret"},
            )


def test_api_request_rejects_url_query_strings_and_fragments() -> None:
    for url in (
        "https://api.soundcloud.com/resolve?token=secret",
        "https://api.soundcloud.com/resolve#fragment",
    ):
        with pytest.raises(ValidationError):
            SoundCloudApiRequest(method=HttpMethod.GET, url=url)
