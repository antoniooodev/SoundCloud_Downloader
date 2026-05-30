import socket
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.application import (
    HLSSegmentPlanner,
    HLSSegmentPlanningError,
    HLSSegmentPlanningRequest,
)
from soundcloud_downloader.domain import SoundCloudResolvedStreamUrl

MANIFEST_URL = "https://media.soundcloud.test/path/playlist.m3u8?Policy=dummy"
SEGMENT_URL = "https://media.soundcloud.test/path/segment0.ts?Policy=dummy"
ABSOLUTE_SEGMENT_URL = "https://cdn.soundcloud.test/audio/segment1.ts?Policy=dummy"
INIT_MAP_URL = "https://media.soundcloud.test/path/init.mp4"
SIMPLE_MEDIA_PLAYLIST = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:7
#EXTINF:6.0,Intro
segment0.ts?Policy=dummy
#EXTINF:5.5,
https://cdn.soundcloud.test/audio/segment1.ts?Policy=dummy
#EXT-X-ENDLIST
"""


def test_planner_builds_plan_for_simple_media_playlist() -> None:
    plan = _plan(SIMPLE_MEDIA_PLAYLIST)

    assert plan.segment_count == 2


def test_plan_resolves_relative_segment_uris_against_manifest_url() -> None:
    plan = _plan(SIMPLE_MEDIA_PLAYLIST)

    assert plan.segments[0].url.get_secret_value() == SEGMENT_URL


def test_plan_preserves_absolute_segment_urls() -> None:
    plan = _plan(SIMPLE_MEDIA_PLAYLIST)

    assert plan.segments[1].url.get_secret_value() == ABSOLUTE_SEGMENT_URL


def test_plan_parses_target_duration() -> None:
    plan = _plan(SIMPLE_MEDIA_PLAYLIST)

    assert plan.target_duration_seconds == 6.0


def test_plan_parses_media_sequence() -> None:
    plan = _plan(SIMPLE_MEDIA_PLAYLIST)

    assert plan.media_sequence == 7


def test_plan_parses_endlist() -> None:
    plan = _plan(SIMPLE_MEDIA_PLAYLIST)

    assert plan.end_list is True


def test_plan_parses_extinf_duration() -> None:
    plan = _plan(SIMPLE_MEDIA_PLAYLIST)

    assert plan.segments[0].duration_seconds == 6.0
    assert plan.segments[1].duration_seconds == 5.5


def test_plan_parses_extinf_title_when_present() -> None:
    plan = _plan(SIMPLE_MEDIA_PLAYLIST)

    assert plan.segments[0].title == "Intro"
    assert plan.segments[1].title == ""


def test_plan_preserves_segment_order() -> None:
    plan = _plan(SIMPLE_MEDIA_PLAYLIST)

    assert [segment.index for segment in plan.segments] == [0, 1]
    assert plan.segments[0].url.get_secret_value().endswith("segment0.ts?Policy=dummy")
    assert plan.segments[1].url.get_secret_value() == ABSOLUTE_SEGMENT_URL


def test_plan_parses_byterange_for_segment() -> None:
    plan = _plan(
        """#EXTM3U
#EXTINF:6.0,
#EXT-X-BYTERANGE:1200@24
segment0.ts
"""
    )

    assert plan.segments[0].byte_range is not None
    assert plan.segments[0].byte_range.length == 1200
    assert plan.segments[0].byte_range.offset == 24


def test_plan_parses_ext_x_map_uri() -> None:
    plan = _plan(
        """#EXTM3U
#EXT-X-MAP:URI="init.mp4"
#EXTINF:6.0,
segment0.m4s
"""
    )

    assert plan.initialization_map is not None
    assert plan.initialization_map.url.get_secret_value() == INIT_MAP_URL


def test_plan_parses_ext_x_map_byterange() -> None:
    plan = _plan(
        """#EXTM3U
