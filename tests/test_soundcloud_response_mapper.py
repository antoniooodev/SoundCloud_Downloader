import logging

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
from soundcloud_downloader.infrastructure.soundcloud import (
    SoundCloudResponseMapper,
    summarize_soundcloud_payload_shape,
)

RAW_RESOLVER_STREAM_URL = (
    "https://api.soundcloud.test/tracks/123/stream?client_secret=SHOULD_NOT_LEAK"
)
RAW_REAL_TRANSCODING_URL = (
    "https://api.soundcloud.test/media/soundcloud:tracks:123/abc/stream/hls"
    "?client_secret=SHOULD_NOT_LEAK"
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
                    "url": RAW_REAL_TRANSCODING_URL,
                    "preset": "mp3_1_0",
                    "quality": "sq",
                    "duration": 12_345,
                    "snipped": False,
                    "format": {
                        "protocol": "hls",
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
    payload = official_track_payload()
    payload["urn"] = "soundcloud:tracks:123"
    resource = SoundCloudResponseMapper().map_resolved_resource(
        payload,
        normalized(),
    )

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.TRACK
    assert resource.track is not None
    assert resource.track.soundcloud_id == "123"
    assert resource.track.soundcloud_urn == "soundcloud:tracks:123"
    assert resource.track.title == "Official track"
    assert resource.track.user is not None
    assert resource.track.user.username == "artist"


def test_maps_official_track_payload_with_top_level_stream_url() -> None:
    payload = official_track_payload()
    payload["stream_url"] = RAW_RESOLVER_STREAM_URL

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.TRACK
    assert resource.track is not None
    assert resource.track.transcodings
    assert resource.invalid_fields == ()


def test_top_level_stream_url_is_not_exposed_in_repr() -> None:
    payload = official_track_payload()
    payload["stream_url"] = RAW_RESOLVER_STREAM_URL

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert RAW_RESOLVER_STREAM_URL not in repr(resource)
    assert "SHOULD_NOT_LEAK" not in repr(resource)


def test_top_level_stream_url_is_not_exposed_in_model_dump() -> None:
    payload = official_track_payload()
    payload["stream_url"] = RAW_RESOLVER_STREAM_URL

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())
    dumped = str(resource.model_dump(mode="json"))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert RAW_RESOLVER_STREAM_URL not in dumped
    assert "SHOULD_NOT_LEAK" not in dumped
    assert "stream_url" not in dumped


def test_top_level_stream_url_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    payload = official_track_payload()
    payload["stream_url"] = RAW_RESOLVER_STREAM_URL

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert RAW_RESOLVER_STREAM_URL not in caplog.text
    assert "SHOULD_NOT_LEAK" not in caplog.text


def test_transcoding_endpoint_url_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    payload = official_track_payload()

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert RAW_REAL_TRANSCODING_URL not in caplog.text
    assert "SHOULD_NOT_LEAK" not in caplog.text


def test_maps_real_like_official_track_payload_with_extra_fields() -> None:
    payload = official_track_payload()
    payload.update(
        {
            "policy": "ALLOW",
            "publisher_metadata": {"artist": "artist"},
            "streamable": True,
            "extra": {"ignored": True},
        }
    )

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.kind is SoundCloudResourceKind.TRACK
    assert resource.track is not None
    assert resource.track.soundcloud_id == "123"


def test_maps_official_track_with_null_artwork_url() -> None:
    payload = official_track_payload()
    payload["artwork_url"] = None

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    assert resource.track.artwork_url_redacted is None


def test_maps_official_track_with_null_publisher_metadata() -> None:
    payload = official_track_payload()
    payload["publisher_metadata"] = None

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None


def test_maps_official_track_with_partial_nested_user() -> None:
    payload = official_track_payload()
    payload["user"] = {"id": 456}

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    assert resource.track.user is not None
    assert resource.track.user.soundcloud_id == "456"
    assert resource.track.user.username is None


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
    assert transcoding.duration_ms == 12_345
    assert transcoding.snipped is False
    assert transcoding.is_hls is True


def test_track_payload_preserves_official_media_transcodings() -> None:
    payload = official_track_payload()
    media = payload["media"]
    assert isinstance(media, dict)
    media["transcodings"] = [
        {
            "url": "https://api.soundcloud.invalid/media/hls",
            "preset": "aac_0_1",
            "quality": "sq",
            "format": {"protocol": "hls", "mime_type": "audio/mp4"},
        },
        {
            "url": "https://api.soundcloud.invalid/media/progressive",
            "preset": "mp3_1_0",
            "quality": "sq",
            "format": {"protocol": "progressive", "mime_type": "audio/mpeg"},
        },
    ]

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    assert len(resource.track.transcodings) == 2


def test_track_payload_maps_hls_transcoding_mime_type() -> None:
    payload = official_track_payload()
    transcoding_payload = _first_official_transcoding(payload)
    format_payload = transcoding_payload["format"]
    assert isinstance(format_payload, dict)
    format_payload["protocol"] = "hls"
    format_payload["mime_type"] = "audio/mp4"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    transcoding = resource.track.transcodings[0]
    assert isinstance(transcoding, SoundCloudTranscodingMetadata)
    assert transcoding.format.mime_type is SoundCloudTranscodingMimeType.AUDIO_MP4


def test_track_payload_maps_progressive_transcoding_metadata() -> None:
    payload = official_track_payload()
    transcoding_payload = _first_official_transcoding(payload)
    format_payload = transcoding_payload["format"]
    assert isinstance(format_payload, dict)
    format_payload["protocol"] = "progressive"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

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
    assert resource.invalid_fields == ("media.transcodings.0.url",)


def test_signed_transcoding_endpoint_url_is_preserved_and_redacted() -> None:
    payload = official_track_payload()

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())
    dumped = repr(resource) + str(resource.model_dump(mode="json"))

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert resource.track is not None
    assert isinstance(resource.track.transcodings[0], SoundCloudTranscodingMetadata)
    assert resource.track.transcodings[0].endpoint_url.get_secret_value() == RAW_REAL_TRANSCODING_URL
    assert RAW_REAL_TRANSCODING_URL not in dumped
    assert "SHOULD_NOT_LEAK" not in dumped


