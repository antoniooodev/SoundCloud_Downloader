import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

import soundcloud_downloader.cli.doctor as doctor_cli
from soundcloud_downloader.cli.main import app


CLIENT_ID = "raw-client-id-should-not-leak"
CLIENT_SECRET = "raw-client-secret-should-not-leak"
SAFE_MISSING_REQUIRED_NAMES = {
    "allow_network",
    "allow_filesystem_writes",
    "soundcloud_client_id",
    "soundcloud_client_secret",
    "oauth_token_encryption_key",
    "oauth_session_encryption_key",
    "ffmpeg",
}


def write_env_file(
    tmp_path: Path,
    *,
    token_key: str | None,
    session_key: str | None,
    client_id: str | None = CLIENT_ID,
    client_secret: str | None = CLIENT_SECRET,
    allow_network: bool = True,
    allow_filesystem_writes: bool = True,
    artifact_storage_root: Path | None = None,
    artifact_temp_root: Path | None = None,
    ffmpeg_binary: str = "ffmpeg",
    ffmpeg_timeout_seconds: int = 300,
) -> Path:
    lines = [
        f"SCD_ALLOW_NETWORK={str(allow_network).lower()}",
        f"SCD_ALLOW_FILESYSTEM_WRITES={str(allow_filesystem_writes).lower()}",
        f"SCD_FFMPEG_BINARY={ffmpeg_binary}",
        f"SCD_FFMPEG_TIMEOUT_SECONDS={ffmpeg_timeout_seconds}",
    ]
    if token_key is not None:
        lines.append(f"SCD_OAUTH_TOKEN_ENCRYPTION_KEY={token_key}")
    if session_key is not None:
        lines.append(f"SCD_OAUTH_SESSION_ENCRYPTION_KEY={session_key}")
    if client_id is not None:
        lines.append(f"SCD_SOUNDCLOUD_CLIENT_ID={client_id}")
    if client_secret is not None:
        lines.append(f"SCD_SOUNDCLOUD_CLIENT_SECRET={client_secret}")
    if artifact_storage_root is not None:
        lines.append(f"SCD_ARTIFACT_STORAGE_ROOT={artifact_storage_root}")
    if artifact_temp_root is not None:
        lines.append(f"SCD_ARTIFACT_TEMP_ROOT={artifact_temp_root}")
    env_file = tmp_path / "settings.env"
    env_file.write_text("\n".join(lines), encoding="utf-8")
    return env_file


def base_env_file(tmp_path: Path, **overrides: object) -> Path:
    token_key = Fernet.generate_key().decode()
    session_key = Fernet.generate_key().decode()
    kwargs: dict[str, object] = {
        "token_key": token_key,
        "session_key": session_key,
    }
    kwargs.update(overrides)
    return write_env_file(tmp_path, **kwargs)  # type: ignore[arg-type]


def patch_ffmpeg_found(monkeypatch: pytest.MonkeyPatch, found: bool) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/ffmpeg" if found else None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(doctor_cli.shutil, "which", fake_which)


def invoke_doctor(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["doctor", *args])
    return result.exit_code, result.output


def test_doctor_command_exists() -> None:
    result = CliRunner().invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output.lower() or "configuration" in result.output.lower()


def test_doctor_returns_json_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path)

    exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0, output
    assert payload["status"] == "ok"
    assert payload["download_ready"] is True
    assert payload["missing_required"] == []


def test_doctor_supports_plain_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path)

    exit_code, output = invoke_doctor("--env-file", str(env_file), "--plain")

    assert exit_code == 0, output
    assert "status=ok" in output
    assert "download_ready=true" in output
    assert "missing_required=" in output
    assert "allow_network=true" in output


def test_doctor_reports_status_ok_when_required_settings_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path)

    exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["download_ready"] is True
    assert payload["missing_required"] == []
    assert payload["errors"] == []


@pytest.mark.parametrize(
    "missing,kwargs",
    [
        ("soundcloud_client_id", {"client_id": None}),
        ("soundcloud_client_secret", {"client_secret": None}),
        ("oauth_token_encryption_key", {"token_key": None}),
        ("oauth_session_encryption_key", {"session_key": None}),
    ],
)
def test_doctor_reports_warning_when_download_requirement_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing: str,
    kwargs: dict[str, object],
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path, **kwargs)

    exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["status"] == "warning"
    assert payload["download_ready"] is False
    assert missing in payload["missing_required"]
    assert payload["errors"] == []


def test_doctor_json_output_includes_download_ready_and_missing_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path)

    exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert "download_ready" in payload
    assert "missing_required" in payload


def test_doctor_reports_allow_network_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path, allow_network=True)

    _exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert payload["checks"]["allow_network"] is True


def test_doctor_reports_allow_filesystem_writes_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path, allow_filesystem_writes=True)

    _exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert payload["checks"]["allow_filesystem_writes"] is True


def test_doctor_warns_when_allow_network_is_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path, allow_network=False)

    exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["status"] == "warning"
    assert payload["download_ready"] is False
    assert "allow_network" in payload["missing_required"]
    assert any("allow_network" in warning for warning in payload["warnings"])


def test_doctor_warns_when_allow_filesystem_writes_is_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path, allow_filesystem_writes=False)

    exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["status"] == "warning"
    assert payload["download_ready"] is False
    assert "allow_filesystem_writes" in payload["missing_required"]
    assert any("allow_filesystem_writes" in warning for warning in payload["warnings"])


def test_doctor_reports_artifact_storage_root_safely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    storage_root = tmp_path / "artifacts"
    env_file = base_env_file(tmp_path, artifact_storage_root=storage_root)

    _exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert payload["checks"]["artifact_storage_root"] == str(storage_root)


