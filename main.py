"""Deprecated source-checkout shim; use ``agent_session_viewer.cli`` instead.

Scheduled for removal in 0.3.0. Not shipped in the wheel, only in the sdist.
"""

from agent_session_viewer.cli import main

if __name__ == "__main__":
    main()
