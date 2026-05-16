import pytest
from pydantic import ValidationError

from soundcloud_downloader.application import HLSManifestAnalyzer
from soundcloud_downloader.domain import (
    DRMStatus,
    HLSDrmIndicator,
    HLSManifestAnalysis,
    HLSManifestKind,
)


def test_plain_media_playlist() -> None:
    analysis = HLSManifestAnalyzer().analyze(
        """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
segment0.ts
#EXTINF:10.0,
segment1.ts
#EXT-X-ENDLIST
"""
    )

    assert analysis.is_hls is True
    assert analysis.kind is HLSManifestKind.MEDIA
    assert analysis.drm_status is DRMStatus.NONE
    assert analysis.is_encrypted is False
    assert analysis.segment_count == 2


def test_plain_master_playlist() -> None:
    analysis = HLSManifestAnalyzer().analyze(
        """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1280000,RESOLUTION=640x360
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2560000,RESOLUTION=1280x720
high.m3u8
"""
    )

    assert analysis.is_hls is True
    assert analysis.kind is HLSManifestKind.MASTER
    assert analysis.has_stream_inf is True
    assert analysis.segment_count == 0
    assert analysis.drm_status is DRMStatus.NONE


def test_non_hls_text() -> None:
    analysis = HLSManifestAnalyzer().analyze("not a manifest")

    assert analysis.is_hls is False
    assert analysis.kind is HLSManifestKind.UNKNOWN
    assert analysis.drm_status is DRMStatus.UNKNOWN
    assert analysis.warnings


def test_aes_128_ext_x_key_is_encrypted_and_redacts_uri_query() -> None:
    analysis = HLSManifestAnalyzer().analyze(
        """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="https://example.test/key.bin?token=secret&sig=abc"
#EXTINF:10.0,
segment0.ts
"""
    )

    assert analysis.is_encrypted is True
    assert analysis.has_ext_x_key is True
    assert analysis.drm_status is DRMStatus.ENCRYPTED_HLS
    assert analysis.drm_indicators
    redacted_line = analysis.drm_indicators[0].raw_line_redacted
    assert "token=secret" not in redacted_line
    assert "sig=abc" not in redacted_line
    assert "[redacted]" in redacted_line


def test_sample_aes_ext_x_key_is_encrypted_hls() -> None:
    analysis = HLSManifestAnalyzer().analyze(
        """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://asset"
segment0.ts
"""
    )

    assert analysis.is_encrypted is True
    assert analysis.drm_status is DRMStatus.ENCRYPTED_HLS


def test_ext_x_session_key_is_encrypted() -> None:
    analysis = HLSManifestAnalyzer().analyze(
        """#EXTM3U
#EXT-X-SESSION-KEY:METHOD=AES-128,URI="https://example.test/key.bin"
#EXT-X-STREAM-INF:BANDWIDTH=1280000
low.m3u8
"""
    )

    assert analysis.has_ext_x_session_key is True
    assert analysis.is_encrypted is True
    assert analysis.drm_status is DRMStatus.ENCRYPTED_HLS


@pytest.mark.parametrize(
    "key_format",
    [
        "com.apple.streamingkeydelivery",
        "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed:widevine",
        "com.microsoft.playready",
    ],
)
def test_eme_key_formats_are_classified_as_drm(key_format: str) -> None:
    analysis = HLSManifestAnalyzer().analyze(
        f"""#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://asset",KEYFORMAT="{key_format}"
segment0.ts
"""
    )

    assert analysis.is_encrypted is True
    assert analysis.drm_status is DRMStatus.EME_DRM


def test_method_none_only_is_not_encrypted() -> None:
    analysis = HLSManifestAnalyzer().analyze(
        """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=NONE
segment0.ts
"""
    )

    assert analysis.is_encrypted is False
    assert analysis.drm_status is DRMStatus.NONE


def test_mixed_method_none_and_aes_128_warns_and_is_encrypted() -> None:
    analysis = HLSManifestAnalyzer().analyze(
        """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=NONE
#EXT-X-KEY:METHOD=AES-128,URI="https://example.test/key.bin"
segment0.ts
"""
    )

    assert analysis.is_encrypted is True
    assert analysis.drm_status is DRMStatus.ENCRYPTED_HLS
    assert any("mixed METHOD=NONE" in warning for warning in analysis.warnings)


def test_key_tag_with_missing_method_is_unknown_and_warns() -> None:
    analysis = HLSManifestAnalyzer().analyze(
        """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:URI="https://example.test/key.bin"
segment0.ts
"""
    )

    assert analysis.drm_status is DRMStatus.UNKNOWN
    assert analysis.is_encrypted is False
    assert any("missing METHOD" in warning for warning in analysis.warnings)


def test_unknown_non_none_method_fails_closed_with_warning() -> None:
    analysis = HLSManifestAnalyzer().analyze(
        """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=ROT13,URI="https://example.test/key.bin"
segment0.ts
"""
    )

    assert analysis.drm_status is DRMStatus.UNKNOWN
    assert analysis.is_encrypted is False
    assert any("Unknown HLS encryption method" in warning for warning in analysis.warnings)


def test_hls_manifest_with_unknown_kind_warns() -> None:
    analysis = HLSManifestAnalyzer().analyze("#EXTM3U\n#EXT-X-VERSION:3\n")

    assert analysis.is_hls is True
    assert analysis.kind is HLSManifestKind.UNKNOWN
    assert analysis.drm_status is DRMStatus.NONE
    assert analysis.warnings


def test_hls_manifest_analysis_rejects_negative_segment_count() -> None:
    with pytest.raises(ValidationError):
        HLSManifestAnalysis(
            kind=HLSManifestKind.MEDIA,
            is_hls=True,
            is_encrypted=False,
            drm_status=DRMStatus.NONE,
            has_ext_x_key=False,
            has_ext_x_session_key=False,
            has_stream_inf=False,
            has_media_sequence=True,
            has_endlist=True,
            segment_count=-1,
        )


def test_hls_drm_indicator_rejects_unredacted_uri_query_string() -> None:
    with pytest.raises(ValidationError):
        HLSDrmIndicator(
            tag="#EXT-X-KEY",
            method="AES-128",
            uri_present=True,
            raw_line_redacted='#EXT-X-KEY:METHOD=AES-128,URI="https://example.test/key?token=secret"',
        )


@pytest.mark.parametrize(
    ("is_encrypted", "drm_status"),
    [
        (True, DRMStatus.NONE),
        (False, DRMStatus.ENCRYPTED_HLS),
        (False, DRMStatus.EME_DRM),
    ],
)
def test_hls_manifest_analysis_rejects_inconsistent_encrypted_drm_states(
    is_encrypted: bool,
    drm_status: DRMStatus,
) -> None:
    with pytest.raises(ValidationError):
        HLSManifestAnalysis(
            kind=HLSManifestKind.MEDIA,
            is_hls=True,
            is_encrypted=is_encrypted,
            drm_status=drm_status,
            has_ext_x_key=False,
            has_ext_x_session_key=False,
            has_stream_inf=False,
            has_media_sequence=True,
            has_endlist=True,
            segment_count=0,
        )


def test_analyzer_uses_manifest_strings_only() -> None:
    analysis = HLSManifestAnalyzer().analyze("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\nsegment.ts\n")

    assert analysis.is_hls is True
    assert analysis.segment_count == 1
