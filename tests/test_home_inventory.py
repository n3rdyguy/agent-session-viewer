"""Unit tests for home-directory inventory (never touches real agent homes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_session_viewer import config
from agent_session_viewer.home_inventory import inventory_all, inventory_one
from agent_session_viewer.home_inventory.redact import (
    redact_command_string,
    redact_json_text,
    redact_text,
)
from agent_session_viewer.home_inventory.skills import parse_frontmatter
from agent_session_viewer.home_inventory.specs import HOME_SPECS


def _install_homes(monkeypatch: pytest.MonkeyPatch, root: Path) -> dict[str, Path]:
    homes = {}
    for spec in HOME_SPECS:
        home = root / spec.id
        home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, spec.home_attr, home)
        homes[spec.id] = home
    return homes


def test_parse_frontmatter_simple() -> None:
    text = "---\nname: demo\ndescription: Does things\n---\n\n# Body\n"
    meta, body = parse_frontmatter(text)
    assert meta["name"] == "demo"
    assert meta["description"] == "Does things"
    assert body.strip().startswith("# Body")


def test_redact_json_api_key() -> None:
    raw = json.dumps({"model": "x", "api_key": "sk-secret-value", "nested": {"token": "abc"}})
    out = redact_json_text(raw)
    assert "sk-secret-value" not in out
    assert "abc" not in out
    assert "***" in out
    assert "model" in out


def test_redact_encoded_command() -> None:
    cmd = "powershell.exe -EncodedCommand " + ("A" * 80) + " rest"
    out = redact_command_string(cmd)
    assert "A" * 40 not in out
    assert "[redacted command payload]" in out


def test_redact_toml_secret_line() -> None:
    text = 'model = "gpt"\napi_key = "secret-123"\n'
    out = redact_text(text)
    assert "secret-123" not in out
    assert "***" in out


def test_missing_home_is_graceful(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for spec in HOME_SPECS:
        monkeypatch.setattr(config, spec.home_attr, tmp_path / "nope" / spec.id)

    reports = inventory_all()
    assert len(reports) == 4
    for r in reports:
        assert r.exists is False
        assert r.skills == []
        assert r.tips  # still show recommendations


def test_inventory_collects_settings_skills_hooks_instructions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    homes = _install_homes(monkeypatch, tmp_path)

    # Claude-shaped home
    claude = homes["claude"]
    (claude / "settings.json").write_text(
        json.dumps(
            {
                "model": "opus",
                "api_key": "should-not-leak",
                "permissions": {"defaultMode": "dontAsk"},
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "echo done", "timeout": 5}]}]
                },
            }
        ),
        encoding="utf-8",
    )
    skill_dir = claude / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: A test skill\n---\n\nDo the thing.\n",
        encoding="utf-8",
    )
    (claude / "CLAUDE__.md").write_text("# Global Claude prefs\n", encoding="utf-8")
    (claude / "auth.json").write_text('{"token":"nope"}', encoding="utf-8")
    sessions = claude / "projects" / "p" / "s.jsonl"
    sessions.parent.mkdir(parents=True)
    sessions.write_text('{"type":"user"}\n', encoding="utf-8")

    # Cursor hooks
    cursor = homes["cursor"]
    (cursor / "hooks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "stop": [{"command": "echo stop", "timeout": 10}],
                },
            }
        ),
        encoding="utf-8",
    )

    # Grok config + skill
    grok = homes["grok"]
    (grok / "config.toml").write_text(
        '[models]\ndefault = "grok-4.5"\n\n[ui]\npermission_mode = "always-approve"\n',
        encoding="utf-8",
    )
    gskill = grok / "skills" / "help"
    gskill.mkdir(parents=True)
    (gskill / "SKILL.md").write_text(
        "---\nname: help\ndescription: Help skill\n---\n\nHelp.\n",
        encoding="utf-8",
    )
    (grok / "AGENTS__.md").write_text("Use rg.\n", encoding="utf-8")

    # Codex
    codex = homes["codex"]
    (codex / "config.toml").write_text(
        'approval_policy = "never"\nmodel = "gpt-test"\n\n'
        '[plugins."browser@openai-bundled"]\nenabled = true\n\n'
        '[mcp_servers.demo]\ncommand = "demo-mcp"\nenabled = true\n',
        encoding="utf-8",
    )

    reports = {r.id: r for r in inventory_all()}

    cl = reports["claude"]
    assert cl.exists
    assert any("should-not-leak" not in s.content for s in cl.settings)
    assert all("should-not-leak" not in s.content for s in cl.settings)
    cl_settings = next(s for s in cl.settings if s.path == "settings.json")
    keys = {row.key: row.value for row in cl_settings.rows}
    assert keys.get("model") == "opus"
    assert keys.get("permissions.defaultMode") == "dontAsk"
    assert keys.get("api_key") == "***"
    assert "should-not-leak" not in " ".join(keys.values())
    # Documented catalog marks set keys
    cat = {row.key: row for row in cl.catalog}
    assert cat["model"].status == "set"
    assert cat["model"].current == "opus"
    assert cat["permissions.defaultMode"].status == "set"
    assert cat["permissions.defaultMode"].current == "dontAsk"
    assert cl.catalog_total > cl.catalog_set >= 2
    # Co-Authored-By tip appears in recommended + settings placements
    assert any("Co-Authored-By" in t.title for t in cl.tips)
    assert any("Co-Authored-By" in t.title for t in cl.settings_tips)
    assert any(s.name == "my-skill" for s in cl.skills)
    assert any(h.event == "Stop" for h in cl.hooks)
    assert any(i.path == "CLAUDE__.md" for i in cl.instructions)
    # auth.json and session bodies must not appear as settings/skills content
    blob = "\n".join(s.content for s in cl.settings)
    assert "nope" not in blob or "should-not-leak" not in blob

    cu = reports["cursor"]
    assert any(h.event == "stop" for h in cu.hooks)

    gr = reports["grok"]
    assert any(s.name == "help" for s in gr.skills)
    # Grok inventory also pulls Claude user skills (compat on by default).
    assert any(s.name == "my-skill" and s.source == "claude-compat" for s in gr.skills)
    assert any("multi-location" in t.title.lower() or "many locations" in t.title.lower() for t in gr.tips)
    assert any("many locations" in t.title.lower() or "Skill" in t.title for t in gr.settings_tips)
    assert any("grok-4.5" in s.content for s in gr.settings)
    gr_cfg = next(s for s in gr.settings if s.path == "config.toml")
    gr_keys = {row.key: row.value for row in gr_cfg.rows}
    assert gr_keys.get("models.default") == "grok-4.5"
    assert gr_keys.get("ui.permission_mode") == "always-approve"
    assert any(i.path == "AGENTS__.md" for i in gr.instructions)
    # permissive tip note for always-approve
    tip_notes = [t.permissive_note for t in gr.tips if t.permissive_note]
    assert tip_notes

    co = reports["codex"]
    assert any(p.name.startswith("browser") for p in co.plugins)
    assert any(m.name == "demo" for m in co.mcp_servers)


def test_skill_body_truncated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    homes = _install_homes(monkeypatch, tmp_path)
    skill = homes["claude"] / "skills" / "big"
    skill.mkdir(parents=True)
    # Just over MAX_TEXT_BYTES so the scanner truncates without a multi-MB write.
    from agent_session_viewer.home_inventory.specs import MAX_TEXT_BYTES

    huge = "x" * (MAX_TEXT_BYTES + 50)
    (skill / "SKILL.md").write_text("---\nname: big\n---\n\n" + huge, encoding="utf-8")

    report = inventory_one(next(s for s in HOME_SPECS if s.id == "claude"))
    big = next(s for s in report.skills if s.name == "big")
    assert big.truncated
    assert "[truncated]" in big.body
