"""Installed command-line entrypoint for Agent Session Viewer."""

import argparse

from .app import run


def main() -> None:
    """Start the local Agent Session Viewer server."""
    parser = argparse.ArgumentParser(description="Browse local agent sessions")
    parser.add_argument("--host", default="127.0.0.1", help="server host (default: %(default)s)")
    parser.add_argument("--port", default=5050, type=int, help="server port (default: %(default)s)")
    args = parser.parse_args()
    run(host=args.host, port=args.port)
