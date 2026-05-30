import subprocess

from soundcloud_downloader.application.ffmpeg import (
    FFMPEGCommand,
    FFMPEGResult,
    sanitize_ffmpeg_output,
)
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import ErrorCode, SoundcloudDownloaderError

_FFMPEG_ERROR_MESSAGE = "ffmpeg execution failed."


class FFMPEGExecutionError(SoundcloudDownloaderError):
    pass


class SubprocessFFMPEGRunner:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def run(self, command: FFMPEGCommand) -> FFMPEGResult:
        try:
            completed = subprocess.run(
                command.args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self._settings.ffmpeg_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FFMPEGExecutionError(ErrorCode.FFMPEG_FAILED, _FFMPEG_ERROR_MESSAGE) from exc
        except OSError as exc:
            raise FFMPEGExecutionError(ErrorCode.FFMPEG_FAILED, _FFMPEG_ERROR_MESSAGE) from exc

        result = FFMPEGResult(
            return_code=completed.returncode,
            stdout=sanitize_ffmpeg_output(completed.stdout or ""),
            stderr=sanitize_ffmpeg_output(completed.stderr or ""),
        )
        if result.return_code != 0:
            raise FFMPEGExecutionError(ErrorCode.FFMPEG_FAILED, _FFMPEG_ERROR_MESSAGE)
        return result
