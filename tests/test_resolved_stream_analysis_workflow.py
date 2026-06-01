import asyncio
import socket
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.application import (
    HLSAnalysisError,
    HLSAnalysisFailureReason,
    HLSManifestFetchFailureKind,
    HLSManifestFetcherPort,
    ResolvedStreamAnalysisRequest,
    ResolvedStreamAnalysisResult,
    ResolvedStreamAnalysisWorkflow,
)
from soundcloud_downloader.domain import (
    AccessMode,
    DRMStatus,
    ErrorCode,
    OfflineDecision,
    OutputProfile,
    SoundCloudResolvedStream,
    SoundCloudResolvedStreamKind,
    SoundCloudResolvedStreamUrl,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
)
from soundcloud_downloader.infrastructure.soundcloud import SoundCloudHLSManifestRetrievalError
from soundcloud_downloader.infrastructure.soundcloud import (
    HLSManifestFetchFailureKind as InfrastructureHLSManifestFetchFailureKind,
)

T = TypeVar("T")

RAW_HLS_URL = "https://media.soundcloud.test/playlist.m3u8?Policy=dummy-policy"
RAW_PROGRESSIVE_URL = "https://media.soundcloud.test/audio.mp3?Policy=dummy-policy"
SEGMENT_URL = "https://media.soundcloud.test/segment0.ts?Policy=dummy-segment-policy"
PLAIN_HLS = f"""#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
{SEGMENT_URL}
#EXT-X-ENDLIST
"""
AES_HLS = f"""#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="https://keys.soundcloud.test/key.bin?Policy=dummy-key-policy"
#EXTINF:10.0,
{SEGMENT_URL}
"""
FAIRPLAY_HLS = f"""#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://asset",KEYFORMAT="com.apple.streamingkeydelivery"
#EXTINF:10.0,
{SEGMENT_URL}
"""


def run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)


def test_hls_stream_triggers_manifest_fetch() -> None:
    fetcher = FakeManifestFetcher(PLAIN_HLS)

    run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST), fetcher))

    assert fetcher.calls == [_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)]


def test_hls_manifest_is_analyzed_by_existing_hls_analyzer() -> None:
    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)))

    assert result.manifest_analysis is not None
    assert result.manifest_analysis.is_hls is True
    assert result.manifest_analysis.segment_count == 1


def test_non_encrypted_hls_manifest_produces_none_drm_status() -> None:
    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)))

    assert result.plan.effective_drm_status is DRMStatus.NONE


def test_encrypted_hls_manifest_produces_encrypted_drm_status() -> None:
    with pytest.raises(HLSAnalysisError) as exc_info:
        run(
            _analyze(
                _stream(SoundCloudResolvedStreamKind.HLS_MANIFEST),
                FakeManifestFetcher(AES_HLS),
            )
        )

    assert exc_info.value.reason is HLSAnalysisFailureReason.HLS_ENCRYPTED_STREAM_UNSUPPORTED


def test_sample_aes_fairplay_like_manifest_is_denied_by_policy() -> None:
    with pytest.raises(HLSAnalysisError) as exc_info:
        run(
            _analyze(
                _stream(SoundCloudResolvedStreamKind.HLS_MANIFEST),
                FakeManifestFetcher(FAIRPLAY_HLS),
            )
        )

    assert exc_info.value.reason is HLSAnalysisFailureReason.HLS_ENCRYPTED_STREAM_UNSUPPORTED
    assert exc_info.value.code is ErrorCode.ENCRYPTED_STREAM_UNSUPPORTED


def test_manifest_containing_ext_x_key_is_denied_or_marked_encrypted() -> None:
    with pytest.raises(HLSAnalysisError) as exc_info:
        run(
            _analyze(
                _stream(SoundCloudResolvedStreamKind.HLS_MANIFEST),
                FakeManifestFetcher(AES_HLS),
            )
        )

    assert exc_info.value.reason is HLSAnalysisFailureReason.HLS_ENCRYPTED_STREAM_UNSUPPORTED


def test_hls_result_contains_manifest_analysis() -> None:
    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)))

    assert result.manifest_analysis is result.plan.hls_analysis
    assert result.manifest_analysis is not None


def test_progressive_stream_does_not_trigger_manifest_fetch() -> None:
    fetcher = FakeManifestFetcher(PLAIN_HLS)

    run(_analyze(_stream(SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA), fetcher))

    assert fetcher.calls == []


def test_progressive_result_has_manifest_analysis_none() -> None:
    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA)))

    assert result.manifest_analysis is None
    assert result.plan.hls_analysis is None


def test_unknown_stream_fails_closed_or_returns_denied_plan() -> None:
    fetcher = FakeManifestFetcher(PLAIN_HLS)

    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.UNKNOWN), fetcher))

    assert fetcher.calls == []
    assert result.plan.effective_drm_status is DRMStatus.UNKNOWN
    assert result.plan.policy.allowed is False
    assert result.plan.policy.decision is OfflineDecision.DENY_UNKNOWN_UNSAFE


