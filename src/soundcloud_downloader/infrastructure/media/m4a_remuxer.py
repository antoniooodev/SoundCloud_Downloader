from pathlib import Path

from soundcloud_downloader.application.artifact_storage import (
    ArtifactStoragePort,
    TemporaryWorkspacePort,
)
from soundcloud_downloader.application.ffmpeg import FFMPEGCommand, FFMPEGRunnerPort
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ArtifactFormat,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
    ErrorCode,
    RemuxInputArtifact,
    RemuxOutputArtifact,
    RemuxResult,
    SoundcloudDownloaderError,
)

_DEFAULT_OUTPUT_PATH = ArtifactRelativePath(value="audio/final.m4a")
_REMUX_ERROR_MESSAGE = "Unable to remux staged media."


class M4ARemuxError(SoundcloudDownloaderError):
    pass


class M4ARemuxer:
    def __init__(
        self,
        *,
        settings: AppSettings,
        storage: ArtifactStoragePort,
        workspace: TemporaryWorkspacePort,
        ffmpeg_runner: FFMPEGRunnerPort,
        output_path: ArtifactRelativePath | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._workspace = workspace
        self._ffmpeg_runner = ffmpeg_runner
        self._output_path = output_path or _DEFAULT_OUTPUT_PATH

    def remux_to_m4a(
        self,
        *,
        input_artifact: ArtifactMetadata,
    ) -> RemuxResult:
        try:
            remux_input = RemuxInputArtifact(artifact=input_artifact)
        except ValueError as exc:
            raise M4ARemuxError(ErrorCode.UNKNOWN_UNSAFE, _REMUX_ERROR_MESSAGE) from exc

        workspace_path: Path | None = None
        primary_error: BaseException | None = None
        try:
            input_bytes = self._read_input(input_artifact)
            workspace_path = self._create_workspace()
            temp_input_path = workspace_path / "input.bin"
            temp_output_path = workspace_path / "output.m4a"
            temp_input_path.write_bytes(input_bytes)
            self._ffmpeg_runner.run(self._command(temp_input_path, temp_output_path))
            output_bytes = self._read_output(temp_output_path)
            output_artifact = self._write_output(output_bytes)
            return RemuxResult(
                input_artifact=remux_input,
                output_artifact=RemuxOutputArtifact(artifact=output_artifact),
            )
        except M4ARemuxError as exc:
            primary_error = exc
            raise
        except Exception as exc:
            primary_error = exc
            raise M4ARemuxError(ErrorCode.FFMPEG_FAILED, _REMUX_ERROR_MESSAGE) from exc
        finally:
            if workspace_path is not None:
                self._cleanup_workspace(workspace_path, primary_error=primary_error)

    def _read_input(self, input_artifact: ArtifactMetadata) -> bytes:
        try:
            input_bytes = self._storage.read_bytes(relative_path=input_artifact.relative_path)
        except Exception as exc:
            raise M4ARemuxError(ErrorCode.STORAGE_FAILED, _REMUX_ERROR_MESSAGE) from exc
        if input_bytes == b"":
            raise M4ARemuxError(ErrorCode.UNKNOWN_UNSAFE, _REMUX_ERROR_MESSAGE)
        return input_bytes

    def _create_workspace(self) -> Path:
        try:
            return self._workspace.create_workspace(prefix="m4a-remux")
        except Exception as exc:
            raise M4ARemuxError(ErrorCode.STORAGE_FAILED, _REMUX_ERROR_MESSAGE) from exc

    def _command(self, input_path: Path, output_path: Path) -> FFMPEGCommand:
        return FFMPEGCommand(
            args=(
                self._settings.ffmpeg_binary,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(input_path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            )
        )

    def _read_output(self, output_path: Path) -> bytes:
        if not output_path.is_file():
            raise M4ARemuxError(ErrorCode.FFMPEG_FAILED, _REMUX_ERROR_MESSAGE)
        try:
            output_bytes = output_path.read_bytes()
        except OSError as exc:
            raise M4ARemuxError(ErrorCode.STORAGE_FAILED, _REMUX_ERROR_MESSAGE) from exc
        if output_bytes == b"":
            raise M4ARemuxError(ErrorCode.FFMPEG_FAILED, _REMUX_ERROR_MESSAGE)
        return output_bytes

    def _write_output(self, output_bytes: bytes) -> ArtifactMetadata:
        try:
            artifact = self._storage.write_bytes(relative_path=self._output_path, data=output_bytes)
        except Exception as exc:
            raise M4ARemuxError(ErrorCode.STORAGE_FAILED, _REMUX_ERROR_MESSAGE) from exc
        return artifact.model_copy(
            update={
                "kind": ArtifactKind.FINAL_AUDIO,
                "format": ArtifactFormat.M4A,
            }
        )

    def _cleanup_workspace(
        self,
        workspace_path: Path,
        *,
        primary_error: BaseException | None,
    ) -> None:
        try:
            self._workspace.cleanup_workspace(workspace_path)
        except Exception as exc:
            if primary_error is None:
                raise M4ARemuxError(ErrorCode.STORAGE_FAILED, _REMUX_ERROR_MESSAGE) from exc
