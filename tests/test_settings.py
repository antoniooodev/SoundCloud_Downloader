from pathlib import Path

import pytest
from pydantic import ValidationError

from soundcloud_downloader.config import (
    AppSettings,
    LogLevel,
    RuntimeEnvironment,
    load_settings,
)


def test_app_settings_defaults_are_safe() -> None:
    settings = AppSettings()

    assert settings.allow_network is False
    assert settings.allow_filesystem_writes is False
    assert settings.enable_public_mode is True
    assert settings.enable_go_plus_mode is True
    assert settings.environment is RuntimeEnvironment.DEVELOPMENT


def test_environment_variables_with_prefix_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCD_ENVIRONMENT", "test")
    monkeypatch.setenv("SCD_LOG_LEVEL", "warning")
    monkeypatch.setenv("SCD_HTTP_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("SCD_ENABLE_PUBLIC_MODE", "false")
    monkeypatch.setenv("SCD_ALLOW_NETWORK", "true")

    settings = load_settings()

    assert settings.environment is RuntimeEnvironment.TEST
    assert settings.log_level is LogLevel.WARNING
    assert settings.http_timeout_seconds == 12.5
    assert settings.enable_public_mode is False
    assert settings.allow_network is True


def test_load_settings_reads_explicit_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "\n".join(
            [
                "SCD_ENVIRONMENT=test",
                "SCD_LOG_LEVEL=error",
                "SCD_DATA_DIR=custom-data",
                "SCD_HTTP_MAX_RETRIES=5",
                "SCD_ALLOW_FILESYSTEM_WRITES=true",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(env_file)

    assert settings.environment is RuntimeEnvironment.TEST
    assert settings.log_level is LogLevel.ERROR
    assert settings.data_dir == Path("custom-data")
    assert settings.http_max_retries == 5
    assert settings.allow_filesystem_writes is True


@pytest.mark.parametrize("timeout", [0, -1])
def test_invalid_http_timeout_seconds_fails_validation(timeout: float) -> None:
    with pytest.raises(ValidationError):
        AppSettings(http_timeout_seconds=timeout)


def test_invalid_http_max_retries_fails_validation() -> None:
    with pytest.raises(ValidationError):
        AppSettings(http_max_retries=-1)


def test_invalid_max_parallel_tracks_per_user_fails_validation() -> None:
    with pytest.raises(ValidationError):
        AppSettings(max_parallel_tracks_per_user=0)


def test_invalid_max_parallel_segments_per_track_fails_validation() -> None:
    with pytest.raises(ValidationError):
        AppSettings(max_parallel_segments_per_track=0)


def test_production_environment_rejects_debug_log_level() -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            environment=RuntimeEnvironment.PRODUCTION,
            log_level=LogLevel.DEBUG,
        )


def test_paths_are_parsed_as_path_objects() -> None:
    settings = AppSettings(
        data_dir="custom-data",
        temp_dir="/tmp/soundcloud-downloader",
        library_dir="custom-library",
    )

    assert settings.data_dir == Path("custom-data")
    assert settings.temp_dir == Path("/tmp/soundcloud-downloader")
    assert settings.library_dir == Path("custom-library")


def test_loading_settings_does_not_create_directories(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    temp_dir = data_dir / "tmp"
    library_dir = data_dir / "library"

    settings = AppSettings(
        data_dir=data_dir,
        temp_dir=temp_dir,
        library_dir=library_dir,
    )

    assert settings.data_dir == data_dir
    assert data_dir.exists() is False
    assert temp_dir.exists() is False
    assert library_dir.exists() is False
