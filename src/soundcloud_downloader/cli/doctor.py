import json
import shutil
from pathlib import Path
from typing import Annotated

import typer

from soundcloud_downloader.config import AppSettings, load_settings


_REQUIRED_PRESENT_CHECKS = (
    "soundcloud_client_id_present",
    "soundcloud_client_secret_present",
    "oauth_token_encryption_key_present",
    "oauth_session_encryption_key_present",
)


def doctor(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Explicit settings env file."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json/--plain", help="Print structured JSON or safe key/value lines."),
    ] = True,
    check_ffmpeg: Annotated[
        bool,
        typer.Option(
            "--check-ffmpeg/--no-check-ffmpeg",
            help="Probe PATH for the configured ffmpeg binary (no media execution).",
        ),
    ] = True,
    check_paths: Annotated[
        bool,
        typer.Option(
            "--check-paths/--no-check-paths",
            help="Inspect configured filesystem paths without creating directories.",
        ),
    ] = True,
) -> None:
    settings = load_settings(env_file=env_file)
    report = build_doctor_report(
        settings,
        check_ffmpeg=check_ffmpeg,
        check_paths=check_paths,
    )
    if json_output:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
    else:
        _echo_plain(report)
    if report["status"] == "error":
        raise typer.Exit(code=1)


def build_doctor_report(
    settings: AppSettings,
    *,
    check_ffmpeg: bool = True,
    check_paths: bool = True,
) -> dict[str, object]:
    checks: dict[str, object] = {
        "allow_network": bool(settings.allow_network),
        "allow_filesystem_writes": bool(settings.allow_filesystem_writes),
        "soundcloud_client_id_present": settings.soundcloud_client_id is not None,
        "soundcloud_client_secret_present": settings.soundcloud_client_secret is not None,
        "oauth_session_encryption_key_present": (
            settings.oauth_session_encryption_key is not None
        ),
        "oauth_token_encryption_key_present": settings.oauth_token_encryption_key is not None,
        "oauth_session_store_path": str(settings.oauth_session_store_path),
        "oauth_token_store_path": str(settings.oauth_token_store_path),
        "artifact_storage_root": str(settings.artifact_storage_root),
        "artifact_temp_root": str(settings.artifact_temp_root),
        "ffmpeg_binary": settings.ffmpeg_binary,
        "ffmpeg_timeout_seconds": settings.ffmpeg_timeout_seconds,
    }

    warnings: list[str] = []
    errors: list[str] = []

    for check_name in _REQUIRED_PRESENT_CHECKS:
        if not checks[check_name]:
            errors.append(f"{check_name.replace('_present', '')} is not configured.")

    if not isinstance(settings.ffmpeg_binary, str) or settings.ffmpeg_binary.strip() == "":
        errors.append("ffmpeg_binary is not configured.")
    if settings.ffmpeg_timeout_seconds <= 0:
        errors.append("ffmpeg_timeout_seconds must be greater than zero.")

    if check_ffmpeg:
        resolved = shutil.which(settings.ffmpeg_binary)
        checks["ffmpeg_found"] = resolved is not None
        if resolved is None:
            warnings.append("ffmpeg binary was not found on PATH.")
    if check_paths:
        for setting_name in ("artifact_storage_root", "artifact_temp_root"):
            raw_value = getattr(settings, setting_name)
            if str(raw_value) == "":
                errors.append(f"{setting_name} is empty.")

    if not checks["allow_network"]:
        warnings.append("allow_network is false; downloads require it to be enabled.")
    if not checks["allow_filesystem_writes"]:
        warnings.append(
            "allow_filesystem_writes is false; downloads require it to be enabled."
        )

    if errors:
        status = "error"
    elif warnings:
        status = "warning"
    else:
        status = "ok"

    return {
        "status": status,
        "checks": checks,
        "warnings": tuple(warnings),
        "errors": tuple(errors),
    }


def _echo_plain(report: dict[str, object]) -> None:
    typer.echo(f"status={report['status']}")
    checks = report.get("checks", {})
    assert isinstance(checks, dict)
    for key in sorted(checks):
        value = checks[key]
        if isinstance(value, bool):
            typer.echo(f"{key}={str(value).lower()}")
        else:
            typer.echo(f"{key}={value}")
    warnings = report.get("warnings", ())
    if isinstance(warnings, (list, tuple)):
        for warning in warnings:
            typer.echo(f"warning={warning}")
    errors = report.get("errors", ())
    if isinstance(errors, (list, tuple)):
        for error in errors:
            typer.echo(f"error={error}")
