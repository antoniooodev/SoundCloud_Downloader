import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_PATH_MARKERS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "cookie",
        "credentials",
        "manifest_url",
        "refresh_token",
        "segment_url",
        "set-cookie",
        "stream_url",
    }
)


class ArtifactKind(str, Enum):
    HLS_SEGMENT = "hls_segment"
    STAGED_MEDIA = "staged_media"
    FINAL_AUDIO = "final_audio"
    ARTWORK = "artwork"
    METADATA = "metadata"
    TEMPORARY = "temporary"


class ArtifactFormat(str, Enum):
    AAC = "aac"
    M4A = "m4a"
    MP3 = "mp3"
    WAV = "wav"
    JSON = "json"
    JPG = "jpg"
    PNG = "png"
    BIN = "bin"
    UNKNOWN = "unknown"


class ChecksumAlgorithm(str, Enum):
    SHA256 = "sha256"


class ArtifactId(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        if value == "":
            raise ValueError("Artifact ID must not be empty.")
        if "/" in value or "\\" in value:
            raise ValueError("Artifact ID must not contain path separators.")
        if ".." in value:
            raise ValueError("Artifact ID must not contain path traversal markers.")
        if not _ARTIFACT_ID_PATTERN.fullmatch(value):
            raise ValueError("Artifact ID contains unsupported characters.")
        _reject_sensitive_marker(value, field_name="Artifact ID")
        return value


class ArtifactRelativePath(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        if value == "":
            raise ValueError("Artifact relative path must not be empty.")
        if "\\" in value:
            raise ValueError("Artifact relative path must use POSIX separators.")
        if value.startswith("~"):
            raise ValueError("Artifact relative path must not use home expansion.")
        if urlsplit(value).scheme:
            raise ValueError("Artifact relative path must not include a URL-like scheme.")

        path = PurePosixPath(value)
        if path.is_absolute():
            raise ValueError("Artifact relative path must not be absolute.")
        if str(path) != value:
            raise ValueError("Artifact relative path must be normalized POSIX text.")
        parts = path.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Artifact relative path contains unsafe path components.")
        if "//" in value or value.endswith("/"):
            raise ValueError("Artifact relative path contains empty path components.")
        _reject_sensitive_marker(value, field_name="Artifact relative path")
        return value


class ArtifactChecksum(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: ChecksumAlgorithm = ChecksumAlgorithm.SHA256
    value: str

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("SHA-256 checksum must be 64 lowercase hexadecimal characters.")
        return value


class ArtifactMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: ArtifactId
    kind: ArtifactKind
    format: ArtifactFormat
    relative_path: ArtifactRelativePath
    size_bytes: int | None = Field(default=None, ge=0)
    checksum: ArtifactChecksum | None = None
    created_at: datetime | None = None

    @model_validator(mode="after")
    def validate_created_at(self) -> Self:
        if self.created_at is None:
            return self
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("Artifact creation timestamp must be timezone-aware UTC.")
        if self.created_at.utcoffset() != timezone.utc.utcoffset(self.created_at):
            raise ValueError("Artifact creation timestamp must be timezone-aware UTC.")
        return self


def _reject_sensitive_marker(value: str, *, field_name: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in _SENSITIVE_PATH_MARKERS):
        raise ValueError(f"{field_name} must not contain sensitive marker names.")
