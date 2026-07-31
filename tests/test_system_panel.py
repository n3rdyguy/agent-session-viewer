"""Grok/Codex system turns are lifted into a collapsed system panel."""

from __future__ import annotations

import json
from pathlib import Path

from agent_session_viewer.session import (
    is_system_panel_turn,
    load_session,
    split_system_panel_turns,
    system_artifacts_to_markdown,
    system_turn_title,
)
from agent_session_viewer.turns import make_turn

FIXTURES = Path(__file__).parent / "fixtures"


def test_system_panel_turn_detection() -> None:
    assert is_system_panel_turn(make_turn(role="system", text="x"))
    assert is_system_panel_turn(make_turn(role="system_reminder", text="x"))
    assert is_system_panel_turn(make_turn(role="developer", text="x"))
    assert is_system_panel_turn(
        make_turn(role="user (project_instructions)", text="x", meta="project_instructions")
    )
    assert is_system_panel_turn(
        make_turn(role="user (user_instructions)", text="x", meta="user_instructions")
    )
    assert is_system_panel_turn(
        make_turn(role="user (user_info)", text="<user_info>os</user_info>", meta="user_info")
    )
    assert is_system_panel_turn(
        make_turn(role="user", text="<user_info>\nOS: windows\n</user_info>")
    )
    assert not is_system_panel_turn(make_turn(role="user", text="hello"))
    assert not is_system_panel_turn(make_turn(role="assistant", text="hi"))
    assert not is_system_panel_turn(make_turn(role="tool_call", text="run"))


def test_split_moves_system_out_of_chat() -> None:
    turns = [
        make_turn(role="system", text="You are an agent.", meta="developer", id="#1"),
        make_turn(
            role="user (project_instructions)",
            text="# AGENTS.md\n\nrules",
            meta="project_instructions",
            id="#2",
        ),
        make_turn(role="user", text="Do the work.", id="#3"),
        make_turn(role="assistant", text="Done.", id="#4"),
        make_turn(role="system_reminder", text="<system-reminder>note</system-reminder>", id="#5"),
    ]
    chat, system_docs = split_system_panel_turns(turns)

    assert [t["role"] for t in chat] == ["user", "assistant"]
    assert [d["title"] for d in system_docs] == [
        "developer",
        "project instructions",
        "system reminder",
    ]
    assert system_docs[0]["text"] == "You are an agent."
    assert system_docs[1]["text"].startswith("# AGENTS.md")


def test_codex_load_session_lifts_system_turns() -> None:
    session = load_session("codex", FIXTURES / "codex" / "rollout-test.jsonl")
    roles = [t["role"] for t in session["turns"]]
    assert "system" not in roles
    assert roles == ["user", "reasoning", "tool_call", "tool_result", "assistant"]
    assert session["system_artifacts"]
    assert any(d["title"] in ("developer", "system") for d in session["system_artifacts"])
    assert any("fixture instructions" in d["text"].lower() for d in session["system_artifacts"])


def test_grok_load_session_lifts_system_and_user_instructions(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": "sess", "cwd": "C:/p"}, "generated_title": "t"}),
        encoding="utf-8",
    )
    (session_dir / "chat_history.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "system", "content": "You are Grok.", "timestamp": "2026-07-30T08:00:00Z"}),
                json.dumps(
                    {
                        "type": "user",
                        "content": (
                            "<user_info>\nOS Version: windows\nShell: pwsh\n"
                            "Workspace Path: C:/p\n</user_info>\n\n"
                            "<git_status>\n## main\n</git_status>"
                        ),
                        "timestamp": "2026-07-30T08:00:00.5Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "synthetic_reason": "project_instructions",
                        "content": "Project rules here.",
                        "timestamp": "2026-07-30T08:00:01Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "content": "Real user prompt.",
                        "timestamp": "2026-07-30T08:00:02Z",
                        "prompt_index": 1,
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "content": "ok",
                        "timestamp": "2026-07-30T08:00:03Z",
                        "id": "a1",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = load_session("grok", session_dir)
    assert [t["role"] for t in session["turns"]] == ["user", "assistant"]
    assert session["turns"][0]["text"] == "Real user prompt."
    titles = [d["title"] for d in (session.get("system_artifacts") or [])]
    assert "user info" in titles
    assert any("project" in t for t in titles)
    texts = " ".join(d["text"] for d in (session.get("system_artifacts") or []))
    assert "You are Grok." in texts
    assert "Project rules here." in texts
    assert "<user_info>" in texts
    assert "Workspace Path: C:/p" in texts


def test_system_artifacts_markdown_export() -> None:
    md = system_artifacts_to_markdown(
        [{"title": "developer", "subtitle": "#1", "text": "Be careful."}]
    )
    assert "### System instructions" in md
    assert "#### developer" in md
    assert "Be careful." in md


def test_system_turn_title_prefers_meta() -> None:
    assert system_turn_title(make_turn(role="system", meta="developer", text="x")) == "developer"
    assert (
        system_turn_title(make_turn(role="user (project_instructions)", text="x"))
        == "project instructions"
    )
