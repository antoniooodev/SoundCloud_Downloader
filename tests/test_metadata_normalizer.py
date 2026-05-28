import socket

import pytest
from pydantic import ValidationError

from soundcloud_downloader.application import (
    SoundCloudMetadataNormalizationError,
    SoundCloudMetadataNormalizer,
)
from soundcloud_downloader.application.ports import (
    SoundCloudPlaylistSummary,
    SoundCloudResolvedResource,
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
    SoundCloudTrackSummary,
    SoundCloudTranscodingSummary,
    SoundCloudUserSummary,
)
from soundcloud_downloader.domain import (
    MediaCodec,
    MediaContainer,
    NormalizedResolverInput,
    ResolverInputType,
    SoundCloudArtworkUrl,
    SoundCloudMetadataKind,
    SoundCloudPermalinkUrl,
    SoundCloudResourceType,
    SourceProtocol,
)


def normalized() -> NormalizedResolverInput:
    return NormalizedResolverInput(
        input_type=ResolverInputType.URL,
        resource_type=SoundCloudResourceType.TRACK,
        normalized_url="https://soundcloud.com/user/track",
        normalized_path="/user/track",
        host="soundcloud.com",
        path_parts=("user", "track"),
    )


def user_summary() -> SoundCloudUserSummary:
    return SoundCloudUserSummary(
        soundcloud_id="user-1",
        username="artist",
        permalink_url_redacted="https://soundcloud.com/artist",
        avatar_url_redacted="https://i.example.invalid/avatar.jpg",
    )


def track_summary() -> SoundCloudTrackSummary:
    return SoundCloudTrackSummary(
        soundcloud_id="track-1",
        title="Example Track",
        duration_ms=123_000,
        permalink_url_redacted="https://soundcloud.com/artist/example-track",
        artwork_url_redacted="https://i.example.invalid/artwork.jpg",
        user=user_summary(),
        is_public=True,
        is_downloadable=True,
        transcodings=(
            SoundCloudTranscodingSummary(
                preset="mp3_1_0",
                protocol=SourceProtocol.PROGRESSIVE,
                mime_type="audio/mpeg",
                codec=MediaCodec.MP3,
                container=MediaContainer.MP3,
            ),
        ),
    )


def playlist_summary() -> SoundCloudPlaylistSummary:
    return SoundCloudPlaylistSummary(
        soundcloud_id="playlist-1",
        title="Example Playlist",
        permalink_url_redacted="https://soundcloud.com/artist/sets/example-playlist",
        artwork_url_redacted="https://i.example.invalid/playlist.jpg",
        user=user_summary(),
        track_count=1,
        tracks=(track_summary(),),
    )


def resolved_track(track: SoundCloudTrackSummary | None = None) -> SoundCloudResolvedResource:
    return SoundCloudResolvedResource(
        status=SoundCloudResolveStatus.RESOLVED,
        kind=SoundCloudResourceKind.TRACK,
        normalized=normalized(),
        track=track if track is not None else track_summary(),
    )


def resolved_playlist(
    playlist: SoundCloudPlaylistSummary | None = None,
) -> SoundCloudResolvedResource:
    return SoundCloudResolvedResource(
        status=SoundCloudResolveStatus.RESOLVED,
        kind=SoundCloudResourceKind.PLAYLIST,
        normalized=normalized(),
        playlist=playlist if playlist is not None else playlist_summary(),
    )


def resolved_user(user: SoundCloudUserSummary | None = None) -> SoundCloudResolvedResource:
    return SoundCloudResolvedResource(
        status=SoundCloudResolveStatus.RESOLVED,
        kind=SoundCloudResourceKind.USER,
        normalized=normalized(),
        user=user if user is not None else user_summary(),
    )


def normalize(resource: SoundCloudResolvedResource):
    return SoundCloudMetadataNormalizer().normalize(resource)


def test_normalizes_resolved_track_resource() -> None:
    metadata = normalize(resolved_track())

    assert metadata.kind is SoundCloudMetadataKind.TRACK
    assert metadata.id.value == "track-1"
    assert metadata.title == "Example Track"
    assert metadata.duration_ms == 123_000
    assert metadata.permalink_url is not None
    assert metadata.permalink_url.value == "https://soundcloud.com/artist/example-track"
    assert metadata.artwork_url is not None
    assert metadata.artwork_url.value == "https://i.example.invalid/artwork.jpg"
    assert metadata.user is not None
    assert metadata.user.username == "artist"
    assert metadata.streamable is True
    assert metadata.downloadable is True
    assert metadata.access == "public"


def test_normalizes_resolved_playlist_resource() -> None:
    metadata = normalize(resolved_playlist())

    assert metadata.kind is SoundCloudMetadataKind.PLAYLIST
    assert metadata.id.value == "playlist-1"
    assert metadata.title == "Example Playlist"
    assert metadata.track_count == 1
    assert metadata.duration_ms == 123_000
    assert len(metadata.tracks) == 1
    assert metadata.tracks[0].id.value == "track-1"


def test_normalizes_resolved_user_resource() -> None:
    metadata = normalize(resolved_user())

    assert metadata.kind is SoundCloudMetadataKind.USER
    assert metadata.id.value == "user-1"
    assert metadata.username == "artist"
    assert metadata.display_name is None
    assert metadata.permalink_url is not None
    assert metadata.permalink_url.value == "https://soundcloud.com/artist"


