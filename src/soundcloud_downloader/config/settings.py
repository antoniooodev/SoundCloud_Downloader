from enum import Enum
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
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
    artifact_storage_root: Path = Path("data/artifacts")
    artifact_temp_root: Path = Path("data/tmp")
    oauth_session_store_path: Path = Path("data/oauth_sessions.enc")
    oauth_session_encryption_key: SecretStr | None = None
    oauth_token_store_path: Path = Path("data/oauth_tokens.enc")
    oauth_token_encryption_key: SecretStr | None = None
    soundcloud_client_id: SecretStr | None = None
    soundcloud_client_secret: SecretStr | None = None
    soundcloud_resolve_endpoint: str | None = None
    soundcloud_api_base_url: str = "https://api.soundcloud.com"
    soundcloud_auth_base_url: str = "https://secure.soundcloud.com"

    @field_validator("artifact_storage_root", "artifact_temp_root", mode="before")
    @classmethod
    def validate_artifact_root_path(cls, value: object) -> object:
        if value == "":
            raise ValueError("Artifact root path must not be empty.")
        return value

    @field_validator("artifact_storage_root", "artifact_temp_root")
    @classmethod
    def validate_artifact_root_path_type(cls, value: Path) -> Path:
        if not isinstance(value, Path):
            raise ValueError("Artifact root path must be a filesystem path.")
        if str(value) == "":
            raise ValueError("Artifact root path must not be empty.")
        return value

    @field_validator("oauth_session_store_path")
    @classmethod
    def validate_oauth_session_store_path(cls, value: Path) -> Path:
        if not isinstance(value, Path):
            raise ValueError("OAuth session store path must be a filesystem path.")
        return value

    @field_validator("oauth_token_store_path")
    @classmethod
    def validate_oauth_token_store_path(cls, value: Path) -> Path:
        if not isinstance(value, Path):
            raise ValueError("OAuth token store path must be a filesystem path.")
        return value

    @field_validator("oauth_session_encryption_key", "oauth_token_encryption_key")
    @classmethod
    def validate_fernet_key(
        cls,
        value: SecretStr | None,
    ) -> SecretStr | None:
        if value is None:
            return None
        try:
            Fernet(value.get_secret_value().encode("ascii"))
        except (TypeError, ValueError):
            raise ValueError("OAuth encryption key must be a valid Fernet key.") from None
        return value

    @field_validator("soundcloud_client_id", "soundcloud_client_secret")
    @classmethod
    def validate_soundcloud_client_credential(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        if value.get_secret_value() == "":
            raise ValueError("SoundCloud client credentials must not be empty when provided.")
        return value

    @field_validator("soundcloud_api_base_url", "soundcloud_auth_base_url")
    @classmethod
    def validate_soundcloud_base_url(cls, value: str) -> str:
        return _validate_url_without_sensitive_parts(
            value,
            field_name="SoundCloud base URL",
            strip_trailing_slash=True,
        )

    @field_validator("soundcloud_resolve_endpoint")
    @classmethod
    def validate_soundcloud_resolve_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_url_without_sensitive_parts(
            value,
            field_name="SoundCloud resolve endpoint",
            strip_trailing_slash=False,
        )

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


def _validate_url_without_sensitive_parts(
    value: str,
    *,
    field_name: str,
    strip_trailing_slash: bool,
) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty.")
    parsed = urlsplit(stripped)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"{field_name} must be a URL.")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must not contain query or fragment.")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not contain credentials.")
    if strip_trailing_slash:
        return stripped.rstrip("/")
    return stripped
