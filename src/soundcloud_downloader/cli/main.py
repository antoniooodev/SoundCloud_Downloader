import typer

from soundcloud_downloader import __version__
from soundcloud_downloader.cli.policy import policy_app

app = typer.Typer(
    help="SoundCloud Downloader command line interface.",
    no_args_is_help=True,
)
app.add_typer(policy_app, name="policy")


@app.callback()
def main() -> None:
    """SoundCloud Downloader command line interface."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)
