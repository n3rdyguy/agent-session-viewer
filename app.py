#!/usr/bin/env python3
"""Deprecated source-checkout shim; use ``agent_session_viewer.app`` instead.

Scheduled for removal in 0.3.0. Not shipped in the wheel, only in the sdist.
"""

from agent_session_viewer.app import app, run

__all__ = ["app", "run"]


if __name__ == "__main__":
    run()
