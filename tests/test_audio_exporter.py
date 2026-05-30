import socket
from pathlib import Path

import pytest

from soundcloud_downloader.application import FFMPEGCommand, FFMPEGResult
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ArtifactFormat,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
    AudioArtworkArtifact,
    AudioExportFormat,
    AudioExportMetadata,
    AudioExportRequest,
    AudioExportResult,
)
from soundcloud_downloader.infrastructure.media import AudioExporter, AudioExportError
from soundcloud_downloader.infrastructure.storage import (
    LocalArtifactStorage,
    LocalTemporaryWorkspace,
)

INPUT_BYTES = b"input-media"
ARTWORK_BYTES = b"artwork-bytes"
OUTPUT_BYTES = b"fake-export-output"


def test_exporter_reads_input_artifact_from_storage(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(tmp_path, AudioExportFormat.MP3, "audio/final.mp3")

    assert runner.input_bytes == INPUT_BYTES


def test_exporter_writes_temp_input_file(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(tmp_path, AudioExportFormat.MP3, "audio/final.mp3")

    assert runner.input_path is not None
    assert runner.input_path.name == "input.bin"
    assert runner.workspace_path is not None
    assert runner.input_path.parent == runner.workspace_path


def test_mp3_export_builds_libmp3lame_command(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(tmp_path, AudioExportFormat.MP3, "audio/final.mp3")

    assert "-codec:a" in runner.command.args
    assert "libmp3lame" in runner.command.args


def test_mp3_export_uses_128k_bitrate(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(tmp_path, AudioExportFormat.MP3, "audio/final.mp3")

    assert "-b:a" in runner.command.args
    assert "128k" in runner.command.args


def test_wav_export_builds_pcm_s16le_command(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(tmp_path, AudioExportFormat.WAV, "audio/final.wav")

    assert "-codec:a" in runner.command.args
    assert "pcm_s16le" in runner.command.args


def test_m4a_export_builds_copy_faststart_command(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(tmp_path, AudioExportFormat.M4A, "audio/final.m4a")

    assert "-c" in runner.command.args
    assert "copy" in runner.command.args
    assert "-movflags" in runner.command.args
    assert "+faststart" in runner.command.args


def test_metadata_fields_are_passed_as_separate_ffmpeg_args(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(
        tmp_path,
        AudioExportFormat.MP3,
        "audio/final.mp3",
        metadata=AudioExportMetadata(title="Track", artist="Artist", album="Album"),
    )

    assert ("-metadata", "title=Track") == _metadata_arg_pair(runner.command, "title=Track")
    assert ("-metadata", "artist=Artist") == _metadata_arg_pair(runner.command, "artist=Artist")
    assert ("-metadata", "album=Album") == _metadata_arg_pair(runner.command, "album=Album")


def test_artwork_artifact_is_read_when_provided(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(
        tmp_path,
        AudioExportFormat.MP3,
        "audio/final.mp3",
        with_artwork=True,
    )

    assert runner.artwork_bytes == ARTWORK_BYTES


def test_artwork_file_is_passed_to_ffmpeg_when_provided(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(
        tmp_path,
        AudioExportFormat.M4A,
        "audio/final.m4a",
        with_artwork=True,
    )

    assert runner.artwork_path is not None
    assert str(runner.artwork_path) in runner.command.args


def test_exporter_runs_through_injected_ffmpeg_runner_port(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(tmp_path, AudioExportFormat.MP3, "audio/final.mp3")

    assert runner.calls == 1


def test_exporter_writes_final_artifact_through_storage(tmp_path: Path) -> None:
    _runner, result, storage = _run_export(tmp_path, AudioExportFormat.MP3, "audio/final.mp3")

    assert storage.exists(relative_path=result.output_artifact.relative_path) is True


def test_mp3_output_artifact_has_final_audio_kind_and_mp3_format(tmp_path: Path) -> None:
    _runner, result, _storage = _run_export(tmp_path, AudioExportFormat.MP3, "audio/final.mp3")

    assert result.output_artifact.kind is ArtifactKind.FINAL_AUDIO
    assert result.output_artifact.format is ArtifactFormat.MP3


def test_wav_output_artifact_has_final_audio_kind_and_wav_format(tmp_path: Path) -> None:
    _runner, result, _storage = _run_export(tmp_path, AudioExportFormat.WAV, "audio/final.wav")

    assert result.output_artifact.kind is ArtifactKind.FINAL_AUDIO
    assert result.output_artifact.format is ArtifactFormat.WAV


def test_m4a_output_artifact_has_final_audio_kind_and_m4a_format(tmp_path: Path) -> None:
    _runner, result, _storage = _run_export(tmp_path, AudioExportFormat.M4A, "audio/final.m4a")

    assert result.output_artifact.kind is ArtifactKind.FINAL_AUDIO
    assert result.output_artifact.format is ArtifactFormat.M4A


def test_export_result_marks_metadata_embedded_when_metadata_provided(tmp_path: Path) -> None:
    _runner, result, _storage = _run_export(
        tmp_path,
        AudioExportFormat.WAV,
        "audio/final.wav",
        metadata=AudioExportMetadata(title="Track"),
    )

    assert result.metadata_embedded is True


def test_export_result_marks_artwork_embedded_when_artwork_provided(tmp_path: Path) -> None:
    _runner, result, _storage = _run_export(
        tmp_path,
        AudioExportFormat.MP3,
        "audio/final.mp3",
        with_artwork=True,
    )

    assert result.artwork_embedded is True


def test_exporter_cleans_workspace_after_success(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(tmp_path, AudioExportFormat.MP3, "audio/final.mp3")

    assert runner.workspace_path is not None
    assert runner.workspace_path.exists() is False


def test_exporter_rejects_empty_input_artifact_bytes(tmp_path: Path) -> None:
    storage, workspace, input_artifact, _artwork = _prepared_storage(tmp_path, input_bytes=b"")

    with pytest.raises(AudioExportError):
        AudioExporter(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(),
        ).export(_request(input_artifact, AudioExportFormat.MP3, "audio/final.mp3"))


def test_exporter_rejects_empty_artwork_bytes(tmp_path: Path) -> None:
    storage, workspace, input_artifact, artwork = _prepared_storage(tmp_path, artwork_bytes=b"")

    with pytest.raises(AudioExportError):
        AudioExporter(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(),
        ).export(
            _request(
                input_artifact,
                AudioExportFormat.MP3,
                "audio/final.mp3",
                artwork=AudioArtworkArtifact(artifact=artwork),
            )
        )


def test_exporter_wraps_ffmpeg_failure_safely(tmp_path: Path) -> None:
    storage, workspace, input_artifact, _artwork = _prepared_storage(tmp_path)

    with pytest.raises(AudioExportError) as exc_info:
        AudioExporter(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(error=RuntimeError("access_token=secret")),
        ).export(_request(input_artifact, AudioExportFormat.MP3, "audio/final.mp3"))

    assert "access_token" not in str(exc_info.value)


def test_exporter_rejects_missing_output_temp_file(tmp_path: Path) -> None:
    storage, workspace, input_artifact, _artwork = _prepared_storage(tmp_path)

    with pytest.raises(AudioExportError):
        AudioExporter(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(write_output=False),
        ).export(_request(input_artifact, AudioExportFormat.MP3, "audio/final.mp3"))


def test_exporter_rejects_empty_output_temp_file(tmp_path: Path) -> None:
    storage, workspace, input_artifact, _artwork = _prepared_storage(tmp_path)

    with pytest.raises(AudioExportError):
        AudioExporter(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(output_bytes=b""),
        ).export(_request(input_artifact, AudioExportFormat.MP3, "audio/final.mp3"))


def test_exporter_wraps_storage_read_failure_safely(tmp_path: Path) -> None:
    storage, workspace, input_artifact, _artwork = _prepared_storage(tmp_path)

    with pytest.raises(AudioExportError):
        AudioExporter(
            settings=_settings(tmp_path),
            storage=FailingStorage(delegate=storage, read_error=True),
            workspace=workspace,
            ffmpeg_runner=FakeRunner(),
        ).export(_request(input_artifact, AudioExportFormat.MP3, "audio/final.mp3"))


def test_exporter_wraps_storage_write_failure_safely(tmp_path: Path) -> None:
    storage, workspace, input_artifact, _artwork = _prepared_storage(tmp_path)

    with pytest.raises(AudioExportError):
        AudioExporter(
            settings=_settings(tmp_path),
            storage=FailingStorage(delegate=storage, write_error=True),
            workspace=workspace,
            ffmpeg_runner=FakeRunner(),
        ).export(_request(input_artifact, AudioExportFormat.MP3, "audio/final.mp3"))


def test_error_messages_do_not_contain_raw_bytes(tmp_path: Path) -> None:
    storage, workspace, input_artifact, _artwork = _prepared_storage(
        tmp_path,
        input_bytes=b"secret-bytes",
    )

    with pytest.raises(AudioExportError) as exc_info:
        AudioExporter(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(error=RuntimeError("failed")),
        ).export(_request(input_artifact, AudioExportFormat.MP3, "audio/final.mp3"))

    assert "secret-bytes" not in str(exc_info.value)


def test_error_messages_do_not_contain_sensitive_markers(tmp_path: Path) -> None:
    storage, workspace, input_artifact, _artwork = _prepared_storage(tmp_path)

    with pytest.raises(AudioExportError) as exc_info:
        AudioExporter(
            settings=_settings(tmp_path),
            storage=storage,
            workspace=workspace,
            ffmpeg_runner=FakeRunner(
                error=RuntimeError("access_token refresh_token client_secret")
            ),
        ).export(_request(input_artifact, AudioExportFormat.MP3, "audio/final.mp3"))

    message = str(exc_info.value)
    assert "access_token" not in message
    assert "refresh_token" not in message
    assert "client_secret" not in message


def test_no_real_ffmpeg_process_is_executed(tmp_path: Path) -> None:
    runner, _result, _storage = _run_export(tmp_path, AudioExportFormat.MP3, "audio/final.mp3")

    assert isinstance(runner, FakeRunner)
    assert runner.calls == 1


def test_tests_perform_no_network_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    _runner, result, _storage = _run_export(tmp_path, AudioExportFormat.MP3, "audio/final.mp3")
    assert result.status.value == "succeeded"


def test_tests_write_only_inside_pytest_tmp_path(tmp_path: Path) -> None:
    storage_root = tmp_path / "artifacts"
    runner, _result, _storage = _run_export(
        tmp_path,
        AudioExportFormat.MP3,
        "audio/final.mp3",
        storage_root=storage_root,
    )

    assert runner.workspace_path is not None
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
        self.artwork_path: Path | None = None
        self.workspace_path: Path | None = None
        self.input_bytes = b""
        self.artwork_bytes: bytes | None = None

    def run(self, command: FFMPEGCommand) -> FFMPEGResult:
        self.calls += 1
        self.command = command
        input_paths = _input_paths(command)
        self.input_path = input_paths[0]
        self.artwork_path = input_paths[1] if len(input_paths) > 1 else None
        output_path = Path(command.args[-1])
        self.workspace_path = self.input_path.parent
        self.input_bytes = self.input_path.read_bytes()
        if self.artwork_path is not None:
            self.artwork_bytes = self.artwork_path.read_bytes()
        if self.error is not None:
            raise self.error
        if self.write_output:
            output_path.write_bytes(self.output_bytes)
        return FFMPEGResult(return_code=0)


class FailingStorage:
    def __init__(
        self,
        *,
        delegate: LocalArtifactStorage,
        read_error: bool = False,
        write_error: bool = False,
    ) -> None:
        self.delegate = delegate
        self.read_error = read_error
        self.write_error = write_error

    def write_bytes(self, *, relative_path: ArtifactRelativePath, data: bytes) -> ArtifactMetadata:
        if self.write_error:
            raise RuntimeError("write failed with client_secret=secret")
        return self.delegate.write_bytes(relative_path=relative_path, data=data)

    def read_bytes(self, *, relative_path: ArtifactRelativePath) -> bytes:
        if self.read_error:
            raise RuntimeError("read failed with refresh_token=secret")
        return self.delegate.read_bytes(relative_path=relative_path)

    def exists(self, *, relative_path: ArtifactRelativePath) -> bool:
        return self.delegate.exists(relative_path=relative_path)

    def delete(self, *, relative_path: ArtifactRelativePath) -> None:
        self.delegate.delete(relative_path=relative_path)


def _run_export(
    tmp_path: Path,
    output_format: AudioExportFormat,
    output_path: str,
    *,
    metadata: AudioExportMetadata | None = None,
    with_artwork: bool = False,
    storage_root: Path | None = None,
) -> tuple[FakeRunner, AudioExportResult, LocalArtifactStorage]:
    storage, workspace, input_artifact, artwork_artifact = _prepared_storage(
        tmp_path,
        storage_root=storage_root,
    )
    runner = FakeRunner()
    artwork = AudioArtworkArtifact(artifact=artwork_artifact) if with_artwork else None
    result = AudioExporter(
        settings=_settings(tmp_path),
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=runner,
    ).export(
        _request(
            input_artifact,
            output_format,
            output_path,
            metadata=metadata,
            artwork=artwork,
        )
    )
    return runner, result, storage


def _prepared_storage(
    tmp_path: Path,
    *,
    input_bytes: bytes = INPUT_BYTES,
    artwork_bytes: bytes = ARTWORK_BYTES,
    storage_root: Path | None = None,
) -> tuple[LocalArtifactStorage, LocalTemporaryWorkspace, ArtifactMetadata, ArtifactMetadata]:
    settings = AppSettings(
        allow_filesystem_writes=True,
        artifact_storage_root=storage_root or tmp_path / "artifacts",
        artifact_temp_root=tmp_path / "tmp",
    )
    storage = LocalArtifactStorage(settings)
    workspace = LocalTemporaryWorkspace(settings)
    input_artifact = storage.write_bytes(
        relative_path=ArtifactRelativePath(value="audio/input.m4a"),
        data=input_bytes,
    ).model_copy(update={"kind": ArtifactKind.FINAL_AUDIO, "format": ArtifactFormat.M4A})
    artwork_artifact = storage.write_bytes(
        relative_path=ArtifactRelativePath(value="artwork/cover.jpg"),
        data=artwork_bytes,
    ).model_copy(update={"kind": ArtifactKind.ARTWORK, "format": ArtifactFormat.JPG})
    return storage, workspace, input_artifact, artwork_artifact


def _request(
    input_artifact: ArtifactMetadata,
    output_format: AudioExportFormat,
    output_path: str,
    *,
    metadata: AudioExportMetadata | None = None,
    artwork: AudioArtworkArtifact | None = None,
) -> AudioExportRequest:
    return AudioExportRequest(
        input_artifact=input_artifact,
        output_format=output_format,
        output_path=ArtifactRelativePath(value=output_path),
        metadata=metadata,
        artwork=artwork,
    )


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        allow_filesystem_writes=True,
        artifact_temp_root=tmp_path / "tmp",
        ffmpeg_binary="ffmpeg",
    )


def _input_paths(command: FFMPEGCommand) -> list[Path]:
    return [Path(command.args[index + 1]) for index, arg in enumerate(command.args) if arg == "-i"]


def _metadata_arg_pair(command: FFMPEGCommand, value: str) -> tuple[str, str]:
    index = command.args.index(value)
    return command.args[index - 1], command.args[index]
