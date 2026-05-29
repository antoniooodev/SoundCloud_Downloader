import re
import shutil
import tempfile
from pathlib import Path

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import ErrorCode, SoundcloudDownloaderError

_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class TemporaryWorkspaceError(SoundcloudDownloaderError):
    pass


class LocalTemporaryWorkspace:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._root = settings.artifact_temp_root

    def create_workspace(self, *, prefix: str = "work") -> Path:
        self._require_filesystem_writes()
        self._validate_prefix(prefix)
        root = self._root.resolve(strict=False)
        try:
            root.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=root))
        except OSError as exc:
            raise TemporaryWorkspaceError(
                ErrorCode.STORAGE_FAILED,
                "Temporary workspace could not be created.",
            ) from exc

    def cleanup_workspace(self, path: Path) -> None:
        self._require_filesystem_writes()
        root = self._root.resolve(strict=False)
        candidate = path.resolve(strict=False)
        if candidate == root or not candidate.is_relative_to(root):
            raise TemporaryWorkspaceError(
                ErrorCode.UNKNOWN_UNSAFE,
                "Temporary workspace path is outside the configured root.",
            )
        if not candidate.exists():
            return
        if not candidate.is_dir():
            raise TemporaryWorkspaceError(
                ErrorCode.STORAGE_FAILED,
                "Temporary workspace path is not a directory.",
            )
        try:
            shutil.rmtree(candidate)
        except OSError as exc:
            raise TemporaryWorkspaceError(
                ErrorCode.STORAGE_FAILED,
                "Temporary workspace could not be cleaned up.",
            ) from exc

    def _require_filesystem_writes(self) -> None:
        if not self._settings.allow_filesystem_writes:
            raise TemporaryWorkspaceError(
                ErrorCode.STORAGE_FAILED,
                "Filesystem writes are disabled by application settings.",
            )

    def _validate_prefix(self, prefix: str) -> None:
        if prefix == "" or not _PREFIX_PATTERN.fullmatch(prefix):
            raise TemporaryWorkspaceError(
                ErrorCode.UNKNOWN_UNSAFE,
                "Temporary workspace prefix is unsafe.",
            )
