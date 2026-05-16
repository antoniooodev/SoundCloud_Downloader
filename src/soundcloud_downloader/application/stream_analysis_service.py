from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soundcloud_downloader.application.hls_manifest_analyzer import HLSManifestAnalyzer
from soundcloud_downloader.domain import (
    DRMStatus,
    HLSManifestAnalysis,
    MediaCodec,
    MediaContainer,
    MediaSource,
    SourceProtocol,
)


class _HLSAnalyzer(Protocol):
    def analyze(self, manifest_text: str) -> HLSManifestAnalysis:
        ...


class StreamAnalysisRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str | None = None
    protocol: SourceProtocol
    mime_type: str | None = None
    codec: MediaCodec = MediaCodec.UNKNOWN
    container: MediaContainer = MediaContainer.UNKNOWN
    bitrate_kbps: int | None = Field(default=None, gt=0)
    requires_auth: bool = False
    is_downloadable: bool = False
    declared_drm_status: DRMStatus = DRMStatus.UNKNOWN
    manifest_text: str | None = None

    @model_validator(mode="after")
    def validate_manifest_protocol(self) -> Self:
        if self.manifest_text is not None and self.protocol is not SourceProtocol.HLS:
            raise ValueError("HLS manifest text may only be provided for HLS sources.")
        return self


class StreamAnalysisResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: MediaSource
    hls_analysis: HLSManifestAnalysis | None = None
    effective_drm_status: DRMStatus
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_drm_consistency(self) -> Self:
        if self.source.drm_status is not self.effective_drm_status:
            raise ValueError("MediaSource DRM status must match the effective DRM status.")
        if (
            self.hls_analysis is not None
            and self.hls_analysis.drm_status is not self.effective_drm_status
        ):
            raise ValueError("HLS analysis DRM status must match the effective DRM status.")
        return self


class StreamAnalysisService:
    def __init__(self, hls_analyzer: _HLSAnalyzer | None = None) -> None:
        self._hls_analyzer = hls_analyzer or HLSManifestAnalyzer()

    def analyze(self, request: StreamAnalysisRequest) -> StreamAnalysisResult:
        hls_analysis: HLSManifestAnalysis | None = None
        warnings: tuple[str, ...] = ()

        if request.protocol is SourceProtocol.HLS:
            if request.manifest_text is None:
                effective_drm_status = DRMStatus.UNKNOWN
                warnings = ("Missing HLS manifest text; encryption state cannot be verified.",)
            else:
                hls_analysis = self._hls_analyzer.analyze(request.manifest_text)
                effective_drm_status = hls_analysis.drm_status
                warnings = hls_analysis.warnings
        else:
            effective_drm_status = request.declared_drm_status

        source = self._build_source(request, effective_drm_status)
        return StreamAnalysisResult(
            source=source,
            hls_analysis=hls_analysis,
            effective_drm_status=effective_drm_status,
            warnings=warnings,
        )

    def _build_source(
        self,
        request: StreamAnalysisRequest,
        drm_status: DRMStatus,
    ) -> MediaSource:
        return MediaSource(
            source_id=request.source_id,
            protocol=request.protocol,
            mime_type=request.mime_type,
            codec=request.codec,
            container=request.container,
            bitrate_kbps=request.bitrate_kbps,
            requires_auth=request.requires_auth,
            is_downloadable=request.is_downloadable,
            drm_status=drm_status,
        )
