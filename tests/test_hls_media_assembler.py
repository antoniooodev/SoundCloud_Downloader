import socket
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ArtifactChecksum,
    ArtifactFormat,
    ArtifactId,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
    HLSSegmentFetchStatus,
    HLSSegmentStagingResult,
    SoundCloudResolvedStreamUrl,
    StagedHLSSegment,
)
from soundcloud_downloader.infrastructure.soundcloud import (
    HLSMediaAssembler,
    HLSMediaAssemblyError,
)
from soundcloud_downloader.infrastructure.storage import LocalArtifactStorage, compute_sha256_bytes

MANIFEST_URL = "https://media.soundcloud.test/path/playlist.m3u8?Policy=dummy"
SEGMENT_URL = "https://media.soundcloud.test/path/segment0.ts?Policy=dummy"
SEGMENT_BYTES = (b"aaa", b"bbbb", b"cc")


def test_assembler_reads_staged_segment_artifacts(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"abc",))

    result = HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    assert result.total_bytes == 3


def test_assembler_concatenates_one_segment(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"abc",))

    HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    assert storage.read_bytes(relative_path=ArtifactRelativePath(value="hls/assembled/media.bin")) == b"abc"


def test_assembler_concatenates_multiple_segments_in_index_order(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)

    HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    assert storage.read_bytes(relative_path=ArtifactRelativePath(value="hls/assembled/media.bin")) == b"aaabbbbcc"


def test_assembler_sorts_out_of_order_staged_segment_metadata_safely(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)
    out_of_order = staging_result.model_copy(
        update={"segments": (staging_result.segments[2], staging_result.segments[0], staging_result.segments[1])}
    )

    HLSMediaAssembler(storage=storage).assemble(staging_result=out_of_order)

    assert storage.read_bytes(relative_path=ArtifactRelativePath(value="hls/assembled/media.bin")) == b"aaabbbbcc"


def test_assembler_rejects_duplicate_segment_indexes(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"aaa", b"bbbb"))
    duplicate = staging_result.model_copy(
        update={"segments": (staging_result.segments[0], staging_result.segments[1].model_copy(update={"index": 0}))}
    )

    with pytest.raises(HLSMediaAssemblyError):
        HLSMediaAssembler(storage=storage).assemble(staging_result=duplicate)


def test_assembler_rejects_missing_segment_index_gap(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"aaa", b"bbbb"))
    gapped = staging_result.model_copy(
        update={"segments": (staging_result.segments[0], staging_result.segments[1].model_copy(update={"index": 2}))}
    )

    with pytest.raises(HLSMediaAssemblyError):
        HLSMediaAssembler(storage=storage).assemble(staging_result=gapped)


def test_assembler_rejects_staging_result_complete_false(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)
    incomplete = staging_result.model_copy(update={"complete": False})

    with pytest.raises(HLSMediaAssemblyError):
        HLSMediaAssembler(storage=storage).assemble(staging_result=incomplete)


def test_assembler_rejects_segment_status_failed(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)
    failed_segment = staging_result.segments[0].model_copy(
        update={"status": HLSSegmentFetchStatus.FAILED}
    )
    failed = staging_result.model_copy(update={"segments": (failed_segment, *staging_result.segments[1:])})

    with pytest.raises(HLSMediaAssemblyError):
        HLSMediaAssembler(storage=storage).assemble(staging_result=failed)


def test_assembler_rejects_empty_segment_artifact_bytes(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"",))
    no_size_metadata = staging_result.model_copy(
        update={
            "segments": (
                staging_result.segments[0].model_copy(
                    update={"artifact": staging_result.segments[0].artifact.model_copy(update={"size_bytes": None})}
                ),
            )
        }
    )

    with pytest.raises(HLSMediaAssemblyError):
        HLSMediaAssembler(storage=storage).assemble(staging_result=no_size_metadata)


def test_assembler_verifies_artifact_size_bytes_when_present(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"abc",))

    result = HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    assert result.total_bytes == 3


