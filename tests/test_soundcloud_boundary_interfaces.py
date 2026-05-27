import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from soundcloud_downloader.application.ports import (
    SoundCloudMetadataPort,
    SoundCloudPlaylistSummary,
    SoundCloudResolveStatus,
    SoundCloudResolvedResource,
    SoundCloudResolverPort,
    SoundCloudResourceKind,
    SoundCloudTrackSummary,
    SoundCloudTranscodingSummary,
    SoundCloudUserSummary,
)
from soundcloud_downloader.domain import (
    NormalizedResolverInput,
    ResolverInputType,
    SoundCloudResourceType,
)


def normalized_track() -> NormalizedResolverInput:
    return NormalizedResolverInput(
        input_type=ResolverInputType.URL,
        resource_type=SoundCloudResourceType.TRACK,
        normalized_url="https://soundcloud.com/user/track",
        normalized_path="/user/track",
        host="soundcloud.com",
        path_parts=("user", "track"),
    )


def test_user_summary_is_immutable() -> None:
    user = SoundCloudUserSummary(soundcloud_id="u1")

    with pytest.raises(ValidationError):
        user.username = "new-name"


def test_track_summary_is_immutable() -> None:
    track = SoundCloudTrackSummary(soundcloud_id="t1", title="Track")

    with pytest.raises(ValidationError):
        track.title = "New title"


def test_playlist_summary_is_immutable() -> None:
    playlist = SoundCloudPlaylistSummary(soundcloud_id="p1", title="Playlist")

    with pytest.raises(ValidationError):
        playlist.title = "New title"


def test_transcoding_summary_is_immutable() -> None:
    transcoding = SoundCloudTranscodingSummary(preset="mp3_0_1")

    with pytest.raises(ValidationError):
        transcoding.requires_auth = True


def test_track_summary_rejects_negative_duration() -> None:
    with pytest.raises(ValidationError):
        SoundCloudTrackSummary(soundcloud_id="t1", title="Track", duration_ms=-1)


def test_playlist_summary_rejects_negative_track_count() -> None:
    with pytest.raises(ValidationError):
        SoundCloudPlaylistSummary(soundcloud_id="p1", title="Playlist", track_count=-1)


@pytest.mark.parametrize(
    "model_type",
    [SoundCloudUserSummary, SoundCloudTrackSummary, SoundCloudPlaylistSummary],
)
def test_redacted_url_fields_reject_query_strings(model_type: type[BaseModel]) -> None:
    kwargs = _required_kwargs(model_type)

    with pytest.raises(ValidationError):
        model_type(**kwargs, permalink_url_redacted="https://soundcloud.com/user?token=secret")


@pytest.mark.parametrize(
    "model_type",
    [SoundCloudUserSummary, SoundCloudTrackSummary, SoundCloudPlaylistSummary],
)
def test_redacted_url_fields_reject_fragments(model_type: type[BaseModel]) -> None:
    kwargs = _required_kwargs(model_type)

    with pytest.raises(ValidationError):
        model_type(**kwargs, permalink_url_redacted="https://soundcloud.com/user#fragment")


def test_resolved_track_requires_track_payload() -> None:
    with pytest.raises(ValidationError):
        SoundCloudResolvedResource(
            status=SoundCloudResolveStatus.RESOLVED,
            kind=SoundCloudResourceKind.TRACK,
            normalized=normalized_track(),
        )


def test_resolved_playlist_requires_playlist_payload() -> None:
    with pytest.raises(ValidationError):
        SoundCloudResolvedResource(
            status=SoundCloudResolveStatus.RESOLVED,
            kind=SoundCloudResourceKind.PLAYLIST,
            normalized=normalized_track(),
        )


def test_resolved_user_requires_user_payload() -> None:
    with pytest.raises(ValidationError):
        SoundCloudResolvedResource(
            status=SoundCloudResolveStatus.RESOLVED,
            kind=SoundCloudResourceKind.USER,
            normalized=normalized_track(),
        )


def test_non_resolved_resource_can_omit_payloads() -> None:
    resource = SoundCloudResolvedResource(
        status=SoundCloudResolveStatus.NEEDS_NETWORK,
        kind=SoundCloudResourceKind.TRACK,
        normalized=normalized_track(),
    )

    assert resource.track is None
    assert resource.playlist is None
    assert resource.user is None


def test_fake_async_resolver_satisfies_resolver_port() -> None:
    assert isinstance(FakeResolver(), SoundCloudResolverPort)


def test_fake_metadata_provider_satisfies_metadata_port() -> None:
    assert isinstance(FakeMetadataProvider(), SoundCloudMetadataPort)


def test_fake_resolver_returns_resolved_track_resource() -> None:
    resource = asyncio.run(FakeResolver().resolve(normalized_track()))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.TRACK
    assert resource.track is not None
    assert resource.track.soundcloud_id == "track-1"


def test_dtos_do_not_include_sensitive_or_url_carrying_stream_fields() -> None:
    forbidden = {
        "endpoint_url",
        "manifest_url",
        "stream_url",
        "access_token",
        "refresh_token",
        "cookie",
        "authorization",
    }
    model_types = (
        SoundCloudUserSummary,
        SoundCloudTranscodingSummary,
        SoundCloudTrackSummary,
        SoundCloudPlaylistSummary,
        SoundCloudResolvedResource,
    )

    for model_type in model_types:
        fields = set(model_type.model_fields)
        assert fields.isdisjoint(forbidden)


class FakeResolver:
    async def resolve(self, normalized: NormalizedResolverInput) -> SoundCloudResolvedResource:
        return SoundCloudResolvedResource(
            status=SoundCloudResolveStatus.RESOLVED,
            kind=SoundCloudResourceKind.TRACK,
            normalized=normalized,
            track=SoundCloudTrackSummary(soundcloud_id="track-1", title="Track"),
        )


class FakeMetadataProvider:
    async def get_track(self, soundcloud_id: str) -> SoundCloudTrackSummary:
        return SoundCloudTrackSummary(soundcloud_id=soundcloud_id, title="Track")

    async def get_playlist(self, soundcloud_id: str) -> SoundCloudPlaylistSummary:
        return SoundCloudPlaylistSummary(soundcloud_id=soundcloud_id, title="Playlist")

    async def get_user(self, soundcloud_id: str) -> SoundCloudUserSummary:
        return SoundCloudUserSummary(soundcloud_id=soundcloud_id)


def _required_kwargs(model_type: type[BaseModel]) -> dict[str, Any]:
    if model_type is SoundCloudUserSummary:
        return {"soundcloud_id": "u1"}
    if model_type is SoundCloudTrackSummary:
        return {"soundcloud_id": "t1", "title": "Track"}
    if model_type is SoundCloudPlaylistSummary:
        return {"soundcloud_id": "p1", "title": "Playlist"}
    raise AssertionError(f"Unexpected model type: {model_type}")
