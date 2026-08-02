"""The /settings route and the header entry point.

Preferences themselves live in localStorage and are covered by the browser test in
tests/test_browser_security.py; these cover the server side, which is read-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_session_viewer.registry import AGENT_SPECS


def _jsonl(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
    return path


def test_settings_page_renders(client) -> None:
    response = client.get("/settings")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for heading in ("Appearance", "Session list", "Session view", "Stored data", "This install"):
        assert heading in html


def test_settings_lists_every_agent_home_and_env_var(client, agent_homes: dict[str, Path]) -> None:
    html = client.get("/settings").get_data(as_text=True)

    for spec in AGENT_SPECS.values():
        assert spec.label in html
        assert spec.home_attr in html
        for root in spec.roots():
            assert str(root) in html


def test_settings_counts_discovered_sessions(client, agent_homes: dict[str, Path]) -> None:
    _jsonl(agent_homes["claude"] / "projects/project/one.jsonl")
    _jsonl(agent_homes["claude"] / "projects/project/two.jsonl")

    html = client.get("/settings").get_data(as_text=True)

    # The Claude row reports both sessions; the other agents' homes stay empty.
    assert ">2</td>" in html
    assert ">0</td>" in html


def test_absent_optional_root_is_a_note_not_a_warning(client, agent_homes: dict[str, Path]) -> None:
    """Codex only creates archived_sessions on first archive; absent is normal."""
    (AGENT_SPECS["codex"].home() / "archived_sessions").rmdir()

    html = client.get("/settings").get_data(as_text=True)

    assert "not created yet" in html
    assert "prefs-missing" not in html


def test_absent_required_root_is_flagged_as_missing(client, agent_homes: dict[str, Path]) -> None:
    (AGENT_SPECS["codex"].home() / "sessions").rmdir()

    html = client.get("/settings").get_data(as_text=True)

    assert "prefs-missing" in html


def test_present_roots_are_not_annotated(client, agent_homes: dict[str, Path]) -> None:
    html = client.get("/settings").get_data(as_text=True)

    assert "prefs-missing" not in html
    assert "not created yet" not in html


def test_optional_roots_are_still_searched(client, agent_homes: dict[str, Path]) -> None:
    """The required/optional split must not change which paths are authorized."""
    archived = AGENT_SPECS["codex"].home() / "archived_sessions" / "rollout-old.jsonl"
    _jsonl(archived)

    response = client.get("/view", query_string={"agent": "codex", "path": str(archived)})

    assert response.status_code == 200
    assert AGENT_SPECS["codex"].roots() == (
        AGENT_SPECS["codex"].home() / "sessions",
        AGENT_SPECS["codex"].home() / "archived_sessions",
    )


def test_settings_offers_every_agent_as_a_default_filter(client) -> None:
    html = client.get("/settings").get_data(as_text=True)

    assert '<option value="">All agents</option>' in html
    for spec in AGENT_SPECS.values():
        assert f'<option value="{spec.id}">{spec.label}</option>' in html


@pytest.mark.parametrize("route", ["/", "/settings", "/agents"])
def test_header_links_to_settings_on_every_page(client, route: str) -> None:
    html = client.get(route).get_data(as_text=True)

    assert 'class="header-nav"' in html
    assert 'href="/agents"' in html
    assert 'href="/settings"' in html
    assert 'aria-label="Agents"' in html
    assert 'aria-label="Settings"' in html
    # Agents sits left of Settings in the header markup.
    assert html.find('href="/agents"') < html.find('href="/settings"')


def test_settings_page_marks_itself_current(client) -> None:
    assert 'aria-current="page"' in client.get("/settings").get_data(as_text=True)
    assert 'aria-current="page"' not in client.get("/").get_data(as_text=True)


def test_every_page_loads_the_theme_and_prefs_bootstraps(client, agent_homes) -> None:
    session = _jsonl(agent_homes["claude"] / "projects/project/session.jsonl")

    for route, params in (
        ("/", {}),
        ("/settings", {}),
        ("/agents", {}),
        ("/view", {"agent": "claude", "path": str(session)}),
    ):
        html = client.get(route, query_string=params).get_data(as_text=True)
        head = html.split("</head>", 1)[0]
        # Both must be in <head>: the theme before first paint, and the default
        # agent redirect before the wrong list is rendered.
        assert "theme-boot.js" in head, route
        assert "prefs.js" in head, route


def test_settings_page_has_no_inline_script_or_style(client) -> None:
    """The CSP is script-src 'self' / style-src 'self'; inline blocks would break."""
    html = client.get("/settings").get_data(as_text=True)

    assert "<style" not in html
    assert " style=" not in html
    # Every <script> must be a src reference, never an inline body.
    for fragment in html.split("<script")[1:]:
        assert "src=" in fragment.split(">", 1)[0]