def test_invalid_fields_do_not_contain_source_url() -> None:
    payload = official_track_payload()
    payload["title"] = 123

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.invalid_fields == ("title",)
    assert "https://soundcloud.com/user/track" not in ",".join(resource.invalid_fields)


def test_invalid_fields_do_not_contain_transcoding_or_stream_urls() -> None:
    payload = official_track_payload()
    raw_url = "https://api.soundcloud.invalid/media/secret-stream-url"
    _first_official_transcoding(payload)["url"] = raw_url
    _first_official_transcoding(payload)["format"] = "not-a-mapping"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.invalid_fields == ("media.transcodings.0.format",)
    assert raw_url not in ",".join(resource.invalid_fields)
    assert "stream-url" not in ",".join(resource.invalid_fields)


def test_invalid_fields_do_not_contain_token_or_client_secret() -> None:
    payload = official_track_payload()
    payload["client_secret"] = "raw-client-secret"

    resource = SoundCloudResponseMapper().map_resolved_resource(payload, normalized())

    assert resource.status is SoundCloudResolveStatus.ERROR
    assert resource.invalid_fields == ("client_secret",)
    assert "raw-client-secret" not in ",".join(resource.invalid_fields)


def test_payload_shape_helper_returns_keys_counts_and_nulls_only() -> None:
    payload = official_track_payload()
    payload["artwork_url"] = None
    payload["publisher_metadata"] = None

    shape = summarize_soundcloud_payload_shape(payload)

    assert shape["kind_present"] is True
    assert shape["kind"] == "track"
    assert shape["kind_type"] == "str"
    assert shape["media_present"] is True
    assert shape["transcodings_field_present"] is True
    assert shape["transcodings_count"] == 1
    assert "media" in shape["top_level_keys"]
    assert "url" in shape["transcodings_field_keys"]
    assert "protocol" in shape["transcodings_format_field_keys"]
    assert "artwork_url" in shape["nullable_field_names"]
    assert "publisher_metadata" in shape["nullable_field_names"]


def test_payload_shape_helper_reports_transcoding_format_and_presence_only() -> None:
    payload = official_track_payload()

    shape = summarize_soundcloud_payload_shape(payload)
    transcodings = shape["transcodings"]

    assert isinstance(transcodings, tuple)
    assert transcodings[0]["protocol"] == "hls"
    assert transcodings[0]["mime_type"] == "audio/mpeg"
    assert transcodings[0]["url_present"] is True
    assert transcodings[0]["snipped"] is False


def test_payload_shape_helper_does_not_return_url_values() -> None:
    raw_url = "https://api.soundcloud.invalid/media/secret-stream-url"
    payload = official_track_payload()
    _first_official_transcoding(payload)["url"] = raw_url
    payload["stream_url"] = RAW_RESOLVER_STREAM_URL
    payload["permalink_url"] = "https://soundcloud.test/artist/track?si=SHOULD_NOT_LEAK"

    shape = summarize_soundcloud_payload_shape(payload)

    assert raw_url not in repr(shape)
    assert "secret-stream-url" not in repr(shape)
    assert RAW_RESOLVER_STREAM_URL not in repr(shape)
    assert "https://soundcloud.test/artist/track" not in repr(shape)
    assert "SHOULD_NOT_LEAK" not in repr(shape)


def test_payload_shape_helper_handles_missing_media() -> None:
    payload = official_track_payload()
    payload.pop("media")

    shape = summarize_soundcloud_payload_shape(payload)

    assert shape["media_present"] is False
    assert shape["transcodings_field_present"] is False
    assert shape["transcodings_count"] is None
    assert shape["transcodings"] == ()


def test_payload_shape_helper_handles_empty_transcodings() -> None:
    payload = official_track_payload()
    payload["media"] = {"transcodings": []}

    shape = summarize_soundcloud_payload_shape(payload)

    assert shape["media_present"] is True
    assert shape["transcodings_field_present"] is True
    assert shape["transcodings_count"] == 0
    assert shape["transcodings"] == ()


def test_payload_shape_helper_handles_malformed_transcodings_safely() -> None:
    payload = official_track_payload()
    payload["media"] = {"transcodings": ["not-a-mapping"]}

    shape = summarize_soundcloud_payload_shape(payload)
    transcodings = shape["transcodings"]

    assert shape["transcodings_count"] == 1
    assert isinstance(transcodings, tuple)
    assert transcodings[0]["protocol"] is None
    assert transcodings[0]["mime_type"] is None
    assert transcodings[0]["url_present"] is False


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
    dumped = repr(resource) + str(resource.model_dump(mode="json")) + " ".join(resource.warnings)

    assert resource.status is SoundCloudResolveStatus.RESOLVED
    assert raw_url not in dumped
    assert "raw-secret" not in dumped


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
