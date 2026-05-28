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
    SoundCloudTranscodingMetadata,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
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


def official_track_payload() -> dict[str, object]:
    return {
        "kind": "track",
        "id": 123,
        "title": "Official track",
        "duration": 42_000,
        "permalink": "official-track",
        "permalink_url": "https://soundcloud.com/user/official-track",
        "artwork_url": "https://i.example.invalid/artwork.jpg",
        "sharing": "public",
        "downloadable": False,
        "user": {
            "kind": "user",
            "id": 456,
            "username": "artist",
            "permalink": "artist",
            "permalink_url": "https://soundcloud.com/artist",
        },
        "media": {
            "transcodings": [
                {
                    "url": "https://api.soundcloud.invalid/media/secret-stream-url",
                    "preset": "mp3_1_0",
                    "quality": "sq",
                    "format": {
                        "protocol": "progressive",
                        "mime_type": "audio/mpeg",
                    },
                }
            ]
        },
    }


def official_playlist_payload() -> dict[str, object]:
    return {
        "kind": "playlist",
        "id": 789,
        "title": "Official playlist",
        "permalink_url": "https://soundcloud.com/user/sets/official-playlist",
        "track_count": 1,
        "user": {
            "kind": "user",
            "id": 456,
            "username": "artist",
        },
        "tracks": [official_track_payload()],
    }


