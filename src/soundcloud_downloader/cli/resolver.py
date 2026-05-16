import json

import typer

from soundcloud_downloader.application import ResolverService, ResolverServiceRequest

resolver_app = typer.Typer(help="Inspect and normalize resolver inputs.")


@resolver_app.command("inspect")
def inspect_resolver_input(value: str) -> None:
    request = ResolverServiceRequest(value=value)
    result = ResolverService().inspect(request)
    payload = result.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
