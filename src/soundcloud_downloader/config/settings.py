from enum import Enum
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeEnvironment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCD_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO
    data_dir: Path = Path("data")
    temp_dir: Path = Path("data/tmp")
    library_dir: Path = Path("data/library")
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0)
    http_backoff_base_seconds: float = Field(default=0.5, ge=0)
    max_parallel_tracks_per_user: int = Field(default=2, gt=0)
    max_parallel_segments_per_track: int = Field(default=8, gt=0)
    enable_public_mode: bool = True
    enable_go_plus_mode: bool = True
    allow_network: bool = False
    allow_filesystem_writes: bool = False
    soundcloud_resolve_endpoint: str | None = None

    @field_validator("soundcloud_resolve_endpoint")
    @classmethod
    def validate_soundcloud_resolve_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("SoundCloud resolve endpoint must not be empty.")
        parsed = urlsplit(stripped)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("SoundCloud resolve endpoint must be a URL.")
        if parsed.query or parsed.fragment:
            raise ValueError("SoundCloud resolve endpoint must not contain query or fragment.")
        if parsed.username or parsed.password:
            raise ValueError("SoundCloud resolve endpoint must not contain credentials.")
        return stripped

    @model_validator(mode="after")
    def validate_production_log_level(self) -> Self:
        if (
            self.environment is RuntimeEnvironment.PRODUCTION
            and self.log_level is LogLevel.DEBUG
        ):
            raise ValueError("Production settings must not use debug log level.")
        return self


def load_settings(env_file: str | Path | None = None) -> AppSettings:
    if env_file is None:
        return AppSettings()

    class EnvFileAppSettings(AppSettings):
        model_config = SettingsConfigDict(
            env_prefix="SCD_",
            case_sensitive=False,
            extra="ignore",
            env_file=env_file,
        )

    return EnvFileAppSettings()
