import pytest
from pydantic import ValidationError

from soundcloud_downloader.application.ports import (
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
)
from soundcloud_downloader.domain import (
    NormalizedResolverInput,
    ResolverInputType,
    SoundCloudResourceType,
)
from soundcloud_downloader.infrastructure.soundcloud import SoundCloudResponseMapper


def normalized() -> NormalizedResolverInput:
    return NormalizedResolverInput(
        input_type=ResolverInputType.URL,
        resource_type=SoundCloudResourceType.TRACK,
        normalized_url="https://soundcloud.com/user/track",
        normalized_path="/user/track",
        host="soundcloud.com",
        path_parts=("user", "track"),
    )


def track_payload() -> dict[str, object]:
    return {
        "status": "resolved",
        "kind": "track",
        "track": {
            "soundcloud_id": "123",
            "title": "Example track",
            "duration_ms": 1000,
            "permalink": "example-track",
            "permalink_url_redacted": "https://soundcloud.com/user/example-track",
            "artwork_url_redacted": "https://i.example.invalid/artwork.jpg",
            "is_public": True,
            "is_go_plus": False,
            "is_preview_only": False,
            "is_downloadable": True,
            "transcodings": [
                {
                    "preset": "mp3_128",
                    "protocol": "progressive",
                    "mime_type": "audio/mpeg",
                    "quality": "standard",
                    "codec": "mp3",
                    "container": "mp3",
                    "requires_auth": False,
                    "is_downloadable": True,
                }
            ],
        },
        "warnings": [],
    }


def test_maps_valid_track_payload_to_resolved_track() -> None:
    resource = SoundCloudResponseMapper().map_resolved_resource(track_payload(), normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.TRACK
    assert resource.track is not None
    assert resource.track.soundcloud_id == "123"
    assert resource.track.transcodings[0].preset == "mp3_128"


def test_maps_valid_playlist_payload_to_resolved_playlist() -> None:
    payload = {
        "status": "resolved",
        "kind": "playlist",
        "playlist": {
            "soundcloud_id": "pl1",
            "title": "Playlist",
            "permalink_url_redacted": "https://soundcloud.com/user/sets/playlist",
            "track_count": 1,
            "tracks": [track_payload()["track"]],
        },
    }

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.PLAYLIST
    assert resource.playlist is not None
    assert resource.playlist.track_count == 1


def test_maps_valid_user_payload_to_resolved_user() -> None:
    payload = {
        "status": "resolved",
        "kind": "user",
        "user": {
            "soundcloud_id": "u1",
            "username": "user",
            "permalink_url_redacted": "https://soundcloud.com/user",
        },
    }

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.USER
    assert resource.user is not None
    assert resource.user.username == "user"


@pytest.mark.parametrize(
    "status",
    [SoundCloudResolveStatus.NEEDS_NETWORK, SoundCloudResolveStatus.NOT_FOUND],
)
def test_maps_unresolved_statuses_without_payload(status: SoundCloudResolveStatus) -> None:
    resource = SoundCloudResponseMapper().map_resolved_resource(
        {"status": status.value, "kind": "unknown"},
        normalized(),
    )

    assert resource.status is status
    assert resource.kind is SoundCloudResourceKind.UNKNOWN
    assert resource.track is None


def test_malformed_payload_maps_to_error() -> None:
    resource = SoundCloudResponseMapper().map_resolved_resource(
        {"status": "resolved", "kind": "track"},
        normalized(),
    )

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert resource.kind is SoundCloudResourceKind.UNKNOWN
    assert resource.warnings


@pytest.mark.parametrize("forbidden_key", ["stream_url", "manifest_url", "access_token"])
def test_payload_with_forbidden_keys_maps_to_error(forbidden_key: str) -> None:
    payload = track_payload()
    assert isinstance(payload["track"], dict)
    payload["track"][forbidden_key] = "secret"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert resource.warnings


def test_redacted_url_with_query_string_maps_to_error() -> None:
    payload = track_payload()
    assert isinstance(payload["track"], dict)
    payload["track"]["permalink_url_redacted"] = "https://soundcloud.com/user/track?token=secret"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert resource.warnings


def test_mapper_output_is_immutable() -> None:
    resource = SoundCloudResponseMapper().map_resolved_resource(track_payload(), normalized())

    with pytest.raises(ValidationError):
        resource.status = SoundCloudResolveStatus.ERROR


def test_mapper_uses_in_memory_payloads_only() -> None:
    resource = SoundCloudResponseMapper().map_resolved_resource(track_payload(), normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