def test_assembler_rejects_size_mismatch(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"abc",))
    bad_segment = staging_result.segments[0].model_copy(
        update={"artifact": staging_result.segments[0].artifact.model_copy(update={"size_bytes": 99})}
    )

    with pytest.raises(HLSMediaAssemblyError):
        HLSMediaAssembler(storage=storage).assemble(
            staging_result=staging_result.model_copy(update={"segments": (bad_segment,)})
        )


def test_assembler_verifies_artifact_checksum_when_present(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"abc",))

    result = HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    assert result.total_bytes == 3


def test_assembler_rejects_checksum_mismatch(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"abc",))
    bad_checksum = ArtifactChecksum(value="0" * 64)
    bad_segment = staging_result.segments[0].model_copy(
        update={"artifact": staging_result.segments[0].artifact.model_copy(update={"checksum": bad_checksum})}
    )

    with pytest.raises(HLSMediaAssemblyError):
        HLSMediaAssembler(storage=storage).assemble(
            staging_result=staging_result.model_copy(update={"segments": (bad_segment,)})
        )


def test_assembler_writes_assembled_artifact_through_storage(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)

    result = HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    assert storage.exists(relative_path=result.artifact.relative_path) is True


def test_assembled_bytes_equal_concatenation_of_source_bytes(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)

    result = HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    assert storage.read_bytes(relative_path=result.artifact.relative_path) == b"aaabbbbcc"


def test_assembly_result_has_source_segment_count(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)

    assert HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result).source_segment_count == 3


def test_assembly_result_has_total_duration_seconds(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)

    assert HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result).total_duration_seconds == 18.0


def test_assembly_result_has_total_bytes(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)

    assert HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result).total_bytes == 9


def test_assembly_result_artifact_kind_is_staged_media(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)

    result = HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    assert result.artifact.kind is ArtifactKind.STAGED_MEDIA


def test_default_output_path_is_safe_and_deterministic(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)

    result = HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    assert result.artifact.relative_path == ArtifactRelativePath(value="hls/assembled/media.bin")


def test_custom_output_path_is_honored(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)
    output_path = ArtifactRelativePath(value="custom/output.bin")

    result = HLSMediaAssembler(storage=storage, output_path=output_path).assemble(
        staging_result=staging_result
    )

    assert result.artifact.relative_path == output_path


def test_custom_output_path_rejects_unsafe_artifact_relative_path() -> None:
    with pytest.raises(ValidationError):
        ArtifactRelativePath(value="../output.bin")


def test_filesystem_writes_disabled_fails_safely_through_storage(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)
    read_only_storage = LocalArtifactStorage(
        AppSettings(artifact_storage_root=tmp_path / "artifacts", allow_filesystem_writes=False)
    )

    with pytest.raises(HLSMediaAssemblyError):
        HLSMediaAssembler(storage=read_only_storage).assemble(staging_result=staging_result)


def test_missing_staged_artifact_raises_hls_media_assembly_error_safely(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path)
    storage.delete(relative_path=staging_result.segments[0].artifact.relative_path)

    with pytest.raises(HLSMediaAssemblyError) as exc_info:
        HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    assert MANIFEST_URL not in str(exc_info.value)
    assert SEGMENT_URL not in str(exc_info.value)


def test_storage_read_failure_raises_hls_media_assembly_error_safely() -> None:
    class FailingStorage:
        def write_bytes(self, *, relative_path: ArtifactRelativePath, data: bytes) -> ArtifactMetadata:
            raise AssertionError("not used")

        def read_bytes(self, *, relative_path: ArtifactRelativePath) -> bytes:
            raise RuntimeError("read failed")

        def exists(self, *, relative_path: ArtifactRelativePath) -> bool:
            return False

        def delete(self, *, relative_path: ArtifactRelativePath) -> None:
            return None

    staging_result = _staging_result_from_artifacts((_artifact(0, b"aaa"),))

    with pytest.raises(HLSMediaAssemblyError) as exc_info:
        HLSMediaAssembler(storage=FailingStorage()).assemble(staging_result=staging_result)

    assert MANIFEST_URL not in str(exc_info.value)
    assert SEGMENT_URL not in str(exc_info.value)


