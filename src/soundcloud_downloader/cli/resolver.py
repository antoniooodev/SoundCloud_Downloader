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
from soundcloud_downloader.infrastructure.http import SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.observability import configure_logging
from soundcloud_downloader.infrastructure.soundcloud import SoundCloudHttpResolver

resolver_app = typer.Typer(help="Inspect and normalize resolver inputs.")


def build_safe_http_client(settings: AppSettings) -> SafeAsyncHttpClient:
    return SafeAsyncHttpClient(settings=settings)


def build_external_resolver_service(
    settings: AppSettings,
    http_client: SafeAsyncHttpClient,
) -> ResolverService:
    resolver_port = SoundCloudHttpResolver(settings=settings, http_client=http_client)
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


def _settings_with_overrides(
    settings: AppSettings,
    *,
    allow_network: bool | None,
    resolve_endpoint: str | None,
) -> AppSettings:
    values = settings.model_dump()
    if allow_network is not None:
        values["allow_network"] = allow_network
    if resolve_endpoint is not None:
        values["soundcloud_resolve_endpoint"] = resolve_endpoint
    return AppSettings(**values)


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
    resolve_endpoint: Annotated[
        str | None,
        typer.Option("--resolve-endpoint", help="Explicit resolver endpoint override."),
    ] = None,
) -> None:
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
    else:
        request = ResolverServiceRequest(value=value)
        result = ResolverService().inspect(request)
    payload = result.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
