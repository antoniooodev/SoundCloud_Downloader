import socket
from pathlib import Path

import pytest
from pydantic import ValidationError

from soundcloud_downloader.application import FFMPEGCommand, FFMPEGResult
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ArtifactFormat,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
)
from soundcloud_downloader.infrastructure.media import M4ARemuxer, M4ARemuxError
from soundcloud_downloader.infrastructure.storage import (
    LocalArtifactStorage,
    LocalTemporaryWorkspace,
)

INPUT_BYTES = b"assembled-media"
OUTPUT_BYTES = b"fake-m4a-output"


def test_remuxer_reads_staged_media_artifact_from_storage(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path)
    runner = FakeRunner()

    M4ARemuxer(
        settings=_settings(tmp_path),
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=runner,
    ).remux_to_m4a(input_artifact=artifact)

    assert runner.input_bytes == INPUT_BYTES


def test_remuxer_writes_temp_input_file_in_workspace(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path)
    runner = FakeRunner()

    M4ARemuxer(
        settings=_settings(tmp_path),
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=runner,
    ).remux_to_m4a(input_artifact=artifact)

    assert runner.input_path is not None
    assert runner.input_path.name == "input.bin"
    assert runner.workspace_path is not None
    assert runner.input_path.parent == runner.workspace_path


def test_remuxer_builds_ffmpeg_command_with_copy_codec(tmp_path: Path) -> None:
    runner = _run_remux(tmp_path)

    assert "-c" in runner.command.args
    assert "copy" in runner.command.args


def test_remuxer_builds_ffmpeg_command_with_faststart(tmp_path: Path) -> None:
    runner = _run_remux(tmp_path)

    assert "-movflags" in runner.command.args
    assert "+faststart" in runner.command.args


def test_remuxer_runs_through_injected_ffmpeg_runner_port(tmp_path: Path) -> None:
    runner = _run_remux(tmp_path)

    assert runner.calls == 1


def test_remuxer_writes_final_m4a_artifact_through_storage(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path)
    runner = FakeRunner()

    result = M4ARemuxer(
        settings=_settings(tmp_path),
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=runner,
    ).remux_to_m4a(input_artifact=artifact)

    assert storage.exists(relative_path=result.output_artifact.artifact.relative_path) is True


def test_remux_result_output_artifact_has_final_audio_kind(tmp_path: Path) -> None:
    result = _run_remux_result(tmp_path)

    assert result.output_artifact.artifact.kind is ArtifactKind.FINAL_AUDIO


def test_remux_result_output_artifact_has_m4a_format(tmp_path: Path) -> None:
    result = _run_remux_result(tmp_path)

    assert result.output_artifact.artifact.format is ArtifactFormat.M4A


def test_remux_result_output_bytes_equal_fake_ffmpeg_output(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path)

    result = M4ARemuxer(
        settings=_settings(tmp_path),
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=FakeRunner(),
    ).remux_to_m4a(input_artifact=artifact)

    assert storage.read_bytes(relative_path=result.output_artifact.artifact.relative_path) == OUTPUT_BYTES


def test_remuxer_cleans_workspace_after_success(tmp_path: Path) -> None:
    runner = _run_remux(tmp_path)

    assert runner.workspace_path is not None
    assert runner.workspace_path.exists() is False


def test_remuxer_rejects_empty_input_artifact_bytes(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path, input_bytes=b"")

    with pytest.raises(M4ARemuxError):
        M4ARemuxer(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(),
        ).remux_to_m4a(input_artifact=artifact)


def test_remuxer_wraps_ffmpeg_failure_safely(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path)

    with pytest.raises(M4ARemuxError) as exc_info:
        M4ARemuxer(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(error=RuntimeError("ffmpeg failed with access_token=secret")),
        ).remux_to_m4a(input_artifact=artifact)

    assert "access_token" not in str(exc_info.value)


def test_remuxer_rejects_missing_output_temp_file(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path)

    with pytest.raises(M4ARemuxError):
        M4ARemuxer(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(write_output=False),
        ).remux_to_m4a(input_artifact=artifact)


def test_remuxer_rejects_empty_output_temp_file(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path)

    with pytest.raises(M4ARemuxError):
        M4ARemuxer(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(output_bytes=b""),
        ).remux_to_m4a(input_artifact=artifact)


def test_remuxer_wraps_storage_read_failure_safely(tmp_path: Path) -> None:
    _storage, workspace, artifact = _prepared_storage(tmp_path)

    with pytest.raises(M4ARemuxError):
        M4ARemuxer(
            settings=_settings(tmp_path),
            storage=FailingStorage(read_error=True),
            workspace=workspace,
            ffmpeg_runner=FakeRunner(),
        ).remux_to_m4a(input_artifact=artifact)


def test_remuxer_wraps_storage_write_failure_safely(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path)

    with pytest.raises(M4ARemuxError):
        M4ARemuxer(
            settings=_settings(tmp_path),
            storage=FailingStorage(delegate=storage, write_error=True),
            workspace=workspace,
            ffmpeg_runner=FakeRunner(),
        ).remux_to_m4a(input_artifact=artifact)


def test_remuxer_uses_default_output_path(tmp_path: Path) -> None:
    result = _run_remux_result(tmp_path)

    assert result.output_artifact.artifact.relative_path == ArtifactRelativePath(
        value="audio/final.m4a"
    )


def test_remuxer_honors_custom_output_path(tmp_path: Path) -> None:
    output_path = ArtifactRelativePath(value="custom/final.m4a")
    storage, workspace, artifact = _prepared_storage(tmp_path)

    result = M4ARemuxer(
        settings=_settings(tmp_path),
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=FakeRunner(),
        output_path=output_path,
    ).remux_to_m4a(input_artifact=artifact)

    assert result.output_artifact.artifact.relative_path == output_path


