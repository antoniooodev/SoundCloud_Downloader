import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ArtifactFormat,
    ArtifactId,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
    ErrorCode,
    SoundcloudDownloaderError,
)
from soundcloud_downloader.infrastructure.storage.checksum import compute_sha256_bytes

_FORMAT_BY_EXTENSION = {
    ".aac": ArtifactFormat.AAC,
    ".bin": ArtifactFormat.BIN,
    ".jpeg": ArtifactFormat.JPG,
    ".jpg": ArtifactFormat.JPG,
    ".json": ArtifactFormat.JSON,
    ".m4a": ArtifactFormat.M4A,
    ".mp3": ArtifactFormat.MP3,
    ".png": ArtifactFormat.PNG,
    ".wav": ArtifactFormat.WAV,
}


class LocalArtifactStorageError(SoundcloudDownloaderError):
    pass


class LocalArtifactStorage:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._root = settings.artifact_storage_root

    def write_bytes(
        self,
        *,
        relative_path: ArtifactRelativePath,
        data: bytes,
    ) -> ArtifactMetadata:
        self._require_filesystem_writes()
        path = self._resolve_path(relative_path)
        temp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
            temp_path.write_bytes(data)
            os.replace(temp_path, path)
        except OSError as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise LocalArtifactStorageError(
                ErrorCode.STORAGE_FAILED,
                "Artifact could not be written to local storage.",
            ) from exc

        return ArtifactMetadata(
            artifact_id=self._artifact_id_for_path(relative_path),
            kind=ArtifactKind.TEMPORARY,
            format=self._format_for_path(path),
            relative_path=relative_path,
            size_bytes=len(data),
            checksum=compute_sha256_bytes(data),
            created_at=datetime.now(timezone.utc),
        )

    def read_bytes(
        self,
        *,
        relative_path: ArtifactRelativePath,
    ) -> bytes:
        path = self._resolve_path(relative_path)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise LocalArtifactStorageError(
                ErrorCode.STORAGE_FAILED,
                "Artifact could not be read from local storage.",
            ) from exc

    def exists(
        self,
        *,
        relative_path: ArtifactRelativePath,
    ) -> bool:
        path = self._resolve_path(relative_path)
        return path.is_file()

    def delete(
        self,
        *,
        relative_path: ArtifactRelativePath,
    ) -> None:
        self._require_filesystem_writes()
        path = self._resolve_path(relative_path)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise LocalArtifactStorageError(
                ErrorCode.STORAGE_FAILED,
                "Artifact could not be deleted from local storage.",
            ) from exc

    def _require_filesystem_writes(self) -> None:
        if not self._settings.allow_filesystem_writes:
            raise LocalArtifactStorageError(
                ErrorCode.STORAGE_FAILED,
                "Filesystem writes are disabled by application settings.",
            )

    def _resolve_path(self, relative_path: ArtifactRelativePath) -> Path:
        try:
            validated_path = ArtifactRelativePath(value=relative_path.value)
        except ValidationError as exc:
            raise LocalArtifactStorageError(
                ErrorCode.UNKNOWN_UNSAFE,
                "Artifact path is unsafe.",
            ) from exc

        root = self._root.resolve(strict=False)
        path = root.joinpath(*PurePosixPath(validated_path.value).parts).resolve(strict=False)
        if not path.is_relative_to(root) or path == root:
            raise LocalArtifactStorageError(
                ErrorCode.UNKNOWN_UNSAFE,
                "Artifact path is outside local storage.",
            )
        return path

    def _artifact_id_for_path(self, relative_path: ArtifactRelativePath) -> ArtifactId:
        return ArtifactId(value=uuid.uuid5(uuid.NAMESPACE_URL, relative_path.value).hex)

    def _format_for_path(self, path: Path) -> ArtifactFormat:
        return _FORMAT_BY_EXTENSION.get(path.suffix.lower(), ArtifactFormat.UNKNOWN)
