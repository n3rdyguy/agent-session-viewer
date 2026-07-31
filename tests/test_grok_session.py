"""Grok todos (resources_state + todo_write) and prompt history."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_session_viewer.agents import grok

FIXTURES = Path(__file__).parent / "fixtures"
GROK_FIXTURE = FIXTURES / "grok"


def _write_session(
    root: Path,
    *,
    session_id: str = "sess-1",
    resources: dict | None = None,
    chat: list[dict] | None = None,
    prompts: list[dict] | None = None,
    summary: dict | None = None,
) -> Path:
    project = root / "project"
    session = project / session_id
    session.mkdir(parents=True)
    if summary is None:
        summary = {
            "info": {"id": session_id, "cwd": "C:/project"},
            "generated_title": "Test session",
        }
    (session / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    if resources is not None:
        (session / "resources_state.json").write_text(json.dumps(resources), encoding="utf-8")
    if chat is not None:
        (session / "chat_history.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in chat),
            encoding="utf-8",
        )
    if prompts is not None:
        (project / "prompt_history.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in prompts),
            encoding="utf-8",
        )
    return session


def test_todos_from_resources_state_dict_form(tmp_path: Path) -> None:
    session = _write_session(
        tmp_path,
        resources={
            "state": {
                "grok_build.Todo": {
                    "todos": {
                        "a": {"content": "First", "status": "in_progress", "priority": "high"},
                        "b": {"content": "Second", "status": "pending", "priority": "low"},
                    }
                }
            }
        },
    )

    todos = grok.grok_resources(session)["todos"]
    assert [(t["id"], t["content"], t["status"], t["priority"]) for t in todos] == [
        ("a", "First", "in_progress", "high"),
        ("b", "Second", "pending", "low"),
    ]


def test_todos_from_resources_state_list_and_legacy_keys(tmp_path: Path) -> None:
    session = _write_session(
        tmp_path,
        resources={
            "state": {
                "Todo": {
                    "todos": [
                        {"id": "x", "step": "Legacy step", "status": "completed"},
                        {"content": "No id item", "status": "pending"},
                    ]
                }
            }
        },
    )

    todos = grok.grok_resources(session)["todos"]
    assert [(t["id"], t["content"], t["status"]) for t in todos] == [
        ("2", "No id item", "pending"),
        ("x", "Legacy step", "completed"),
    ]


def test_todo_write_chat_fallback_supports_merge(tmp_path: Path) -> None:
    session = _write_session(
        tmp_path,
        chat=[
            {
                "type": "assistant",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "todo_write",
                        "arguments": json.dumps(
                            {
                                "merge": False,
                                "todos": [
                                    {"id": "1", "content": "Alpha", "status": "in_progress"},
                                    {"id": "2", "content": "Beta", "status": "pending"},
                                ],
                            }
                        ),
                    }
                ],
            },
            {
                "type": "assistant",
                "tool_calls": [
                    {
                        "id": "c2",
                        "name": "todo_write",
                        "arguments": json.dumps(
                            {
                                "merge": True,
                                "todos": [
                                    {"id": "1", "status": "completed"},
                                    {"id": "2", "status": "in_progress"},
                                ],
                            }
                        ),
                    }
                ],
            },
        ],
    )

    todos = grok.grok_resources(session)["todos"]
    assert [(t["id"], t["content"], t["status"]) for t in todos] == [
        ("2", "Beta", "in_progress"),
        ("1", "Alpha", "completed"),
    ]


def test_resources_state_takes_precedence_over_chat(tmp_path: Path) -> None:
    session = _write_session(
        tmp_path,
        resources={
            "state": {
                "grok_build.Todo": {
                    "todos": {"only": {"content": "From resources", "status": "pending"}}
                }
            }
        },
        chat=[
            {
                "type": "assistant",
                "tool_calls": [
                    {
                        "name": "todo_write",
                        "arguments": json.dumps(
                            {
                                "todos": [
                                    {"id": "chat", "content": "From chat", "status": "pending"}
                                ]
                            }
                        ),
                    }
                ],
            }
        ],
    )

    todos = grok.grok_resources(session)["todos"]
    assert [t["content"] for t in todos] == ["From resources"]


def test_prompt_history_is_filtered_by_session(tmp_path: Path) -> None:
    session = _write_session(
        tmp_path,
        session_id="sess-mine",
        prompts=[
            {
                "timestamp": "2026-07-30T08:00:00Z",
                "session_id": "sess-mine",
                "prompt": "mine",
                "is_bash": False,
            },
            {
                "timestamp": "2026-07-30T08:01:00Z",
                "session_id": "sess-other",
                "prompt": "other",
                "is_bash": False,
            },
            {
                "timestamp": "2026-07-30T08:02:00Z",
                "session_id": "sess-mine",
                "prompt": "ls",
                "is_bash": True,
            },
        ],
    )

    rows = grok.grok_prompt_history(session, "sess-mine")
    assert [r["display"] for r in rows] == ["mine", "$ ls"]

    artifacts = grok.grok_resources(session)["artifacts"]
    history = next(a for a in artifacts if a["id"] == "prompt-history")
    assert "Prompt history" == history["title"]
    assert "mine" in history["text"]
    assert "other" not in history["text"]
    assert "$ ls" in history["text"]


def test_fixture_conversation_still_loads() -> None:
    turns = grok.get_grok_conversation(GROK_FIXTURE)
    assert turns
    assert turns[0]["role"] == "user"
