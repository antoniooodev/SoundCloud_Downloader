from soundcloud_downloader.application.artifact_storage import ArtifactStoragePort
from soundcloud_downloader.domain import (
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
    ErrorCode,
    HLSMediaAssemblyResult,
    HLSSegmentFetchStatus,
    HLSSegmentStagingResult,
    SoundcloudDownloaderError,
    StagedHLSSegment,
)
from soundcloud_downloader.infrastructure.storage import compute_sha256_bytes

_DEFAULT_OUTPUT_PATH = ArtifactRelativePath(value="hls/assembled/media.bin")
_MALFORMED_MESSAGE = "Malformed staged HLS segment result."
_INTEGRITY_MESSAGE = "Staged HLS segment integrity check failed."
_ASSEMBLY_MESSAGE = "Unable to assemble staged HLS media."


class HLSMediaAssemblyError(SoundcloudDownloaderError):
    pass


class HLSMediaAssembler:
    def __init__(
        self,
        *,
        storage: ArtifactStoragePort,
        output_path: ArtifactRelativePath | None = None,
    ) -> None:
        self._storage = storage
        self._output_path = output_path or _DEFAULT_OUTPUT_PATH

    def assemble(
        self,
        *,
        staging_result: HLSSegmentStagingResult,
    ) -> HLSMediaAssemblyResult:
        segments = self._validated_segments(staging_result)
        assembled_parts: list[bytes] = []
        total_bytes = 0

        for segment in segments:
            segment_bytes = self._read_segment(segment)
            self._verify_segment_integrity(segment, segment_bytes)
            assembled_parts.append(segment_bytes)
            total_bytes += len(segment_bytes)

        assembled_bytes = b"".join(assembled_parts)
        try:
            artifact = self._storage.write_bytes(
                relative_path=self._output_path,
                data=assembled_bytes,
            )
        except Exception as exc:
            raise HLSMediaAssemblyError(ErrorCode.STORAGE_FAILED, _ASSEMBLY_MESSAGE) from exc

        return HLSMediaAssemblyResult(
            artifact=self._as_staged_media_artifact(artifact),
            source_segment_count=len(segments),
            total_duration_seconds=sum(segment.duration_seconds for segment in segments),
            total_bytes=total_bytes,
        )

    def _validated_segments(
        self,
        staging_result: HLSSegmentStagingResult,
    ) -> tuple[StagedHLSSegment, ...]:
        if not staging_result.complete or not staging_result.segments:
            raise HLSMediaAssemblyError(ErrorCode.UNKNOWN_UNSAFE, _MALFORMED_MESSAGE)

        indexes = [segment.index for segment in staging_result.segments]
        if len(indexes) != len(set(indexes)):
            raise HLSMediaAssemblyError(ErrorCode.UNKNOWN_UNSAFE, _MALFORMED_MESSAGE)

        sorted_segments = tuple(sorted(staging_result.segments, key=lambda segment: segment.index))
        expected_indexes = list(range(len(sorted_segments)))
        if [segment.index for segment in sorted_segments] != expected_indexes:
            raise HLSMediaAssemblyError(ErrorCode.UNKNOWN_UNSAFE, _MALFORMED_MESSAGE)

        if any(segment.status is not HLSSegmentFetchStatus.STAGED for segment in sorted_segments):
            raise HLSMediaAssemblyError(ErrorCode.UNKNOWN_UNSAFE, _MALFORMED_MESSAGE)

        return sorted_segments

    def _read_segment(self, segment: StagedHLSSegment) -> bytes:
        try:
            segment_bytes = self._storage.read_bytes(relative_path=segment.artifact.relative_path)
        except Exception as exc:
            raise HLSMediaAssemblyError(ErrorCode.STORAGE_FAILED, _ASSEMBLY_MESSAGE) from exc
        if segment_bytes == b"":
            raise HLSMediaAssemblyError(ErrorCode.UNKNOWN_UNSAFE, _INTEGRITY_MESSAGE)
        return segment_bytes

    def _verify_segment_integrity(
        self,
        segment: StagedHLSSegment,
        segment_bytes: bytes,
    ) -> None:
        if segment.artifact.size_bytes is not None and segment.artifact.size_bytes != len(
            segment_bytes
        ):
            raise HLSMediaAssemblyError(ErrorCode.UNKNOWN_UNSAFE, _INTEGRITY_MESSAGE)

        if segment.artifact.checksum is not None:
            actual_checksum = compute_sha256_bytes(segment_bytes)
            if actual_checksum != segment.artifact.checksum:
                raise HLSMediaAssemblyError(ErrorCode.UNKNOWN_UNSAFE, _INTEGRITY_MESSAGE)

    def _as_staged_media_artifact(self, artifact: ArtifactMetadata) -> ArtifactMetadata:
        return artifact.model_copy(update={"kind": ArtifactKind.STAGED_MEDIA})
