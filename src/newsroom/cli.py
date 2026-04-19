"""CLI entry point. Real subcommands added in Task 14."""
import typer

from newsroom import __version__

app = typer.Typer(help="Daily Newsroom — local news-digest agent")


@app.command()
def version() -> None:
    """Print version."""
    print(f"newsroom {__version__}")


# Placeholder — real subcommands (fetch, score, digest) added in Task 14.
@app.command(hidden=True)
def _placeholder() -> None:
    """Placeholder to enable multi-command mode."""
