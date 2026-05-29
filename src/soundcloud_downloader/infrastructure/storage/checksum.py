import hashlib
from pathlib import Path

from soundcloud_downloader.domain import ArtifactChecksum, ChecksumAlgorithm

_CHUNK_SIZE = 1024 * 1024


def compute_sha256_bytes(data: bytes) -> ArtifactChecksum:
    return ArtifactChecksum(
        algorithm=ChecksumAlgorithm.SHA256,
        value=hashlib.sha256(data).hexdigest(),
    )


def compute_sha256_file(path: Path) -> ArtifactChecksum:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return ArtifactChecksum(
        algorithm=ChecksumAlgorithm.SHA256,
        value=digest.hexdigest(),
    )
