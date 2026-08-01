from __future__ import annotations

import importlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
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


@pytest.fixture(scope="session")
def browser() -> Iterator[object]:
    """One Chromium instance for every `browser`-marked module.

    Shared deliberately: the sync Playwright API cannot start a second driver
    while the first one's event loop is running, so a per-module fixture breaks
    as soon as two browser test files run in the same session. Playwright is
    imported inside the fixture so a `-m "not browser"` run never loads it.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@contextmanager
def _serve() -> Iterator[str]:
    """Serve the real app on an ephemeral loopback port."""
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app_module.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def live_app():
    """The server context manager, as a fixture.

    Handed over rather than entered here so a test can control when the server
    starts relative to its own setup. Use as ``with live_app() as base_url:``.
    tests/ is not a package, so this cannot be a plain import.
    """
    return _serve
