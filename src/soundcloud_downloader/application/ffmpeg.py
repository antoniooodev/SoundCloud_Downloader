from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

_REDACTED_PATH = "[PATH]"
_REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_MARKERS = frozenset(
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


class FFMPEGCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    args: tuple[str, ...] = Field(min_length=1)

    @field_validator("args")
    @classmethod
    def validate_args(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for arg in value:
            if arg == "":
                raise ValueError("ffmpeg command arguments must not be empty.")
            if "\x00" in arg or "\n" in arg or "\r" in arg:
                raise ValueError("ffmpeg command arguments must not contain control characters.")
        return value


class FFMPEGResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    return_code: int
    stdout: str = ""
    stderr: str = ""


@runtime_checkable
class FFMPEGRunnerPort(Protocol):
    def run(self, command: FFMPEGCommand) -> FFMPEGResult:
        ...


def redact_ffmpeg_command(command: FFMPEGCommand) -> dict[str, object]:
    return {"args": [_redact_arg(arg, index=index) for index, arg in enumerate(command.args)]}


def sanitize_ffmpeg_output(value: str) -> str:
    sanitized_parts = [_redact_arg(part, index=1) for part in value.split()]
    return " ".join(sanitized_parts)


def _redact_arg(arg: str, *, index: int) -> str:
    lowered = arg.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return _REDACTED_VALUE
    if urlsplit(arg).scheme in {"http", "https"}:
        return _REDACTED_VALUE
    if index > 0 and _is_path_like(arg):
        return _REDACTED_PATH
    return arg


def _is_path_like(arg: str) -> bool:
    path = Path(arg)
    return path.is_absolute() or "/" in arg or "\\" in arg