def test_rejects_unsupported_resource_kind_safely() -> None:
    resource = SoundCloudResolvedResource(
        status=SoundCloudResolveStatus.UNSUPPORTED,
        kind=SoundCloudResourceKind.UNKNOWN,
        normalized=normalized(),
    )

    with pytest.raises(SoundCloudMetadataNormalizationError) as exc_info:
        normalize(resource)

    assert "Unsupported SoundCloud metadata resource." in str(exc_info.value)


def test_rejects_missing_required_id_safely() -> None:
    track = SoundCloudTrackSummary(soundcloud_id="", title="Track")

    with pytest.raises(SoundCloudMetadataNormalizationError) as exc_info:
        normalize(resolved_track(track))

    assert "Malformed SoundCloud metadata resource." in str(exc_info.value)


@pytest.mark.parametrize(
    "track",
    [
        SoundCloudTrackSummary(soundcloud_id="track-1", title=""),
        SoundCloudTrackSummary.model_construct(
            soundcloud_id="track-1",
            title="Track",
            duration_ms=-1,
            permalink_url_redacted=None,
            artwork_url_redacted=None,
            user=None,
            is_public=False,
            is_go_plus=False,
            is_preview_only=False,
            is_downloadable=False,
            transcodings=(),
        ),
    ],
)
def test_rejects_malformed_track_fields(track: SoundCloudTrackSummary) -> None:
    with pytest.raises(SoundCloudMetadataNormalizationError):
        normalize(resolved_track(track))


@pytest.mark.parametrize(
    "playlist",
    [
        SoundCloudPlaylistSummary(soundcloud_id="playlist-1", title=""),
        SoundCloudPlaylistSummary.model_construct(
            soundcloud_id="playlist-1",
            title="Playlist",
            permalink_url_redacted=None,
            artwork_url_redacted=None,
            user=None,
            track_count=-1,
            tracks=(),
        ),
    ],
)
def test_rejects_malformed_playlist_fields(playlist: SoundCloudPlaylistSummary) -> None:
    with pytest.raises(SoundCloudMetadataNormalizationError):
        normalize(resolved_playlist(playlist))


def test_rejects_empty_username() -> None:
    user = SoundCloudUserSummary(soundcloud_id="user-1", username="")

    with pytest.raises(SoundCloudMetadataNormalizationError):
        normalize(resolved_user(user))


@pytest.mark.parametrize(
    "url_model",
    [
        lambda: SoundCloudPermalinkUrl(value="https://user:pass@soundcloud.com/user"),
        lambda: SoundCloudPermalinkUrl(value="https://soundcloud.com/user?access_token=raw"),
        lambda: SoundCloudArtworkUrl(value="https://i.example.invalid/a.jpg?client_secret=raw"),
    ],
)
def test_domain_url_models_reject_unsafe_urls(url_model) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        url_model()


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("https://soundcloud.com/user?access_token=raw-access-token", "raw-access-token"),
        ("https://soundcloud.com/user?client_secret=raw-client-secret", "raw-client-secret"),
    ],
)
def test_normalizer_rejects_unsafe_urls_without_leaking_secret(url: str, secret: str) -> None:
    track = SoundCloudTrackSummary.model_construct(
        soundcloud_id="track-1",
        title="Track",
        duration_ms=None,
        permalink_url_redacted=url,
        artwork_url_redacted=None,
        user=None,
        is_public=False,
        is_go_plus=False,
        is_preview_only=False,
        is_downloadable=False,
        transcodings=(),
    )

    with pytest.raises(SoundCloudMetadataNormalizationError) as exc_info:
        normalize(resolved_track(track))

    assert secret not in str(exc_info.value)


def test_model_repr_and_dump_do_not_contain_sensitive_field_names() -> None:
    metadata = normalize(resolved_track())
    dumped = repr(metadata) + str(metadata.model_dump(mode="json"))

    for forbidden in (
        "access_token",
        "refresh_token",
        "client_secret",
        "stream_url",
        "manifest_url",
    ):
        assert forbidden not in dumped


def test_exception_messages_do_not_contain_raw_sensitive_values() -> None:
    secret = "raw-access-token"
    track = SoundCloudTrackSummary.model_construct(
        soundcloud_id="track-1",
        title="Track",
        duration_ms=None,
        permalink_url_redacted=f"https://soundcloud.com/user?access_token={secret}",
        artwork_url_redacted=None,
        user=None,
        is_public=False,
        is_go_plus=False,
        is_preview_only=False,
        is_downloadable=False,
        transcodings=(),
    )

    with pytest.raises(SoundCloudMetadataNormalizationError) as exc_info:
        normalize(resolved_track(track))

    assert secret not in str(exc_info.value)


def test_domain_models_are_immutable() -> None:
    metadata = normalize(resolved_track())

    with pytest.raises(ValidationError):
        metadata.title = "Changed"


def test_normalizer_performs_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    metadata = normalize(resolved_track())

    assert metadata.id.value == "track-1"


def test_normalizer_writes_no_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    before = set(tmp_path.iterdir())

    metadata = normalize(resolved_playlist())

    assert metadata.id.value == "playlist-1"
    assert set(tmp_path.iterdir()) == before
