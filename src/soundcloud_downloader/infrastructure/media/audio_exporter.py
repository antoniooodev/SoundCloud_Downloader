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
    AudioExportFormat,
    AudioExportMetadata,
    AudioExportRequest,
    AudioExportResult,
    ErrorCode,
    SoundcloudDownloaderError,
)

_AUDIO_EXPORT_ERROR_MESSAGE = "Unable to export audio artifact."
_OUTPUT_FORMATS = {
    AudioExportFormat.M4A: ArtifactFormat.M4A,
    AudioExportFormat.MP3: ArtifactFormat.MP3,
    AudioExportFormat.WAV: ArtifactFormat.WAV,
}


class AudioExportError(SoundcloudDownloaderError):
    pass


class AudioExporter:
    def __init__(
        self,
        *,
        settings: AppSettings,
        storage: ArtifactStoragePort,
        workspace: TemporaryWorkspacePort,
        ffmpeg_runner: FFMPEGRunnerPort,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._workspace = workspace
        self._ffmpeg_runner = ffmpeg_runner

    def export(self, request: AudioExportRequest) -> AudioExportResult:
        workspace_path: Path | None = None
        primary_error: BaseException | None = None
        try:
            input_bytes = self._read_required_artifact(request.input_artifact)
            artwork = request.artwork
            artwork_bytes = (
                None if artwork is None else self._read_required_artifact(artwork.artifact)
            )
            workspace_path = self._create_workspace()
            temp_input_path = workspace_path / "input.bin"
            temp_output_path = workspace_path / f"output.{request.output_format.value}"
            temp_input_path.write_bytes(input_bytes)

            temp_artwork_path = None
            if artwork_bytes is not None:
                if artwork is None:
                    raise AudioExportError(ErrorCode.UNKNOWN_UNSAFE, _AUDIO_EXPORT_ERROR_MESSAGE)
                artwork_extension = _artwork_extension(artwork.artifact)
                temp_artwork_path = workspace_path / f"artwork{artwork_extension}"
                temp_artwork_path.write_bytes(artwork_bytes)

            command = self._command(
                request=request,
                input_path=temp_input_path,
                output_path=temp_output_path,
                artwork_path=temp_artwork_path,
            )
            ffmpeg_result = self._ffmpeg_runner.run(command)
            if ffmpeg_result.return_code != 0:
                raise AudioExportError(ErrorCode.FFMPEG_FAILED, _AUDIO_EXPORT_ERROR_MESSAGE)
            output_bytes = self._read_output(temp_output_path)
            output_artifact = self._write_output(request, output_bytes)
            return AudioExportResult(
                input_artifact=request.input_artifact,
                output_artifact=output_artifact,
                output_format=request.output_format,
                metadata_embedded=request.metadata is not None,
                artwork_embedded=request.artwork is not None,
            )
        except AudioExportError as exc:
            primary_error = exc
            raise
        except Exception as exc:
            primary_error = exc
            raise AudioExportError(ErrorCode.FFMPEG_FAILED, _AUDIO_EXPORT_ERROR_MESSAGE) from exc
        finally:
            if workspace_path is not None:
                self._cleanup_workspace(workspace_path, primary_error=primary_error)

    def _read_required_artifact(self, artifact: ArtifactMetadata) -> bytes:
        try:
            artifact_bytes = self._storage.read_bytes(relative_path=artifact.relative_path)
        except Exception as exc:
            raise AudioExportError(ErrorCode.STORAGE_FAILED, _AUDIO_EXPORT_ERROR_MESSAGE) from exc
        if artifact_bytes == b"":
            raise AudioExportError(ErrorCode.UNKNOWN_UNSAFE, _AUDIO_EXPORT_ERROR_MESSAGE)
        return artifact_bytes

    def _create_workspace(self) -> Path:
        try:
            return self._workspace.create_workspace(prefix="audio-export")
        except Exception as exc:
            raise AudioExportError(ErrorCode.STORAGE_FAILED, _AUDIO_EXPORT_ERROR_MESSAGE) from exc

    def _command(
        self,
        *,
        request: AudioExportRequest,
        input_path: Path,
        output_path: Path,
        artwork_path: Path | None,
    ) -> FFMPEGCommand:
        if request.output_format is AudioExportFormat.MP3:
            args = self._mp3_args(request.metadata, input_path, output_path, artwork_path)
        elif request.output_format is AudioExportFormat.WAV:
            args = self._wav_args(request.metadata, input_path, output_path)
        elif request.output_format is AudioExportFormat.M4A:
            args = self._m4a_args(request.metadata, input_path, output_path, artwork_path)
        else:
            raise AudioExportError(ErrorCode.UNKNOWN_UNSAFE, _AUDIO_EXPORT_ERROR_MESSAGE)
        return FFMPEGCommand(args=args)

    def _mp3_args(
        self,
        metadata: AudioExportMetadata | None,
        input_path: Path,
        output_path: Path,
        artwork_path: Path | None,
    ) -> tuple[str, ...]:
        args = [
            self._settings.ffmpeg_binary,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
        ]
        if artwork_path is None:
            args.extend(
                ["-vn", *self._metadata_args(metadata), "-codec:a", "libmp3lame", "-b:a", "128k"]
            )
        else:
            args.extend(
                [
                    "-i",
                    str(artwork_path),
                    "-map",
                    "0:a",
                    "-map",
                    "1:v",
                    *self._metadata_args(metadata),
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "128k",
                    "-codec:v",
                    "mjpeg",
                    "-id3v2_version",
                    "3",
                ]
            )
        args.append(str(output_path))
        return tuple(args)

    def _wav_args(
        self,
        metadata: AudioExportMetadata | None,
        input_path: Path,
        output_path: Path,
    ) -> tuple[str, ...]:
        return (
            self._settings.ffmpeg_binary,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vn",
            *self._metadata_args(metadata),
            "-codec:a",
            "pcm_s16le",
            str(output_path),
        )

    def _m4a_args(
        self,
        metadata: AudioExportMetadata | None,
        input_path: Path,
        output_path: Path,
        artwork_path: Path | None,
    ) -> tuple[str, ...]:
        args = [
            self._settings.ffmpeg_binary,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
        ]
        if artwork_path is None:
            args.extend([*self._metadata_args(metadata), "-c", "copy"])
        else:
            args.extend(
                [
                    "-i",
                    str(artwork_path),
                    "-map",
                    "0:a",
                    "-map",
                    "1:v",
                    *self._metadata_args(metadata),
                    "-c:a",
                    "copy",
                    "-c:v",
                    "copy",
                    "-disposition:v",
                    "attached_pic",
                ]
            )
        args.extend(["-movflags", "+faststart", str(output_path)])
        return tuple(args)

    def _metadata_args(self, metadata: AudioExportMetadata | None) -> tuple[str, ...]:
        if metadata is None:
            return ()
        args: list[str] = []
        for field_name in ("title", "artist", "album"):
            value = getattr(metadata, field_name)
            if value is not None:
                args.extend(["-metadata", f"{field_name}={value}"])
        return tuple(args)

    def _read_output(self, output_path: Path) -> bytes:
        if not output_path.is_file():
            raise AudioExportError(ErrorCode.FFMPEG_FAILED, _AUDIO_EXPORT_ERROR_MESSAGE)
        try:
            output_bytes = output_path.read_bytes()
        except OSError as exc:
            raise AudioExportError(ErrorCode.STORAGE_FAILED, _AUDIO_EXPORT_ERROR_MESSAGE) from exc
        if output_bytes == b"":
            raise AudioExportError(ErrorCode.FFMPEG_FAILED, _AUDIO_EXPORT_ERROR_MESSAGE)
        return output_bytes

    def _write_output(
        self,
        request: AudioExportRequest,
        output_bytes: bytes,
    ) -> ArtifactMetadata:
        try:
            artifact = self._storage.write_bytes(
                relative_path=request.output_path, data=output_bytes
            )
        except Exception as exc:
            raise AudioExportError(ErrorCode.STORAGE_FAILED, _AUDIO_EXPORT_ERROR_MESSAGE) from exc
        return artifact.model_copy(
            update={
                "kind": ArtifactKind.FINAL_AUDIO,
                "format": _OUTPUT_FORMATS[request.output_format],
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
                raise AudioExportError(
                    ErrorCode.STORAGE_FAILED, _AUDIO_EXPORT_ERROR_MESSAGE
                ) from exc


def _artwork_extension(artifact: ArtifactMetadata) -> str:
    if artifact.format is ArtifactFormat.PNG:
        return ".png"
    return ".jpg"