def test_error_messages_do_not_contain_manifest_url(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"abc",))
    bad_segment = staging_result.segments[0].model_copy(
        update={"artifact": staging_result.segments[0].artifact.model_copy(update={"size_bytes": 99})}
    )

    with pytest.raises(HLSMediaAssemblyError) as exc_info:
        HLSMediaAssembler(storage=storage).assemble(
            staging_result=staging_result.model_copy(update={"segments": (bad_segment,)})
        )

    assert MANIFEST_URL not in str(exc_info.value)


def test_error_messages_do_not_contain_segment_url(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"abc",))
    bad_segment = staging_result.segments[0].model_copy(
        update={"artifact": staging_result.segments[0].artifact.model_copy(update={"size_bytes": 99})}
    )

    with pytest.raises(HLSMediaAssemblyError) as exc_info:
        HLSMediaAssembler(storage=storage).assemble(
            staging_result=staging_result.model_copy(update={"segments": (bad_segment,)})
        )

    assert SEGMENT_URL not in str(exc_info.value)


def test_error_messages_do_not_contain_raw_bytes(tmp_path: Path) -> None:
    storage, staging_result = _prepared_storage(tmp_path, segment_bytes=(b"secret-bytes",))
    bad_segment = staging_result.segments[0].model_copy(
        update={"artifact": staging_result.segments[0].artifact.model_copy(update={"size_bytes": 99})}
    )

    with pytest.raises(HLSMediaAssemblyError) as exc_info:
        HLSMediaAssembler(storage=storage).assemble(
            staging_result=staging_result.model_copy(update={"segments": (bad_segment,)})
        )

    assert "secret-bytes" not in str(exc_info.value)


def test_tests_perform_no_network_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    storage, staging_result = _prepared_storage(tmp_path)
    assert HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result).total_bytes == 9


def test_tests_write_only_inside_pytest_tmp_path(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    storage, staging_result = _prepared_storage(tmp_path, root=root)

    HLSMediaAssembler(storage=storage).assemble(staging_result=staging_result)

    for file_path in root.rglob("*"):
        assert file_path.resolve().is_relative_to(tmp_path.resolve())


def _prepared_storage(
    tmp_path: Path,
    *,
    segment_bytes: tuple[bytes, ...] = SEGMENT_BYTES,
    root: Path | None = None,
) -> tuple[LocalArtifactStorage, HLSSegmentStagingResult]:
    storage = LocalArtifactStorage(
        AppSettings(
            artifact_storage_root=root or tmp_path / "artifacts",
            allow_filesystem_writes=True,
        )
    )
    artifacts: list[ArtifactMetadata] = []
    for index, data in enumerate(segment_bytes):
        relative_path = ArtifactRelativePath(value=f"hls/staged/segments/{index:06d}.bin")
        artifact = storage.write_bytes(relative_path=relative_path, data=data).model_copy(
            update={"kind": ArtifactKind.HLS_SEGMENT}
        )
        artifacts.append(artifact)
    return storage, _staging_result_from_artifacts(tuple(artifacts))


def _manifest_url() -> SoundCloudResolvedStreamUrl:
    return SoundCloudResolvedStreamUrl(value=SecretStr(MANIFEST_URL))


def _artifact(index: int, data: bytes) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=ArtifactId(value=f"segment-{index}"),
        kind=ArtifactKind.HLS_SEGMENT,
        format=ArtifactFormat.BIN,
        relative_path=ArtifactRelativePath(value=f"hls/staged/segments/{index:06d}.bin"),
        size_bytes=len(data),
        checksum=compute_sha256_bytes(data),
    )


def _staging_result_from_artifacts(
    artifacts: tuple[ArtifactMetadata, ...],
) -> HLSSegmentStagingResult:
    return HLSSegmentStagingResult(
        manifest_url=_manifest_url(),
        segments=tuple(
            StagedHLSSegment(
                index=index,
                artifact=artifact,
                duration_seconds=(6.0, 6.5, 5.5)[index],
            )
            for index, artifact in enumerate(artifacts)
        ),
        total_bytes=sum(artifact.size_bytes or 0 for artifact in artifacts),
    )
