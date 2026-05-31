import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from soundcloud_downloader.application import (
    ResolverService,
    ResolverServiceRequest,
    ResolverServiceResult,
)
from soundcloud_downloader.config import AppSettings, load_settings
from soundcloud_downloader.domain import (
    ErrorCode,
    OAuthClientId,
    OAuthClientSecret,
    OAuthTokenProfileId,
    SoundcloudDownloaderError,
)
from soundcloud_downloader.infrastructure.http import SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.oauth import (
    AutoRefreshingAccessTokenProvider,
    EncryptedOAuthTokenStore,
)
from soundcloud_downloader.infrastructure.observability import configure_logging
from soundcloud_downloader.infrastructure.soundcloud import (
    OAuthRefreshTokenService,
    OfficialSoundCloudResolver,
    SoundCloudHttpResolver,
)

resolver_app = typer.Typer(help="Inspect and normalize resolver inputs.")


def build_safe_http_client(settings: AppSettings) -> SafeAsyncHttpClient:
    return SafeAsyncHttpClient(settings=settings)


def build_external_resolver_service(
    settings: AppSettings,
    http_client: SafeAsyncHttpClient,
) -> ResolverService:
    resolver_port = SoundCloudHttpResolver(settings=settings, http_client=http_client)
    return ResolverService(resolver_port=resolver_port)


def build_official_resolver_service(
    settings: AppSettings,
    http_client: SafeAsyncHttpClient,
    *,
    profile_id: OAuthTokenProfileId,
) -> ResolverService:
    client_id, client_secret = _oauth_client_credentials(settings)
    token_store = EncryptedOAuthTokenStore(settings)
    refresh_service = OAuthRefreshTokenService(settings=settings, http_client=http_client)
    token_provider = AutoRefreshingAccessTokenProvider(
        token_store=token_store,
        refresh_service=refresh_service,
        client_id=client_id,
        client_secret=client_secret,
        profile_id=profile_id,
    )
    resolver_port = OfficialSoundCloudResolver(
        settings=settings,
        http_client=http_client,
        token_provider=token_provider,
    )
    return ResolverService(resolver_port=resolver_port)


async def inspect_with_external_resolver(
    value: str,
    settings: AppSettings,
) -> ResolverServiceResult:
    quiet_values = settings.model_dump()
    quiet_values["log_level"] = "error"
    quiet_settings = AppSettings(**quiet_values)
    configure_logging(quiet_settings)
    async with build_safe_http_client(settings) as http_client:
        service = build_external_resolver_service(settings, http_client)
        return await service.resolve(
            ResolverServiceRequest(
                value=value,
                allow_external_resolution=True,
            )
        )


async def inspect_with_official_resolver(
    value: str,
    settings: AppSettings,
    *,
    profile_id: OAuthTokenProfileId,
) -> ResolverServiceResult:
    quiet_values = settings.model_dump()
    quiet_values["log_level"] = "error"
    quiet_settings = AppSettings(**quiet_values)
    configure_logging(quiet_settings)
    async with build_safe_http_client(settings) as http_client:
        service = build_official_resolver_service(
            settings,
            http_client,
            profile_id=profile_id,
        )
        return await service.resolve(
            ResolverServiceRequest(
                value=value,
                allow_external_resolution=True,
            )
        )


def _settings_with_overrides(
    settings: AppSettings,
    *,
    allow_network: bool | None,
    allow_filesystem_writes: bool | None = None,
    resolve_endpoint: str | None,
    token_store_path: Path | None = None,
) -> AppSettings:
    values = settings.model_dump()
    if allow_network is not None:
        values["allow_network"] = allow_network
    if allow_filesystem_writes is not None:
        values["allow_filesystem_writes"] = allow_filesystem_writes
    if resolve_endpoint is not None:
        values["soundcloud_resolve_endpoint"] = resolve_endpoint
    if token_store_path is not None:
        values["oauth_token_store_path"] = token_store_path
    return AppSettings(**values)


def _oauth_client_credentials(settings: AppSettings) -> tuple[OAuthClientId, OAuthClientSecret]:
    if settings.soundcloud_client_id is None or settings.soundcloud_client_secret is None:
        raise SoundcloudDownloaderError(
            ErrorCode.AUTH_REQUIRED,
            "Authenticated resolver mode is not configured.",
        )
    return (
        OAuthClientId(value=settings.soundcloud_client_id),
        OAuthClientSecret(value=settings.soundcloud_client_secret),
    )


