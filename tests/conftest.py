from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from agent_session_viewer import config
from agent_session_viewer.registry import AGENT_SPECS

app_module = importlib.import_module("agent_session_viewer.app")


def install_agent_homes(monkeypatch: pytest.MonkeyPatch, homes: dict[str, Path]) -> None:
    """Point every agent home at a temporary directory.

    Patching ``config`` alone is enough: the registry resolves ``spec.home()`` through
    it on every call, and every other module reads homes through the registry.
    """
    for agent, home in homes.items():
        monkeypatch.setattr(config, AGENT_SPECS[agent].home_attr, home)


@pytest.fixture
def agent_homes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    """Create and install empty agent homes so route tests never inspect real data."""
    homes = {agent: tmp_path / agent for agent in AGENT_SPECS}
    install_agent_homes(monkeypatch, homes)
    for spec in AGENT_SPECS.values():
        for root in spec.roots():
            root.mkdir(parents=True, exist_ok=True)
    return homes


@pytest.fixture
def client(agent_homes: dict[str, Path]):
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()
