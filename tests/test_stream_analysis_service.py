import pytest
from pydantic import ValidationError

from soundcloud_downloader.application import (
    StreamAnalysisRequest,
    StreamAnalysisResult,
    StreamAnalysisService,
)
from soundcloud_downloader.domain import (
    DRMStatus,
    HLSManifestAnalysis,
    HLSManifestKind,
    MediaCodec,
    MediaContainer,
    MediaSource,
    SourceProtocol,
)


PLAIN_HLS = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
segment0.ts
#EXT-X-ENDLIST
"""

AES_HLS = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="https://example.test/key.bin?token=secret"
#EXTINF:10.0,
segment0.ts
"""


def hls_request(manifest_text: str | None) -> StreamAnalysisRequest:
    return StreamAnalysisRequest(
        source_id="source-1",
        protocol=SourceProtocol.HLS,
        mime_type="application/vnd.apple.mpegurl",
        codec=MediaCodec.AAC,
        container=MediaContainer.M4A,
        bitrate_kbps=128,
        requires_auth=True,
        is_downloadable=False,
        declared_drm_status=DRMStatus.UNKNOWN,
        manifest_text=manifest_text,
    )


def test_hls_plain_manifest_produces_source_with_no_drm() -> None:
    result = StreamAnalysisService().analyze(hls_request(PLAIN_HLS))

    assert result.effective_drm_status is DRMStatus.NONE
    assert result.source.drm_status is DRMStatus.NONE
    assert result.source.protocol is SourceProtocol.HLS
    assert result.source.codec is MediaCodec.AAC
    assert result.hls_analysis is not None
    assert result.hls_analysis.drm_status is DRMStatus.NONE


def test_hls_aes_128_manifest_produces_encrypted_hls_source() -> None:
    result = StreamAnalysisService().analyze(hls_request(AES_HLS))

    assert result.effective_drm_status is DRMStatus.ENCRYPTED_HLS
    assert result.source.drm_status is DRMStatus.ENCRYPTED_HLS
    assert result.hls_analysis is not None
    assert result.hls_analysis.has_ext_x_key is True


@pytest.mark.parametrize(
    "key_format",
    [
        "com.apple.streamingkeydelivery",
        "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed:widevine",
        "com.microsoft.playready",
    ],
)
def test_hls_drm_manifest_produces_eme_drm_source(key_format: str) -> None:
    manifest = f"""#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://asset",KEYFORMAT="{key_format}"
segment0.ts
"""

    result = StreamAnalysisService().analyze(hls_request(manifest))

    assert result.effective_drm_status is DRMStatus.EME_DRM
    assert result.source.drm_status is DRMStatus.EME_DRM
    assert result.hls_analysis is not None
    assert result.hls_analysis.drm_status is DRMStatus.EME_DRM


def test_hls_source_without_manifest_fails_closed_with_unknown_drm_and_warning() -> None:
    result = StreamAnalysisService().analyze(hls_request(None))

    assert result.effective_drm_status is DRMStatus.UNKNOWN
    assert result.source.drm_status is DRMStatus.UNKNOWN
    assert result.hls_analysis is None
    assert result.warnings
    assert "Missing HLS manifest text" in result.warnings[0]


def test_non_hls_downloadable_source_preserves_declared_no_drm() -> None:
    request = StreamAnalysisRequest(
        source_id="download-1",
        protocol=SourceProtocol.DOWNLOAD,
        codec=MediaCodec.MP3,
        container=MediaContainer.ORIGINAL,
        is_downloadable=True,
        declared_drm_status=DRMStatus.NONE,
    )

    result = StreamAnalysisService().analyze(request)

    assert result.source.is_downloadable is True
    assert result.effective_drm_status is DRMStatus.NONE
    assert result.source.drm_status is DRMStatus.NONE
    assert result.hls_analysis is None


def test_non_hls_source_with_unknown_declared_drm_remains_unknown() -> None:
    request = StreamAnalysisRequest(
        protocol=SourceProtocol.PROGRESSIVE,
        codec=MediaCodec.MP3,
        declared_drm_status=DRMStatus.UNKNOWN,
    )

    result = StreamAnalysisService().analyze(request)

    assert result.effective_drm_status is DRMStatus.UNKNOWN
    assert result.source.drm_status is DRMStatus.UNKNOWN
    assert result.hls_analysis is None


def test_non_hls_source_with_manifest_text_fails_validation() -> None:
    with pytest.raises(ValidationError):
        StreamAnalysisRequest(
            protocol=SourceProtocol.DOWNLOAD,
            declared_drm_status=DRMStatus.NONE,
            manifest_text=PLAIN_HLS,
        )


def test_negative_bitrate_fails_validation() -> None:
    with pytest.raises(ValidationError):
        StreamAnalysisRequest(protocol=SourceProtocol.HLS, bitrate_kbps=-1)


def test_stream_analysis_result_rejects_mismatched_source_and_effective_drm_status() -> None:
    source = MediaSource(protocol=SourceProtocol.DOWNLOAD, drm_status=DRMStatus.NONE)

    with pytest.raises(ValidationError):
        StreamAnalysisResult(
            source=source,
            effective_drm_status=DRMStatus.UNKNOWN,
        )


def test_stream_analysis_result_rejects_mismatched_hls_analysis_and_effective_drm_status() -> None:
    source = MediaSource(protocol=SourceProtocol.HLS, drm_status=DRMStatus.UNKNOWN)
    hls_analysis = HLSManifestAnalysis(
        kind=HLSManifestKind.MEDIA,
        is_hls=True,
        is_encrypted=False,
        drm_status=DRMStatus.NONE,
        has_ext_x_key=False,
        has_ext_x_session_key=False,
        has_stream_inf=False,
        has_media_sequence=True,
        has_endlist=True,
        segment_count=1,
    )

    with pytest.raises(ValidationError):
        StreamAnalysisResult(
            source=source,
            hls_analysis=hls_analysis,
            effective_drm_status=DRMStatus.UNKNOWN,
        )


def test_service_does_not_call_hls_analyzer_for_non_hls_sources() -> None:
    class RaisingAnalyzer:
        def analyze(self, manifest_text: str) -> HLSManifestAnalysis:
            raise AssertionError("HLS analyzer should not be called for non-HLS sources.")

    request = StreamAnalysisRequest(
        protocol=SourceProtocol.DOWNLOAD,
        declared_drm_status=DRMStatus.NONE,
    )

    result = StreamAnalysisService(RaisingAnalyzer()).analyze(request)

    assert result.effective_drm_status is DRMStatus.NONE
    assert result.hls_analysis is None


def test_stream_analysis_request_is_immutable() -> None:
    request = StreamAnalysisRequest(protocol=SourceProtocol.HLS)

    with pytest.raises(ValidationError):
        request.manifest_text = PLAIN_HLS


def test_stream_analysis_result_is_immutable() -> None:
    result = StreamAnalysisService().analyze(hls_request(PLAIN_HLS))

    with pytest.raises(ValidationError):
        result.effective_drm_status = DRMStatus.UNKNOWN