def official_user_payload() -> dict[str, object]:
    return {
        "kind": "user",
        "id": 456,
        "username": "artist",
        "permalink_url": "https://soundcloud.com/artist",
        "avatar_url": "https://i.example.invalid/avatar.jpg",
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


def test_maps_official_like_track_payload() -> None:
    resource = SoundCloudResponseMapper().map_resolved_resource(
        official_track_payload(),
        normalized(),
    )

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.TRACK
    assert resource.track is not None
    assert resource.track.soundcloud_id == "123"
    assert resource.track.title == "Official track"
    assert resource.track.user is not None
    assert resource.track.user.username == "artist"


def test_track_payload_maps_hls_transcoding_metadata() -> None:
    payload = official_track_payload()
    transcoding_payload = _first_official_transcoding(payload)
    format_payload = transcoding_payload["format"]
    assert isinstance(format_payload, dict)
    format_payload["protocol"] = "hls"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    transcoding = resource.track.transcodings[0]
    assert isinstance(transcoding, SoundCloudTranscodingMetadata)
    assert transcoding.format.protocol is SoundCloudTranscodingProtocol.HLS
    assert transcoding.format.mime_type is SoundCloudTranscodingMimeType.AUDIO_MPEG
    assert transcoding.preset == "mp3_1_0"
    assert transcoding.quality == "sq"
    assert transcoding.is_hls is True


def test_track_payload_maps_progressive_transcoding_metadata() -> None:
    resource = SoundCloudResponseMapper().map_resolved_resource(
        official_track_payload(),
        normalized(),
    )

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    transcoding = resource.track.transcodings[0]
    assert isinstance(transcoding, SoundCloudTranscodingMetadata)
    assert transcoding.format.protocol is SoundCloudTranscodingProtocol.PROGRESSIVE
    assert transcoding.is_progressive is True


def test_track_payload_with_missing_media_maps_empty_transcodings() -> None:
    payload = official_track_payload()
    payload.pop("media")

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    assert resource.track.transcodings == ()


def test_track_payload_with_empty_transcodings_maps_empty_tuple() -> None:
    payload = official_track_payload()
    media = payload["media"]
    assert isinstance(media, dict)
    media["transcodings"] = []

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    assert resource.track.transcodings == ()


def test_unknown_transcoding_protocol_maps_unknown() -> None:
    payload = official_track_payload()
    transcoding_payload = _first_official_transcoding(payload)
    format_payload = transcoding_payload["format"]
    assert isinstance(format_payload, dict)
    format_payload["protocol"] = "future-protocol"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    transcoding = resource.track.transcodings[0]
    assert isinstance(transcoding, SoundCloudTranscodingMetadata)
    assert transcoding.format.protocol is SoundCloudTranscodingProtocol.UNKNOWN


def test_unknown_transcoding_mime_type_maps_unknown() -> None:
    payload = official_track_payload()
    transcoding_payload = _first_official_transcoding(payload)
    format_payload = transcoding_payload["format"]
    assert isinstance(format_payload, dict)
    format_payload["mime_type"] = "audio/future"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    transcoding = resource.track.transcodings[0]
    assert isinstance(transcoding, SoundCloudTranscodingMetadata)
    assert transcoding.format.mime_type is SoundCloudTranscodingMimeType.UNKNOWN


def test_invalid_transcodings_type_rejects_safely() -> None:
    payload = official_track_payload()
    media = payload["media"]
    assert isinstance(media, dict)
    media["transcodings"] = "not-a-sequence"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert resource.kind is SoundCloudResourceKind.UNKNOWN
    assert resource.warnings == ("SoundCloud resolver payload was malformed.",)


def test_invalid_transcoding_format_type_rejects_safely() -> None:
    payload = official_track_payload()
    _first_official_transcoding(payload)["format"] = "not-a-mapping"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert resource.warnings == ("SoundCloud resolver payload was malformed.",)


def test_invalid_transcoding_endpoint_url_rejects_safely() -> None:
    payload = official_track_payload()
    _first_official_transcoding(payload)["url"] = "/relative/transcoding"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert resource.warnings == ("SoundCloud resolver payload was malformed.",)


def test_raw_transcoding_url_does_not_appear_in_mapped_dto_repr() -> None:
    raw_url = "https://api.soundcloud.invalid/media/secret-stream-url"

    resource = SoundCloudResponseMapper().map_resolved_resource(
        official_track_payload(),
        normalized(),
    )

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert raw_url not in repr(resource)


def test_raw_transcoding_url_does_not_appear_in_mapped_dto_model_dump() -> None:
    raw_url = "https://api.soundcloud.invalid/media/secret-stream-url"

    resource = SoundCloudResponseMapper().map_resolved_resource(
        official_track_payload(),
        normalized(),
    )

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert raw_url not in str(resource.model_dump(mode="json"))


def test_raw_transcoding_url_does_not_appear_in_exception_messages() -> None:
    raw_url = "https://api.soundcloud.invalid/media/transcoding?access_token=raw-secret"
    payload = official_track_payload()
    _first_official_transcoding(payload)["url"] = raw_url

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert raw_url not in " ".join(resource.warnings)
    assert "raw-secret" not in " ".join(resource.warnings)


def test_maps_official_like_playlist_payload() -> None:
    resource = SoundCloudResponseMapper().map_resolved_resource(
        official_playlist_payload(),
        normalized(),
    )

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.PLAYLIST
    assert resource.playlist is not None
    assert resource.playlist.soundcloud_id == "789"
    assert resource.playlist.title == "Official playlist"
    assert resource.playlist.track_count == 1
    assert len(resource.playlist.tracks) == 1


def test_maps_official_like_user_payload() -> None:
    resource = SoundCloudResponseMapper().map_resolved_resource(
        official_user_payload(),
        normalized(),
    )

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.USER
    assert resource.user is not None
    assert resource.user.soundcloud_id == "456"
    assert resource.user.username == "artist"


def test_maps_official_like_profile_payload_as_user() -> None:
    payload = official_user_payload()
    payload["kind"] = "profile"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.USER
    assert resource.user is not None


def test_ignores_unknown_harmless_official_fields() -> None:
    payload = official_track_payload()
    payload["harmless_extra"] = {"nested": "value"}

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    assert resource.track.soundcloud_id == "123"


def test_rejects_unsupported_official_kind() -> None:
    resource = SoundCloudResponseMapper().map_resolved_resource(
        {"kind": "comment", "id": 1},
        normalized(),
    )

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert resource.kind is SoundCloudResourceKind.UNKNOWN


def test_rejects_official_payload_missing_id() -> None:
    payload = official_track_payload()
    payload.pop("id")

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR


def test_rejects_invalid_official_id_type() -> None:
    payload = official_track_payload()
    payload["id"] = ["not-valid"]

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR


def test_rejects_invalid_official_track_title_type() -> None:
    payload = official_track_payload()
    payload["title"] = 123

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR


def test_rejects_invalid_official_playlist_title_type() -> None:
    payload = official_playlist_payload()
    payload["title"] = 123

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR


def test_rejects_invalid_official_username_type() -> None:
    payload = official_user_payload()
    payload["username"] = 123

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR


@pytest.mark.parametrize(
    ("secret_key", "secret_value"),
    [
        ("access_token", "raw-access-token"),
        ("refresh_token", "raw-refresh-token"),
        ("client_secret", "raw-client-secret"),
        ("cookie", "raw-cookie"),
    ],
)
def test_sensitive_official_payload_fields_do_not_leak(
    secret_key: str,
    secret_value: str,
) -> None:
    payload = official_track_payload()
    payload[secret_key] = secret_value

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())
    dumped = repr(resource) + str(resource.model_dump(mode="json"))

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert secret_value not in dumped


def test_official_media_transcoding_url_is_not_exposed() -> None:
    raw_url = "https://api.soundcloud.invalid/media/secret-stream-url"

    resource = SoundCloudResponseMapper().map_resolved_resource(
        official_track_payload(),
        normalized(),
    )
    dumped = repr(resource) + str(resource.model_dump(mode="json"))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert raw_url not in dumped


def test_mapper_exception_messages_do_not_contain_sensitive_values() -> None:
    secret_value = "raw-access-token"
    payload = official_track_payload()
    payload["access_token"] = secret_value

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.warnings
    assert secret_value not in " ".join(resource.warnings)


def _first_official_transcoding(payload: dict[str, object]) -> dict[str, object]:
    media = payload["media"]
    assert isinstance(media, dict)
    transcodings = media["transcodings"]
    assert isinstance(transcodings, list)
    first_transcoding = transcodings[0]
    assert isinstance(first_transcoding, dict)
    return first_transcoding
