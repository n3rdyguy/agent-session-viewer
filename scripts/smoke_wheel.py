"""Verify that a built wheel contains and can load the current runtime assets."""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    wheels = sorted(Path("dist").glob("agent_session_viewer-*.whl"))
    if not wheels:
        raise SystemExit("No built wheel found in dist/")

    required = {
        "agent_session_viewer/app.py",
        "app.py",
        "main.py",
        "static/app.css",
        "static/app.js",
        "templates/base.html",
        "templates/list.html",
        "templates/view.html",
    }
    with zipfile.ZipFile(wheels[-1]) as wheel:
        names = set(wheel.namelist())
        missing = sorted(required - names)
        if missing:
            raise SystemExit(f"Wheel is missing runtime files: {', '.join(missing)}")

        with tempfile.TemporaryDirectory() as directory:
            wheel.extractall(directory)
            for name in ("GROK_HOME", "CLAUDE_HOME", "CODEX_HOME"):
                os.environ[name] = str(Path(directory) / name.lower())
            sys.path.insert(0, directory)
            from agent_session_viewer.app import app

            response = app.test_client().get("/")
            if response.status_code != 200 or b"Agent Session Viewer" not in response.data:
                raise SystemExit("Installed-wheel Flask smoke test failed")


if __name__ == "__main__":
    main()