def test_doctor_reports_artifact_temp_root_safely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    temp_root = tmp_path / "tmp"
    env_file = base_env_file(tmp_path, artifact_temp_root=temp_root)

    _exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert payload["checks"]["artifact_temp_root"] == str(temp_root)


def test_doctor_reports_ffmpeg_binary_safely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path, ffmpeg_binary="custom-ffmpeg")

    _exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert payload["checks"]["ffmpeg_binary"] == "custom-ffmpeg"


def test_doctor_reports_ffmpeg_found_true_when_shutil_which_returns_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path)

    _exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert payload["checks"]["ffmpeg_found"] is True


def test_doctor_reports_ffmpeg_found_false_when_shutil_which_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, False)
    env_file = base_env_file(tmp_path)

    exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["status"] == "warning"
    assert payload["download_ready"] is False
    assert payload["checks"]["ffmpeg_found"] is False
    assert "ffmpeg" in payload["missing_required"]


def test_doctor_no_check_ffmpeg_skips_shutil_which(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = {"count": 0}

    def fake_which(name: str) -> str | None:
        calls["count"] += 1
        return None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(doctor_cli.shutil, "which", fake_which)
    env_file = base_env_file(tmp_path)

    exit_code, output = invoke_doctor("--env-file", str(env_file), "--no-check-ffmpeg")
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["download_ready"] is True
    assert payload["missing_required"] == []
    assert calls["count"] == 0
    assert "ffmpeg_found" not in payload["checks"]


def test_missing_required_contains_only_symbolic_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, False)
    token_key = Fernet.generate_key().decode()
    session_key = Fernet.generate_key().decode()
    env_file = write_env_file(
        tmp_path,
        token_key=token_key,
        session_key=session_key,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        allow_network=False,
        allow_filesystem_writes=False,
    )

    _exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert set(payload["missing_required"]) <= SAFE_MISSING_REQUIRED_NAMES
    assert CLIENT_ID not in payload["missing_required"]
    assert CLIENT_SECRET not in payload["missing_required"]
    assert token_key not in payload["missing_required"]
    assert session_key not in payload["missing_required"]
    assert CLIENT_ID not in output
    assert CLIENT_SECRET not in output
    assert token_key not in output
    assert session_key not in output


def test_doctor_does_not_create_artifact_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    storage_root = tmp_path / "artifacts_not_created"
    env_file = base_env_file(tmp_path, artifact_storage_root=storage_root)

    exit_code, _output = invoke_doctor("--env-file", str(env_file))

    assert exit_code == 0
    assert not storage_root.exists()


def test_doctor_does_not_create_temp_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    temp_root = tmp_path / "tmp_not_created"
    env_file = base_env_file(tmp_path, artifact_temp_root=temp_root)

    exit_code, _output = invoke_doctor("--env-file", str(env_file))

    assert exit_code == 0
    assert not temp_root.exists()


def test_doctor_json_output_does_not_contain_client_secret_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path)

    _exit_code, output = invoke_doctor("--env-file", str(env_file))

    assert CLIENT_SECRET not in output


def test_doctor_json_output_does_not_contain_token_encryption_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    token_key = Fernet.generate_key().decode()
    session_key = Fernet.generate_key().decode()
    env_file = write_env_file(tmp_path, token_key=token_key, session_key=session_key)

    _exit_code, output = invoke_doctor("--env-file", str(env_file))

    assert token_key not in output
    assert session_key not in output


def test_doctor_plain_output_does_not_contain_client_secret_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path)

    _exit_code, output = invoke_doctor("--env-file", str(env_file), "--plain")

    assert CLIENT_SECRET not in output


def test_doctor_plain_output_does_not_contain_token_encryption_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    token_key = Fernet.generate_key().decode()
    session_key = Fernet.generate_key().decode()
    env_file = write_env_file(tmp_path, token_key=token_key, session_key=session_key)

    _exit_code, output = invoke_doctor("--env-file", str(env_file), "--plain")

    assert token_key not in output
    assert session_key not in output


def test_doctor_warning_output_is_safe_when_requirement_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    token_key = Fernet.generate_key().decode()
    env_file = write_env_file(
        tmp_path,
        token_key=token_key,
        session_key=None,
    )

    exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["status"] == "warning"
    assert payload["download_ready"] is False
    assert token_key not in output
    assert CLIENT_SECRET not in output


def test_doctor_settings_error_output_is_safe(tmp_path: Path) -> None:
    env_file = tmp_path / "settings.env"
    secret_value = "invalid-secret-value-should-not-leak"
    env_file.write_text(
        "\n".join(
            [
                f"SCD_SOUNDCLOUD_CLIENT_SECRET={CLIENT_SECRET}",
                f"SCD_OAUTH_TOKEN_ENCRYPTION_KEY={secret_value}",
            ]
        ),
        encoding="utf-8",
    )

    exit_code, output = invoke_doctor("--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code != 0
    assert payload["status"] == "error"
    assert payload["download_ready"] is False
    assert payload["errors"] == ["settings could not be loaded."]
    assert CLIENT_SECRET not in output
    assert secret_value not in output


def test_no_real_network_calls_occur(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)

    def fail(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail)
    env_file = base_env_file(tmp_path)

    exit_code, _output = invoke_doctor("--env-file", str(env_file))

    assert exit_code == 0


def test_no_real_ffmpeg_execution_occurs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)

    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real subprocess calls are not allowed")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(subprocess, "Popen", fail)
    env_file = base_env_file(tmp_path)

    exit_code, _output = invoke_doctor("--env-file", str(env_file))

    assert exit_code == 0


def test_tests_write_only_inside_tmp_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_ffmpeg_found(monkeypatch, True)
    env_file = base_env_file(tmp_path)
    assert env_file.is_relative_to(tmp_path)
