"""Codex update_plan → resources.todos (classic JSON + newer exec formats)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_session_viewer.agents import codex

FIXTURES = Path(__file__).parent / "fixtures"
CODEX_FIXTURE = FIXTURES / "codex" / "rollout-test.jsonl"


def _write_rollout(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _meta(session_id: str = "codex-todo-test") -> dict:
    return {
        "timestamp": "2026-07-30T08:00:00Z",
        "type": "session_meta",
        "payload": {"id": session_id, "cwd": "C:/fixture"},
    }


def test_fixture_without_plan_has_empty_todos() -> None:
    resources = codex.codex_scan_session(CODEX_FIXTURE)["resources"]
    assert resources["todos"] == []


def test_classic_function_call_update_plan_json_arguments(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    _write_rollout(
        rollout,
        [
            _meta(),
            {
                "timestamp": "2026-07-30T08:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "update_plan",
                    "call_id": "call-plan-1",
                    "arguments": json.dumps(
                        {
                            "explanation": "Starting implementation",
                            "plan": [
                                {"step": "Inspect the parser", "status": "in_progress"},
                                {"step": "Patch the parser", "status": "pending"},
                            ],
                        }
                    ),
                },
            },
        ],
    )

    todos = codex.codex_scan_session(rollout)["resources"]["todos"]

    assert [(t["id"], t["content"], t["status"]) for t in todos] == [
        ("1", "Inspect the parser", "in_progress"),
        ("2", "Patch the parser", "pending"),
    ]
    assert todos[0]["priority"] == "Starting implementation"
    assert todos[1]["priority"] == ""


def test_newer_exec_custom_tool_call_tools_update_plan(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    script = (
        'const r = await tools.update_plan({explanation:"Backend done",'
        'plan:[{step:"Map routes",status:"completed"},'
        '{step:"Wire UI",status:"in_progress"},'
        '{step:"Verify",status:"pending"}]});\n'
        'text(typeof r === "string" ? r : JSON.stringify(r));\n'
    )
    _write_rollout(
        rollout,
        [
            _meta(),
            {
                "timestamp": "2026-07-30T08:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "call-plan-exec",
                    "input": script,
                },
            },
        ],
    )

    todos = codex.codex_scan_session(rollout)["resources"]["todos"]

    assert [(t["content"], t["status"]) for t in todos] == [
        ("Map routes", "completed"),
        ("Wire UI", "in_progress"),
        ("Verify", "pending"),
    ]
    assert todos[0]["priority"] == "Backend done"


def test_last_non_empty_plan_wins_across_formats(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    _write_rollout(
        rollout,
        [
            _meta(),
            {
                "timestamp": "2026-07-30T08:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "update_plan",
                    "arguments": json.dumps(
                        {
                            "plan": [
                                {"step": "Old step A", "status": "completed"},
                                {"step": "Old step B", "status": "pending"},
                            ]
                        }
                    ),
                },
            },
            {
                "timestamp": "2026-07-30T08:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": (
                        "const r = await tools.update_plan({plan:["
                        '{step:"New only",status:"in_progress"}]});'
                    ),
                },
            },
        ],
    )

    todos = codex.codex_scan_session(rollout)["resources"]["todos"]
    assert [(t["content"], t["status"]) for t in todos] == [("New only", "in_progress")]


def test_empty_plan_does_not_wipe_previous(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    _write_rollout(
        rollout,
        [
            _meta(),
            {
                "timestamp": "2026-07-30T08:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "update_plan",
                    "arguments": json.dumps({"plan": [{"step": "Keep me", "status": "pending"}]}),
                },
            },
            {
                "timestamp": "2026-07-30T08:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "update_plan",
                    "arguments": json.dumps({"plan": []}),
                },
            },
        ],
    )

    todos = codex.codex_scan_session(rollout)["resources"]["todos"]
    assert [t["content"] for t in todos] == ["Keep me"]


def test_update_plan_named_custom_tool_and_content_aliases(tmp_path: Path) -> None:
    """Defensive: custom_tool_call named update_plan, and content/text aliases."""
    rollout = tmp_path / "rollout.jsonl"
    _write_rollout(
        rollout,
        [
            _meta(),
            {
                "timestamp": "2026-07-30T08:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "update_plan",
                    "input": json.dumps(
                        {
                            "todos": [
                                {"content": "Alias content", "status": "pending"},
                                {"text": "Alias text", "status": "completed"},
                            ]
                        }
                    ),
                },
            },
        ],
    )

    todos = codex.codex_scan_session(rollout)["resources"]["todos"]
    assert [(t["content"], t["status"]) for t in todos] == [
        ("Alias content", "pending"),
        ("Alias text", "completed"),
    ]


def test_parse_helpers_tolerate_garbage() -> None:
    assert codex.parse_update_plan_blob(None) is None
    assert codex.parse_update_plan_blob("") is None
    assert codex.parse_update_plan_blob("{not json") is None
    assert codex.parse_update_plan_blob("const r = await tools.shell({cmd:'x'});") is None
    assert codex.extract_update_plan_todos({"type": "message", "role": "user"}) is None
    assert (
        codex.extract_update_plan_todos(
            {"type": "function_call", "name": "shell_command", "arguments": "{}"}
        )
        is None
    )


def test_prompt_history_is_filtered_by_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "history.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"session_id": "codex-todo-test", "ts": 1785076791, "text": "mine"}),
                json.dumps({"session_id": "other", "ts": 1785077005, "text": "other"}),
                json.dumps(
                    {
                        "session_id": "codex-todo-test",
                        "timestamp": "2026-07-30T08:00:00Z",
                        "prompt": "also mine",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(codex.config, "CODEX_HOME", codex_home)

    rows = codex.codex_prompt_history("codex-todo-test")
    assert [r["display"] for r in rows] == ["mine", "also mine"]
    assert rows[0]["time"]


def test_prompt_history_attached_to_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "history.jsonl").write_text(
        json.dumps({"session_id": "codex-todo-test", "ts": 1785076791, "text": "hello plan"})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(codex.config, "CODEX_HOME", codex_home)
    monkeypatch.setattr(codex, "load_codex_session_index", lambda: {})

    rollout = tmp_path / "rollout-codex-todo-test.jsonl"
    _write_rollout(
        rollout,
        [
            _meta("codex-todo-test"),
            {
                "timestamp": "2026-07-30T08:00:01Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hello plan"},
            },
        ],
    )

    scan = codex.codex_scan_session(rollout)
    history = scan["resources"]["prompt_history"]
    assert [r["display"] for r in history] == ["hello plan"]
    assert not any(a.get("id") == "prompt-history" for a in scan.get("artifacts") or [])