#EXT-X-MAP:URI="init.mp4",BYTERANGE="720@0"
#EXTINF:6.0,
segment0.m4s
"""
    )

    assert plan.initialization_map is not None
    assert plan.initialization_map.byte_range is not None
    assert plan.initialization_map.byte_range.length == 720
    assert plan.initialization_map.byte_range.offset == 0


def test_planner_rejects_manifest_without_extm3u() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan("#EXTINF:6.0,\nsegment0.ts\n")


def test_planner_rejects_master_playlist_with_ext_x_stream_inf() -> None:
    with pytest.raises(HLSSegmentPlanningError) as exc_info:
        _plan("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=128000\nlow.m3u8\n")

    assert "Unsupported HLS playlist type." in str(exc_info.value)


def test_planner_rejects_ext_x_key() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:6.0,\nsegment0.ts\n')


def test_planner_rejects_ext_x_session_key() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan(
            '#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=AES-128,URI="key.bin"\n'
            "#EXTINF:6.0,\nsegment0.ts\n"
        )


def test_planner_rejects_sample_aes_manifest() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan("#EXTM3U\n#EXT-X-VERSION:5\n#EXT-X-KEY:METHOD=SAMPLE-AES\n")


def test_planner_rejects_keyformat_manifest() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan(
            '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,KEYFORMAT="com.apple.streamingkeydelivery"\n'
        )


def test_planner_rejects_playlist_with_no_segments() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan("#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-ENDLIST\n")


def test_planner_rejects_malformed_extinf() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan("#EXTM3U\n#EXTINF:not-a-number,\nsegment0.ts\n")


def test_planner_rejects_extinf_without_following_uri() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan("#EXTM3U\n#EXTINF:6.0,\n#EXT-X-ENDLIST\n")


def test_planner_rejects_malformed_byterange() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan("#EXTM3U\n#EXT-X-BYTERANGE:bad\n#EXTINF:6.0,\nsegment0.ts\n")


def test_planner_rejects_malformed_ext_x_map() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan("#EXTM3U\n#EXT-X-MAP:URI\n#EXTINF:6.0,\nsegment0.ts\n")


def test_planner_rejects_unsafe_segment_url() -> None:
    with pytest.raises(HLSSegmentPlanningError) as exc_info:
        _plan("#EXTM3U\n#EXTINF:6.0,\nsegment0.ts?access_token=secret\n")

    assert "Unsafe HLS segment URL." in str(exc_info.value)


def test_planner_rejects_unsafe_init_map_url() -> None:
    with pytest.raises(HLSSegmentPlanningError):
        _plan('#EXTM3U\n#EXT-X-MAP:URI="init.mp4?access_token=secret"\n#EXTINF:6.0,\nsegment0.ts\n')


def test_planner_exception_messages_do_not_contain_manifest_url() -> None:
    with pytest.raises(HLSSegmentPlanningError) as exc_info:
        _plan("#EXTM3U\n#EXTINF:bad,\nsegment0.ts\n")

    assert MANIFEST_URL not in str(exc_info.value)


def test_planner_exception_messages_do_not_contain_segment_url() -> None:
    with pytest.raises(HLSSegmentPlanningError) as exc_info:
        _plan(f"#EXTM3U\n#EXTINF:6.0,\n{SEGMENT_URL}&access_token=secret\n")

    assert SEGMENT_URL not in str(exc_info.value)


def test_planner_exception_messages_do_not_contain_manifest_body() -> None:
    manifest = f"#EXTM3U\n#EXTINF:6.0,\n{SEGMENT_URL}&access_token=secret\n"

    with pytest.raises(HLSSegmentPlanningError) as exc_info:
        _plan(manifest)

    assert manifest not in str(exc_info.value)


def test_planning_request_repr_and_model_dump_do_not_expose_segment_urls() -> None:
    request = _request(SIMPLE_MEDIA_PLAYLIST)
    output = f"{request!r} {request.model_dump(mode='json')}"

    assert "segment0.ts" not in output
    assert ABSOLUTE_SEGMENT_URL not in output


def test_planning_request_is_immutable() -> None:
    request = _request(SIMPLE_MEDIA_PLAYLIST)

    with pytest.raises(ValidationError):
        request.manifest_text = SecretStr("#EXTM3U\n")


def test_planner_performs_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert _plan(SIMPLE_MEDIA_PLAYLIST).segment_count == 2


def test_planner_writes_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert _plan(SIMPLE_MEDIA_PLAYLIST).segment_count == 2


def _plan(manifest_text: str):
    return HLSSegmentPlanner().build_plan(_request(manifest_text))


def _request(manifest_text: str) -> HLSSegmentPlanningRequest:
    return HLSSegmentPlanningRequest(
        manifest_url=SoundCloudResolvedStreamUrl(value=SecretStr(MANIFEST_URL)),
        manifest_text=SecretStr(manifest_text),
    )
