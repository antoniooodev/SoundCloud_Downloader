from pathlib import Path
from typing import Protocol, runtime_checkable

from soundcloud_downloader.domain import ArtifactMetadata, ArtifactRelativePath


@runtime_checkable
class ArtifactStoragePort(Protocol):
    def write_bytes(
        self,
        *,
        relative_path: ArtifactRelativePath,
        data: bytes,
    ) -> ArtifactMetadata:
        ...

    def read_bytes(
        self,
        *,
        relative_path: ArtifactRelativePath,
    ) -> bytes:
        ...

    def exists(
        self,
        *,
        relative_path: ArtifactRelativePath,
    ) -> bool:
        ...

    def delete(
        self,
        *,
        relative_path: ArtifactRelativePath,
    ) -> None:
        ...


@runtime_checkable
class TemporaryWorkspacePort(Protocol):
    def create_workspace(self, *, prefix: str = "work") -> Path:
        ...

    def cleanup_workspace(self, path: Path) -> None:
        ...
