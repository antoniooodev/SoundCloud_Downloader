import pytest
from pydantic import ValidationError

from soundcloud_downloader.application import ResolverInputNormalizer
from soundcloud_downloader.domain import (
    NormalizedResolverInput,
    ResolverInputType,
    SoundCloudResourceType,
)


def normalize(value: str) -> NormalizedResolverInput:
    return ResolverInputNormalizer().normalize(value)


def test_normalizes_soundcloud_track_url() -> None:
    result = normalize("https://soundcloud.com/user/track")

    assert result.input_type is ResolverInputType.URL
    assert result.resource_type is SoundCloudResourceType.TRACK
    assert result.normalized_url == "https://soundcloud.com/user/track"
    assert result.normalized_path == "/user/track"
    assert result.host == "soundcloud.com"
    assert result.path_parts == ("user", "track")


def test_normalizes_http_soundcloud_url_to_https() -> None:
    result = normalize("http://soundcloud.com/user/track")

    assert result.normalized_url == "https://soundcloud.com/user/track"


def test_strips_query_strings_and_fragments() -> None:
    result = normalize("https://soundcloud.com/user/track?token=secret#comments")

    assert result.normalized_url == "https://soundcloud.com/user/track"
    assert "token=secret" not in result.normalized_url
    assert "#" not in result.normalized_url


def test_normalizes_www_host_to_soundcloud_host() -> None:
    result = normalize("https://www.soundcloud.com/user/track")

    assert result.host == "soundcloud.com"
    assert result.normalized_url == "https://soundcloud.com/user/track"


def test_normalizes_mobile_host_to_soundcloud_host() -> None:
    result = normalize("https://m.soundcloud.com/user/track")

    assert result.host == "soundcloud.com"
    assert result.normalized_url == "https://soundcloud.com/user/track"


def test_classifies_user_url() -> None:
    result = normalize("https://soundcloud.com/user")

    assert result.resource_type is SoundCloudResourceType.USER
    assert result.path_parts == ("user",)


def test_classifies_playlist_url() -> None:
    result = normalize("https://soundcloud.com/user/sets/playlist")

    assert result.resource_type is SoundCloudResourceType.PLAYLIST
    assert result.path_parts == ("user", "sets", "playlist")


def test_classifies_playlist_url_with_extra_path_parts_and_warning() -> None:
    result = normalize("https://soundcloud.com/user/sets/playlist/extra")

    assert result.resource_type is SoundCloudResourceType.PLAYLIST
    assert result.warnings


def test_classifies_shortlink_as_requiring_network_resolution() -> None:
    result = normalize("https://on.soundcloud.com/abc123")

    assert result.resource_type is SoundCloudResourceType.SHORTLINK
    assert result.requires_network_resolution is True
    assert result.normalized_url == "https://on.soundcloud.com/abc123"


@pytest.mark.parametrize("path", ["/stream", "/discover"])
def test_classifies_reserved_routes_as_unknown_with_warning(path: str) -> None:
    result = normalize(f"https://soundcloud.com{path}")

    assert result.resource_type is SoundCloudResourceType.UNKNOWN
    assert result.warnings


def test_unsupported_host_becomes_unknown_with_warning_and_sanitized_url() -> None:
    result = normalize("https://example.test/user/track?token=secret#frag")

    assert result.input_type is ResolverInputType.URL
    assert result.resource_type is SoundCloudResourceType.UNKNOWN
    assert result.normalized_url == "https://example.test/user/track"
    assert result.host == "example.test"
    assert result.warnings


def test_raw_text_becomes_raw_text_unknown_with_warning() -> None:
    result = normalize("artist track name")

    assert result.input_type is ResolverInputType.RAW_TEXT
    assert result.resource_type is SoundCloudResourceType.UNKNOWN
    assert result.normalized_url is None
    assert result.normalized_path is None
    assert result.host is None
    assert result.path_parts == ()
    assert result.warnings


def test_duplicate_slashes_in_path_are_normalized() -> None:
    result = normalize("https://soundcloud.com//user///track")

    assert result.normalized_url == "https://soundcloud.com/user/track"
    assert result.normalized_path == "/user/track"
    assert result.path_parts == ("user", "track")


def test_trailing_slash_is_removed_except_root() -> None:
    track = normalize("https://soundcloud.com/user/track/")
    root = normalize("https://soundcloud.com/")

    assert track.normalized_url == "https://soundcloud.com/user/track"
    assert root.normalized_url == "https://soundcloud.com/"
    assert root.normalized_path == "/"


def test_domain_model_rejects_normalized_url_with_query_string() -> None:
    with pytest.raises(ValidationError):
        NormalizedResolverInput(
            input_type=ResolverInputType.URL,
            resource_type=SoundCloudResourceType.UNKNOWN,
            normalized_url="https://soundcloud.com/user?token=secret",
        )


def test_domain_model_rejects_normalized_url_with_fragment() -> None:
    with pytest.raises(ValidationError):
        NormalizedResolverInput(
            input_type=ResolverInputType.URL,
            resource_type=SoundCloudResourceType.UNKNOWN,
            normalized_url="https://soundcloud.com/user#frag",
        )


def test_domain_model_rejects_empty_path_parts() -> None:
    with pytest.raises(ValidationError):
        NormalizedResolverInput(
            input_type=ResolverInputType.URL,
            resource_type=SoundCloudResourceType.TRACK,
            normalized_url="https://soundcloud.com/user/track",
            path_parts=("user", "", "track"),
        )


def test_normalizer_uses_input_strings_only() -> None:
    result = normalize("https://soundcloud.com/user/track")

    assert result.resource_type is SoundCloudResourceType.TRACK
