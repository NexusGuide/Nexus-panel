#!/usr/bin/env python3
"""PasarGuard CLI"""

import typer

from cli import console
from cli.admin import generate_temp_key

app = typer.Typer(
    name="PasarGuard",
    help="PasarGuard CLI",
    add_completion=False,
    rich_markup_mode="rich",
)


@app.command("generate-temp-key")
def cmd_generate_temp_key():
    """Generate a one-time temp key for owner setup (create/reset/delete)."""
    generate_temp_key()


@app.command()
def version():
    """Show the panel version."""
    from app.version import UPSTREAM_VERSION, __version__

    console.print(
        f"[bold blue]Nexus Panel[/bold blue] version [bold green]{__version__}[/bold green] "
        f"(based on PasarGuard {UPSTREAM_VERSION})"
    )


if __name__ == "__main__":
    app()
