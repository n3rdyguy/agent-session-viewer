#!/usr/bin/env python3
"""Backward-compatible module entrypoint."""

from agent_session_viewer.app import app, run

__all__ = ["app", "run"]


if __name__ == "__main__":
    run()