def _validate_official_settings(settings: AppSettings) -> None:
    if not settings.allow_network:
        typer.echo(
            "Network access must be enabled for official resolver mode.",
            err=True,
        )
        raise typer.Exit(code=1)
    if not settings.allow_filesystem_writes:
        typer.echo(
            "Filesystem writes must be enabled for official resolver mode.",
            err=True,
        )
        raise typer.Exit(code=1)
    if settings.oauth_token_encryption_key is None:
        typer.echo(
            "Authenticated resolver mode is not configured.",
            err=True,
        )
        raise typer.Exit(code=1)
    if settings.soundcloud_client_id is None:
        typer.echo(
            "Authenticated resolver mode is not configured.",
            err=True,
        )
        raise typer.Exit(code=1)
    if settings.soundcloud_client_secret is None:
        typer.echo(
            "Authenticated resolver mode is not configured.",
            err=True,
        )
        raise typer.Exit(code=1)


def _echo_result(result: ResolverServiceResult, *, resolution_mode: str | None = None) -> None:
    payload = result.model_dump(mode="json")
    if resolution_mode is not None:
        payload["resolution_mode"] = resolution_mode
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _exit_official_failure(result: ResolverServiceResult) -> None:
    if result.resolved:
        return
    typer.echo("Official resolver request failed.", err=True)
    reason = _official_failure_reason(result)
    typer.echo(f"reason={reason}", err=True)
    if reason == "official_resolver_payload_invalid":
        typer.echo(f"invalid_fields={_official_invalid_fields(result)}", err=True)
    raise typer.Exit(code=1)


def _official_failure_reason(result: ResolverServiceResult) -> str:
    warnings = " ".join(result.warnings).lower()
    if any(
        marker in warnings
        for marker in (
            "malformed",
            "invalid json",
            "non-object json",
            "unsupported official resource kind",
            "forbidden fields",
        )
    ):
        return "official_resolver_payload_invalid"
    if result.resolved_resource is not None and result.resolved_resource.status.value == "error":
        return "official_resolver_payload_invalid"
    return "unknown"


def _official_invalid_fields(result: ResolverServiceResult) -> str:
    if result.resolved_resource is None or not result.resolved_resource.invalid_fields:
        return "unknown"
    return ",".join(result.resolved_resource.invalid_fields)


@resolver_app.command("inspect")
def inspect_resolver_input(
    value: str,
    external: Annotated[
        bool,
        typer.Option(
            "--external/--offline",
            help="Use the configured external resolver skeleton.",
        ),
    ] = False,
    official: Annotated[
        bool,
        typer.Option("--official", help="Use the official authenticated SoundCloud resolver."),
    ] = False,
    profile_id: Annotated[
        str,
        typer.Option("--profile-id", help="OAuth token profile to use for official mode."),
    ] = "default",
    token_store_path: Annotated[
        Path | None,
        typer.Option("--token-store-path", help="Explicit OAuth token store path override."),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Explicit settings env file."),
    ] = None,
    allow_network: Annotated[
        bool | None,
        typer.Option(
            "--allow-network/--no-allow-network",
            help="Override settings network gate for this command.",
        ),
    ] = None,
    allow_filesystem_writes: Annotated[
        bool | None,
        typer.Option(
            "--allow-filesystem-writes/--no-allow-filesystem-writes",
            help="Override settings filesystem write gate for this command.",
        ),
    ] = None,
    resolve_endpoint: Annotated[
        str | None,
        typer.Option("--resolve-endpoint", help="Explicit resolver endpoint override."),
    ] = None,
) -> None:
    if external and official:
        typer.echo(
            "Official resolver mode and external resolver mode are mutually exclusive.",
            err=True,
        )
        raise typer.Exit(code=1)

    if external:
        settings = _settings_with_overrides(
            load_settings(env_file=env_file),
            allow_network=allow_network,
            resolve_endpoint=resolve_endpoint,
        )
        if not settings.allow_network:
            typer.echo(
                "External resolver inspection requires network access to be enabled.",
                err=True,
            )
            raise typer.Exit(code=1)
        if settings.soundcloud_resolve_endpoint is None:
            typer.echo(
                "External resolver inspection requires an explicit resolve endpoint.",
                err=True,
            )
            raise typer.Exit(code=1)
        result = asyncio.run(inspect_with_external_resolver(value, settings))
        _echo_result(result)
        return
    if official:
        settings = _settings_with_overrides(
            load_settings(env_file=env_file),
            allow_network=allow_network,
            allow_filesystem_writes=allow_filesystem_writes,
            resolve_endpoint=None,
            token_store_path=token_store_path,
        )
        _validate_official_settings(settings)
        try:
            result = asyncio.run(
                inspect_with_official_resolver(
                    value,
                    settings,
                    profile_id=OAuthTokenProfileId(value=profile_id),
                )
            )
        except SoundcloudDownloaderError:
            typer.echo("OAuth token profile is missing or unusable.", err=True)
            raise typer.Exit(code=1) from None
        except Exception:
            typer.echo("Official resolver request failed.", err=True)
            typer.echo("reason=unknown", err=True)
            raise typer.Exit(code=1) from None
        _exit_official_failure(result)
        _echo_result(result, resolution_mode="official")
        return
    else:
        request = ResolverServiceRequest(value=value)
        result = ResolverService().inspect(request)
    _echo_result(result)
