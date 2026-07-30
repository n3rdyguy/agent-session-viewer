from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from agent_session_viewer import authorization, discovery, util

app_module = importlib.import_module("agent_session_viewer.app")


@pytest.fixture
def agent_homes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    """Create and install empty agent homes so route tests never inspect real data."""
    homes = {
        "grok": tmp_path / "grok",
        "claude": tmp_path / "claude",
        "codex": tmp_path / "codex",
    }
    for home, child in (
        (homes["grok"], "sessions"),
        (homes["claude"], "projects"),
        (homes["codex"], "sessions"),
        (homes["codex"], "archived_sessions"),
    ):
        (home / child).mkdir(parents=True, exist_ok=True)

    for module in (app_module, discovery, util):
        monkeypatch.setattr(module, "GROK_HOME", homes["grok"])
        monkeypatch.setattr(module, "CLAUDE_HOME", homes["claude"])
        monkeypatch.setattr(module, "CODEX_HOME", homes["codex"])
    monkeypatch.setattr(authorization.config, "GROK_HOME", homes["grok"])
    monkeypatch.setattr(authorization.config, "CLAUDE_HOME", homes["claude"])
    monkeypatch.setattr(authorization.config, "CODEX_HOME", homes["codex"])
    return homes


@pytest.fixture
def client(agent_homes: dict[str, Path]):
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()
