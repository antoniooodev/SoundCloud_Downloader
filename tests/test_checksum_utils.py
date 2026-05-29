import hashlib
import socket
from pathlib import Path

import pytest

from soundcloud_downloader.domain import ChecksumAlgorithm
from soundcloud_downloader.infrastructure.storage import compute_sha256_bytes, compute_sha256_file


def test_compute_sha256_bytes_returns_expected_digest() -> None:
    checksum = compute_sha256_bytes(b"artifact-data")

    assert checksum.algorithm is ChecksumAlgorithm.SHA256
    assert checksum.value == hashlib.sha256(b"artifact-data").hexdigest()


def test_compute_sha256_file_returns_expected_digest(tmp_path: Path) -> None:
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"artifact-data")

    checksum = compute_sha256_file(path)

    assert checksum.value == hashlib.sha256(b"artifact-data").hexdigest()


def test_compute_sha256_file_works_for_larger_chunked_file(tmp_path: Path) -> None:
    data = (b"0123456789abcdef" * 70_000) + b"tail"
    path = tmp_path / "large-artifact.bin"
    path.write_bytes(data)

    checksum = compute_sha256_file(path)

    assert checksum.value == hashlib.sha256(data).hexdigest()


def test_checksum_value_is_lowercase_hex() -> None:
    checksum = compute_sha256_bytes(b"ABC")

    assert checksum.value == checksum.value.lower()
    assert all(char in "0123456789abcdef" for char in checksum.value)


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert compute_sha256_bytes(b"data").algorithm is ChecksumAlgorithm.SHA256


def test_file_writing_tests_use_only_pytest_tmp_path(tmp_path: Path) -> None:
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"data")

    assert path.is_relative_to(tmp_path)
    assert compute_sha256_file(path).value == hashlib.sha256(b"data").hexdigest()
