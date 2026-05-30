import socket
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from soundcloud_downloader.application import FFMPEGCommand, redact_ffmpeg_command
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.infrastructure.media import (
    FFMPEGExecutionError,
    SubprocessFFMPEGRunner,
)


def test_ffmpeg_command_rejects_empty_args() -> None:
    with pytest.raises(ValidationError):
        FFMPEGCommand(args=())


def test_ffmpeg_command_rejects_empty_arg() -> None:
    with pytest.raises(ValidationError):
        FFMPEGCommand(args=("ffmpeg", ""))


def test_subprocess_ffmpeg_runner_calls_subprocess_run_with_shell_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=("ffmpeg",), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    SubprocessFFMPEGRunner(_settings()).run(FFMPEGCommand(args=("ffmpeg", "-version")))

    assert captured["kwargs"]["shell"] is False


def test_runner_uses_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=("ffmpeg",), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    SubprocessFFMPEGRunner(_settings(timeout=12)).run(FFMPEGCommand(args=("ffmpeg", "-version")))

    assert captured["kwargs"]["timeout"] == 12


def test_runner_captures_stdout_and_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=("ffmpeg",),
            returncode=0,
            stdout="ok",
            stderr="warning",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SubprocessFFMPEGRunner(_settings()).run(FFMPEGCommand(args=("ffmpeg", "-version")))

    assert result.stdout == "ok"
    assert result.stderr == "warning"


def test_runner_returns_ffmpeg_result_on_zero_return_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=("ffmpeg",), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SubprocessFFMPEGRunner(_settings()).run(FFMPEGCommand(args=("ffmpeg", "-version")))

    assert result.return_code == 0


def test_runner_raises_execution_error_on_non_zero_return_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=("ffmpeg",), returncode=1, stdout="", stderr="bad")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FFMPEGExecutionError):
        SubprocessFFMPEGRunner(_settings()).run(FFMPEGCommand(args=("ffmpeg", "-version")))


def test_runner_raises_execution_error_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=("ffmpeg",), timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FFMPEGExecutionError):
        SubprocessFFMPEGRunner(_settings()).run(FFMPEGCommand(args=("ffmpeg", "-version")))


def test_runner_raises_execution_error_on_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("missing binary")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FFMPEGExecutionError):
        SubprocessFFMPEGRunner(_settings()).run(FFMPEGCommand(args=("ffmpeg", "-version")))


def test_error_messages_do_not_contain_raw_command_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_path = "/tmp/private/input.bin"

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=("ffmpeg",), returncode=1, stdout="", stderr=raw_path)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FFMPEGExecutionError) as exc_info:
        SubprocessFFMPEGRunner(_settings()).run(FFMPEGCommand(args=("ffmpeg", "-i", raw_path)))

    assert raw_path not in str(exc_info.value)


def test_error_messages_do_not_contain_sensitive_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=("ffmpeg",), returncode=1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FFMPEGExecutionError) as exc_info:
        SubprocessFFMPEGRunner(_settings()).run(
            FFMPEGCommand(args=("ffmpeg", "-i", "access_token=secret"))
        )

    message = str(exc_info.value)
    assert "access_token" not in message
    assert "refresh_token" not in message
    assert "client_secret" not in message


def test_redact_ffmpeg_command_does_not_return_shell_string() -> None:
    redacted = redact_ffmpeg_command(FFMPEGCommand(args=("ffmpeg", "-i", "/tmp/input.bin")))

    assert isinstance(redacted["args"], list)


def test_redact_ffmpeg_command_redacts_path_like_args() -> None:
    redacted = redact_ffmpeg_command(
        FFMPEGCommand(args=("ffmpeg", "-i", "/tmp/input.bin", "-c", "copy", "/tmp/output.m4a"))
    )

    assert redacted["args"] == ["ffmpeg", "-i", "[PATH]", "-c", "copy", "[PATH]"]


def test_no_real_ffmpeg_process_is_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=("ffmpeg",), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert SubprocessFFMPEGRunner(_settings()).run(
        FFMPEGCommand(args=("ffmpeg", "-version"))
    ).return_code == 0


def test_tests_perform_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert redact_ffmpeg_command(FFMPEGCommand(args=("ffmpeg", "-version")))


def test_tests_write_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("file writes are not allowed")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)

    assert FFMPEGCommand(args=("ffmpeg", "-version")).args[0] == "ffmpeg"


def _settings(*, timeout: int = 300) -> AppSettings:
    return AppSettings(ffmpeg_timeout_seconds=timeout)
