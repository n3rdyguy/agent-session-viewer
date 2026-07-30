#!/usr/bin/env python3
"""Deprecated source-checkout shim; use ``agent_session_viewer.app`` instead."""

from agent_session_viewer.app import app, run

__all__ = ["app", "run"]


if __name__ == "__main__":
    run()