def test_workflow_result_does_not_expose_raw_stream_url_in_repr() -> None:
    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)))

    assert RAW_HLS_URL not in repr(result)


def test_workflow_result_does_not_expose_raw_stream_url_in_model_dump() -> None:
    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)))

    assert RAW_HLS_URL not in str(result.model_dump(mode="json"))


def test_workflow_result_does_not_expose_raw_manifest_body() -> None:
    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)))

    assert PLAIN_HLS not in repr(result)
    assert PLAIN_HLS not in str(result.model_dump(mode="json"))


def test_workflow_result_does_not_expose_segment_urls() -> None:
    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)))

    assert SEGMENT_URL not in repr(result)
    assert SEGMENT_URL not in str(result.model_dump(mode="json"))


def test_manifest_fetch_failure_propagates_safely() -> None:
    fetcher = FakeManifestFetcher(
        PLAIN_HLS,
        error=SoundCloudHLSManifestRetrievalError(
            ErrorCode.SOURCE_NOT_DOWNLOADABLE,
            "Manifest fetch failed safely.",
        ),
    )

    with pytest.raises(HLSAnalysisError) as exc_info:
        run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST), fetcher))

    assert RAW_HLS_URL not in str(exc_info.value)
    assert SEGMENT_URL not in str(exc_info.value)
    assert exc_info.value.reason is HLSAnalysisFailureReason.HLS_MANIFEST_FETCH_FAILED


def test_manifest_fetch_failure_kind_propagates_safely() -> None:
    fetcher = FakeManifestFetcher(
        PLAIN_HLS,
        error=SoundCloudHLSManifestRetrievalError(
            ErrorCode.AUTH_REQUIRED,
            "Manifest fetch failed safely.",
            manifest_request_status=403,
            failure_kind=InfrastructureHLSManifestFetchFailureKind.HTTP_STATUS,
        ),
    )

    with pytest.raises(HLSAnalysisError) as exc_info:
        run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST), fetcher))

    assert exc_info.value.reason is HLSAnalysisFailureReason.HLS_MANIFEST_FETCH_FAILED
    assert exc_info.value.manifest_fetch_failure_kind is HLSManifestFetchFailureKind.HTTP_STATUS
    assert exc_info.value.manifest_request_status == 403


def test_fake_manifest_fetcher_satisfies_port() -> None:
    assert isinstance(FakeManifestFetcher(PLAIN_HLS), HLSManifestFetcherPort)


def test_request_model_is_immutable() -> None:
    request = ResolvedStreamAnalysisRequest(
        stream=_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST),
        access_mode=AccessMode.GO_PLUS,
        output_profile=OutputProfile.AAC_M4A,
    )

    with pytest.raises(ValidationError):
        request.output_profile = OutputProfile.MP3_128


def test_result_model_is_immutable() -> None:
    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)))

    with pytest.raises(ValidationError):
        result.manifest_analysis = None


def test_tests_perform_no_real_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)))

    assert result.manifest_analysis is not None


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    result = run(_analyze(_stream(SoundCloudResolvedStreamKind.HLS_MANIFEST)))

    assert result.plan.policy is not None


async def _analyze(
    stream: SoundCloudResolvedStream,
    fetcher: "FakeManifestFetcher | None" = None,
) -> ResolvedStreamAnalysisResult:
    workflow = ResolvedStreamAnalysisWorkflow(
        manifest_fetcher=fetcher or FakeManifestFetcher(PLAIN_HLS),
    )
    return await workflow.analyze(
        ResolvedStreamAnalysisRequest(
            stream=stream,
            access_mode=AccessMode.GO_PLUS,
            output_profile=OutputProfile.AAC_M4A,
        )
    )


class FakeManifestFetcher:
    def __init__(
        self,
        manifest_text: str,
        *,
        error: Exception | None = None,
    ) -> None:
        self._manifest_text = manifest_text
        self._error = error
        self.calls: list[SoundCloudResolvedStream] = []

    async def fetch_manifest(
        self,
        *,
        stream: SoundCloudResolvedStream,
    ) -> str:
        self.calls.append(stream)
        if self._error is not None:
            raise self._error
        return self._manifest_text


def _stream(kind: SoundCloudResolvedStreamKind) -> SoundCloudResolvedStream:
    raw_url = (
        RAW_PROGRESSIVE_URL
        if kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA
        else RAW_HLS_URL
    )
    protocol = (
        SoundCloudTranscodingProtocol.PROGRESSIVE
        if kind is SoundCloudResolvedStreamKind.PROGRESSIVE_MEDIA
        else SoundCloudTranscodingProtocol.HLS
    )
    return SoundCloudResolvedStream(
        kind=kind,
        url=SoundCloudResolvedStreamUrl(value=SecretStr(raw_url)),
        protocol=protocol,
        mime_type=SoundCloudTranscodingMimeType.AUDIO_MP4,
        preset="aac_0_1",
        quality="sq",
        snipped=False,
    )
