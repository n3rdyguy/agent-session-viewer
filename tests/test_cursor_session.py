"""Cursor agent-transcript discovery and parsing."""

from __future__ import annotations

import json
from pathlib import Path

from agent_session_viewer.agents.cursor import is_cursor_transcript, load_session
from agent_session_viewer.authorization import resolve_session_path
from agent_session_viewer.discovery import discover_cursor


def _write_transcript(home: Path, project: str, sid: str, records: list[dict]) -> Path:
    path = home / "projects" / project / "agent-transcripts" / sid / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return path


def test_is_cursor_transcript_shape(tmp_path: Path) -> None:
    good = _write_transcript(tmp_path, "proj", "abc-123", [{"role": "user"}])
    assert is_cursor_transcript(good)
    bad = tmp_path / "projects" / "proj" / "other.jsonl"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{}\n", encoding="utf-8")
    assert not is_cursor_transcript(bad)


def test_load_session_extracts_user_query_and_tools(
    agent_homes: dict[str, Path],
) -> None:
    home = agent_homes["cursor"]
    sid = "11111111-2222-3333-4444-555555555555"
    path = _write_transcript(
        home,
        "c-Users-Test-app",
        sid,
        [
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<timestamp>Tue</timestamp>\n"
                                "<user_query>\nfix the bug\n</user_query>"
                            ),
                        }
                    ]
                },
            },
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Looking into it."},
                        {
                            "type": "tool_use",
                            "id": "call1",
                            "name": "Read",
                            "input": {"path": "C:\\\\Users\\\\Test\\\\app\\\\main.py"},
                        },
                    ]
                },
            },
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call1",
                            "content": "print('hi')",
                        }
                    ]
                },
            },
        ],
    )

    session = load_session(path)
    assert session["agent"] == "cursor"
    assert "fix the bug" in session["title"]
    roles = [t["role"] for t in session["turns"]]
    assert "user" in roles
    assert "assistant" in roles
    assert "tool" in roles
    assert "tool_result" in roles
    user_turn = next(t for t in session["turns"] if t["role"] == "user")
    assert "fix the bug" in user_turn["text"]
    assert "<user_query>" not in user_turn["text"]


def test_discover_and_authorize_cursor(agent_homes: dict[str, Path]) -> None:
    home = agent_homes["cursor"]
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    path = _write_transcript(
        home,
        "proj-one",
        sid,
        [
            {
                "role": "user",
                "message": {
                    "content": [{"type": "text", "text": "<user_query>hello cursor</user_query>"}]
                },
            }
        ],
    )

    cards = discover_cursor()
    assert len(cards) == 1
    assert cards[0]["agent"] == "cursor"
    assert cards[0]["id"] == sid
    assert "hello cursor" in (cards[0].get("title") or "")

    authorized = resolve_session_path("cursor", str(path))
    assert authorized.path == path.resolve()
