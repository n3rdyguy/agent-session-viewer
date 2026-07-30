"""Installed command-line entrypoint for Agent Session Viewer."""

from .app import run


def main() -> None:
    """Start the local Agent Session Viewer server."""
    run()
