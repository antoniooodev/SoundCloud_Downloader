import socket
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.domain import (
    HLSByteRange,
    HLSInitializationMapReference,
    HLSInitializationMapUrl,
    HLSSegmentPlan,
    HLSSegmentReference,
    HLSSegmentUrl,
    SoundCloudResolvedStreamUrl,
    redact_hls_segment_plan,
)

MANIFEST_URL = "https://media.soundcloud.test/playlists/track.m3u8?Policy=dummy"
SEGMENT_URL = "https://media.soundcloud.test/playlists/segment0.ts?Policy=dummy"


def test_hls_segment_url_accepts_absolute_https_url() -> None:
    url = HLSSegmentUrl(value=SecretStr(SEGMENT_URL))

    assert url.get_secret_value() == SEGMENT_URL


def test_hls_segment_url_accepts_absolute_http_url() -> None:
    url = HLSSegmentUrl(value=SecretStr("http://media.soundcloud.test/segment.ts"))

    assert url.get_secret_value() == "http://media.soundcloud.test/segment.ts"


def test_hls_segment_url_rejects_empty_url() -> None:
    with pytest.raises(ValidationError):
        HLSSegmentUrl(value=SecretStr(""))


def test_hls_segment_url_rejects_relative_url() -> None:
    with pytest.raises(ValidationError):
        HLSSegmentUrl(value=SecretStr("segment.ts"))


def test_hls_segment_url_rejects_userinfo_credentials() -> None:
    with pytest.raises(ValidationError):
        HLSSegmentUrl(value=SecretStr("https://user:pass@example.test/segment.ts"))


@pytest.mark.parametrize("query_key", ["access_token", "refresh_token", "client_secret"])
def test_hls_segment_url_rejects_sensitive_query_keys(query_key: str) -> None:
    with pytest.raises(ValidationError):
        HLSSegmentUrl(value=SecretStr(f"https://example.test/segment.ts?{query_key}=secret"))


def test_hls_segment_url_repr_does_not_expose_raw_url() -> None:
    url = HLSSegmentUrl(value=SecretStr(SEGMENT_URL))

    assert SEGMENT_URL not in repr(url)


def test_hls_segment_url_model_dump_does_not_expose_raw_url() -> None:
    url = HLSSegmentUrl(value=SecretStr(SEGMENT_URL))

    assert SEGMENT_URL not in str(url.model_dump(mode="json"))


def test_hls_initialization_map_url_follows_same_redaction_behavior() -> None:
    raw_url = "https://media.soundcloud.test/init.mp4?Policy=dummy"
    url = HLSInitializationMapUrl(value=SecretStr(raw_url))

    assert raw_url not in repr(url)
    assert raw_url not in str(url.model_dump(mode="json"))


def test_hls_byte_range_rejects_non_positive_length() -> None:
    with pytest.raises(ValidationError):
        HLSByteRange(length=0)


def test_hls_byte_range_rejects_negative_offset() -> None:
    with pytest.raises(ValidationError):
        HLSByteRange(length=10, offset=-1)


def test_hls_segment_reference_rejects_negative_index() -> None:
    with pytest.raises(ValidationError):
        HLSSegmentReference(index=-1, url=_segment_url(), duration_seconds=6.0)


def test_hls_segment_reference_rejects_non_positive_duration() -> None:
    with pytest.raises(ValidationError):
        HLSSegmentReference(index=0, url=_segment_url(), duration_seconds=0)


def test_hls_segment_plan_rejects_empty_segment_list() -> None:
    with pytest.raises(ValidationError):
        HLSSegmentPlan(manifest_url=_manifest_url(), segments=())


def test_hls_segment_plan_exposes_segment_count() -> None:
    plan = _plan()

    assert plan.segment_count == 2


def test_hls_segment_plan_exposes_total_duration_seconds() -> None:
    plan = _plan()

    assert plan.total_duration_seconds == 12.5


def test_hls_segment_plan_repr_and_model_dump_do_not_expose_manifest_url() -> None:
    plan = _plan()
    output = f"{plan!r} {plan.model_dump(mode='json')}"

    assert MANIFEST_URL not in output


def test_hls_segment_plan_repr_and_model_dump_do_not_expose_segment_urls() -> None:
    plan = _plan()
    output = f"{plan!r} {plan.model_dump(mode='json')}"

    assert SEGMENT_URL not in output
    assert "segment1.ts" not in output


def test_redact_hls_segment_plan_redacts_manifest_url() -> None:
    redacted = redact_hls_segment_plan(_plan())

    assert redacted["manifest_url"] == "[REDACTED]"
    assert MANIFEST_URL not in str(redacted)


def test_redact_hls_segment_plan_redacts_segment_urls() -> None:
    redacted = redact_hls_segment_plan(_plan())

    assert redacted["segments"][0]["url"] == "[REDACTED]"
    assert SEGMENT_URL not in str(redacted)


def test_domain_models_are_immutable() -> None:
    segment = HLSSegmentReference(index=0, url=_segment_url(), duration_seconds=6.0)

    with pytest.raises(ValidationError):
        segment.duration_seconds = 7.0


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert _plan().segment_count == 2


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert _plan().total_duration_seconds == 12.5


def _manifest_url() -> SoundCloudResolvedStreamUrl:
    return SoundCloudResolvedStreamUrl(value=SecretStr(MANIFEST_URL))


def _segment_url(raw_url: str = SEGMENT_URL) -> HLSSegmentUrl:
    return HLSSegmentUrl(value=SecretStr(raw_url))


def _plan() -> HLSSegmentPlan:
    return HLSSegmentPlan(
        manifest_url=_manifest_url(),
        segments=(
            HLSSegmentReference(
                index=0,
                url=_segment_url(),
                duration_seconds=6.0,
            ),
            HLSSegmentReference(
                index=1,
                url=_segment_url("https://media.soundcloud.test/playlists/segment1.ts?Policy=dummy"),
                duration_seconds=6.5,
                byte_range=HLSByteRange(length=100, offset=0),
            ),
        ),
        initialization_map=HLSInitializationMapReference(
            url=HLSInitializationMapUrl(
                value=SecretStr("https://media.soundcloud.test/init.mp4?Policy=dummy")
            )
        ),
        target_duration_seconds=7.0,
        media_sequence=12,
        end_list=True,
    )
