"""The /agents inventory page and header entry point."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_session_viewer import config
from agent_session_viewer.home_inventory.specs import HOME_SPECS

app_module = __import__("agent_session_viewer.app", fromlist=["app"])


@pytest.fixture
def inventory_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Point every inventory home at a temp dir so tests never read real homes."""
    homes: dict[str, Path] = {}
    for spec in HOME_SPECS:
        home = tmp_path / spec.id
        home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, spec.home_attr, home)
        homes[spec.id] = home

    # Minimal content so the page has something to render.
    claude = homes["claude"]
    (claude / "settings.json").write_text(
        json.dumps(
            {
                "model": "test-model",
                "api_key": "super-secret-key-xyz",
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": "echo hi", "timeout": 3}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    skill = claude / "skills" / "fixture-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: fixture-skill\ndescription: Fixture only\n---\n\n# Fixture skill body\n",
        encoding="utf-8",
    )
    (claude / "CLAUDE.md").write_text("# Claude global\n", encoding="utf-8")

    (homes["grok"] / "config.toml").write_text(
        '[models]\ndefault = "grok-test"\n', encoding="utf-8"
    )
    (homes["codex"] / "config.toml").write_text(
        'approval_policy = "on-request"\n', encoding="utf-8"
    )
    (homes["cursor"] / "hooks.json").write_text(
        json.dumps({"version": 1, "hooks": {"stop": [{"command": "true"}]}}),
        encoding="utf-8",
    )
    return homes


@pytest.fixture
def inv_client(inventory_homes: dict[str, Path]):
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def test_agents_page_renders(inv_client) -> None:
    response = inv_client.get("/agents")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for label in ("Grok", "Claude", "Codex", "Cursor"):
        assert label in html
    assert "Recommended settings" in html
    assert "kv-table" in html or "settings.json" in html
    assert "Full file" in html
    assert 'class="settings-raw"' in html
    assert "All documented settings" in html
    assert "settings-catalog" in html
    assert "fixture-skill" in html


def test_claude_coauthor_tip_on_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Claude documents Co-Authored-By default and shows it under Settings tips."""
    from agent_session_viewer import config
    from agent_session_viewer.home_inventory.specs import HOME_SPECS

    for spec in HOME_SPECS:
        home = tmp_path / spec.id
        home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, spec.home_attr, home)

    claude = tmp_path / "claude"
    # Unset includeCoAuthoredBy → tip should warn default is ON
    (claude / "settings.json").write_text(
        json.dumps({"model": "opus"}), encoding="utf-8"
    )

    app_module.app.config.update(TESTING=True)
    html = app_module.app.test_client().get("/agents").get_data(as_text=True)
    assert "Co-Authored-By" in html
    assert "includeCoAuthoredBy" in html
    assert "Settings tips" in html
    assert "not set" in html or "default applies" in html or "Unset" in html


def test_agents_nav_link_left_of_settings(inv_client) -> None:
    html = inv_client.get("/").get_data(as_text=True)
    agents_pos = html.find('href="/agents"')
    settings_pos = html.find('href="/settings"')
    assert agents_pos != -1
    assert settings_pos != -1
    assert agents_pos < settings_pos


def test_agents_page_on_agents_aria_current(inv_client) -> None:
    html = inv_client.get("/agents").get_data(as_text=True)
    # The Agents nav link should be current
    assert 'href="/agents"' in html
    assert 'aria-current="page"' in html


def test_agents_missing_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for spec in HOME_SPECS:
        monkeypatch.setattr(config, spec.home_attr, tmp_path / "absent" / spec.id)
    app_module.app.config.update(TESTING=True)
    client = app_module.app.test_client()
    response = client.get("/agents")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "home missing" in html
