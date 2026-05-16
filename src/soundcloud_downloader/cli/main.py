import typer

from soundcloud_downloader import __version__

app = typer.Typer(
    help="SoundCloud Downloader command line interface.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)