def test_custom_output_path_rejects_unsafe_artifact_relative_path() -> None:
    with pytest.raises(ValidationError):
        ArtifactRelativePath(value="../audio/final.m4a")


def test_error_messages_do_not_contain_raw_bytes(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path, input_bytes=b"secret-bytes")

    with pytest.raises(M4ARemuxError) as exc_info:
        M4ARemuxer(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(error=RuntimeError("failed")),
        ).remux_to_m4a(input_artifact=artifact)

    assert "secret-bytes" not in str(exc_info.value)


def test_error_messages_do_not_contain_sensitive_markers(tmp_path: Path) -> None:
    storage, workspace, artifact = _prepared_storage(tmp_path)

    with pytest.raises(M4ARemuxError) as exc_info:
        M4ARemuxer(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(error=RuntimeError("access_token refresh_token client_secret")),
        ).remux_to_m4a(input_artifact=artifact)

    message = str(exc_info.value)
    assert "access_token" not in message
    assert "refresh_token" not in message
    assert "client_secret" not in message


def test_no_real_ffmpeg_process_is_executed(tmp_path: Path) -> None:
    runner = _run_remux(tmp_path)

    assert isinstance(runner, FakeRunner)
    assert runner.calls == 1


def test_tests_perform_no_network_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert _run_remux_result(tmp_path).status.value == "succeeded"


def test_tests_write_only_inside_pytest_tmp_path(tmp_path: Path) -> None:
    storage_root = tmp_path / "artifacts"
    storage, workspace, artifact = _prepared_storage(tmp_path, storage_root=storage_root)

    M4ARemuxer(
        settings=_settings(tmp_path),
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=FakeRunner(),
    ).remux_to_m4a(input_artifact=artifact)

    for file_path in tmp_path.rglob("*"):
        assert file_path.resolve().is_relative_to(tmp_path.resolve())


class FakeRunner:
    def __init__(
        self,
        *,
        output_bytes: bytes = OUTPUT_BYTES,
        write_output: bool = True,
        error: Exception | None = None,
    ) -> None:
        self.output_bytes = output_bytes
        self.write_output = write_output
        self.error = error
        self.calls = 0
        self.command = FFMPEGCommand(args=("ffmpeg", "-version"))
        self.input_path: Path | None = None
        self.workspace_path: Path | None = None

    def run(self, command: FFMPEGCommand) -> FFMPEGResult:
        self.calls += 1
        self.command = command
        self.input_path = Path(command.args[6])
        output_path = Path(command.args[-1])
        self.workspace_path = self.input_path.parent
        self.input_bytes = self.input_path.read_bytes()
        if self.error is not None:
            raise self.error
        if self.write_output:
            output_path.write_bytes(self.output_bytes)
        return FFMPEGResult(return_code=0)


class FailingStorage:
    def __init__(
        self,
        *,
        delegate: LocalArtifactStorage | None = None,
        read_error: bool = False,
        write_error: bool = False,
    ) -> None:
        self.delegate = delegate
        self.read_error = read_error
        self.write_error = write_error

    def write_bytes(self, *, relative_path: ArtifactRelativePath, data: bytes) -> ArtifactMetadata:
        if self.write_error:
            raise RuntimeError("write failed")
        if self.delegate is None:
            raise AssertionError("delegate required")
        return self.delegate.write_bytes(relative_path=relative_path, data=data)

    def read_bytes(self, *, relative_path: ArtifactRelativePath) -> bytes:
        if self.read_error:
            raise RuntimeError("read failed")
        if self.delegate is None:
            raise AssertionError("delegate required")
        return self.delegate.read_bytes(relative_path=relative_path)

    def exists(self, *, relative_path: ArtifactRelativePath) -> bool:
        return False if self.delegate is None else self.delegate.exists(relative_path=relative_path)

    def delete(self, *, relative_path: ArtifactRelativePath) -> None:
        if self.delegate is not None:
            self.delegate.delete(relative_path=relative_path)


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        allow_filesystem_writes=True,
        artifact_temp_root=tmp_path / "tmp",
        ffmpeg_binary="ffmpeg",
    )


def _prepared_storage(
    tmp_path: Path,
    *,
    input_bytes: bytes = INPUT_BYTES,
    storage_root: Path | None = None,
) -> tuple[LocalArtifactStorage, LocalTemporaryWorkspace, ArtifactMetadata]:
    settings = AppSettings(
        allow_filesystem_writes=True,
        artifact_storage_root=storage_root or tmp_path / "artifacts",
        artifact_temp_root=tmp_path / "tmp",
    )
    storage = LocalArtifactStorage(settings)
    workspace = LocalTemporaryWorkspace(settings)
    artifact = storage.write_bytes(
        relative_path=ArtifactRelativePath(value="hls/assembled/media.bin"),
        data=input_bytes,
    ).model_copy(update={"kind": ArtifactKind.STAGED_MEDIA})
    return storage, workspace, artifact


def _run_remux(tmp_path: Path) -> FakeRunner:
    storage, workspace, artifact = _prepared_storage(tmp_path)
    runner = FakeRunner()
    M4ARemuxer(
        settings=_settings(tmp_path),
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=runner,
    ).remux_to_m4a(input_artifact=artifact)
    return runner


def _run_remux_result(tmp_path: Path):
    storage, workspace, artifact = _prepared_storage(tmp_path)
    return M4ARemuxer(
        settings=_settings(tmp_path),
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=FakeRunner(),
    ).remux_to_m4a(input_artifact=artifact)
