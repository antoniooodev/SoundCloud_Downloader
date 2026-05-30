import json
import shutil
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from soundcloud_downloader.config import AppSettings, load_settings


_DOWNLOAD_REQUIREMENTS = (
    ("allow_network", "allow_network"),
    ("allow_filesystem_writes", "allow_filesystem_writes"),
    ("soundcloud_client_id", "soundcloud_client_id_present"),
    ("soundcloud_client_secret", "soundcloud_client_secret_present"),
    ("oauth_token_encryption_key", "oauth_token_encryption_key_present"),
    ("oauth_session_encryption_key", "oauth_session_encryption_key_present"),
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
    try:
        settings = load_settings(env_file=env_file)
    except ValidationError:
        report = build_settings_error_report()
    else:
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


def build_settings_error_report() -> dict[str, object]:
    return {
        "status": "error",
        "download_ready": False,
        "missing_required": [],
        "checks": {},
        "warnings": (),
        "errors": ("settings could not be loaded.",),
    }


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

    missing_required = _missing_required(checks, check_ffmpeg=check_ffmpeg)
    download_ready = not missing_required and not errors

    if errors:
        status = "error"
    elif not download_ready or warnings:
        status = "warning"
    else:
        status = "ok"

    return {
        "status": status,
        "download_ready": download_ready,
        "missing_required": missing_required,
        "checks": checks,
        "warnings": tuple(warnings),
        "errors": tuple(errors),
    }


def _missing_required(
    checks: dict[str, object],
    *,
    check_ffmpeg: bool,
) -> list[str]:
    missing = [
        symbolic_name
        for symbolic_name, check_name in _DOWNLOAD_REQUIREMENTS
        if checks.get(check_name) is not True
    ]
    if not isinstance(checks.get("ffmpeg_binary"), str) or not str(
        checks.get("ffmpeg_binary")
    ).strip():
        missing.append("ffmpeg")
    elif check_ffmpeg and checks.get("ffmpeg_found") is not True:
        missing.append("ffmpeg")
    return missing


def _echo_plain(report: dict[str, object]) -> None:
    typer.echo(f"status={report['status']}")
    if "download_ready" in report:
        typer.echo(f"download_ready={str(report['download_ready']).lower()}")
    missing_required = report.get("missing_required", ())
    if isinstance(missing_required, (list, tuple)):
        joined = ",".join(str(item) for item in missing_required)
        typer.echo(f"missing_required={joined}")
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
