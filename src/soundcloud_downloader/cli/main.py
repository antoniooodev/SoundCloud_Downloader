from importlib.metadata import PackageNotFoundError, version
from typing import Annotated

import typer

from soundcloud_downloader.cli.doctor import doctor
from soundcloud_downloader.cli.download import download_app
from soundcloud_downloader.cli.oauth import oauth_app
from soundcloud_downloader.cli.policy import policy_app
from soundcloud_downloader.cli.reconstruction import plan_app
from soundcloud_downloader.cli.resolver import resolver_app

PACKAGE_NAME = "soundcloud-downloader"
UNKNOWN_VERSION = "0.0.0+unknown"


def get_package_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return UNKNOWN_VERSION


def format_version() -> str:
    return f"{PACKAGE_NAME} {get_package_version()}"


def version_callback(value: bool) -> None:
    if value:
        typer.echo(format_version())
        raise typer.Exit()


app = typer.Typer(
    help="SoundCloud Downloader command line interface.",
    no_args_is_help=True,
)
app.add_typer(download_app, name="download")
app.add_typer(oauth_app, name="oauth")
app.add_typer(policy_app, name="policy")
app.add_typer(plan_app, name="plan")
app.add_typer(resolver_app, name="resolver")
app.command("doctor", help="Inspect local configuration before running downloads.")(doctor)


@app.callback()
def main(
    version_option: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=version_callback,
            help="Print the package version and exit.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """SoundCloud Downloader command line interface."""


@app.command("version")
def version_command() -> None:
    """Print the package version."""
    typer.echo(format_version())
