from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_session_viewer.agents import claude
from agent_session_viewer.agents.claude import (
    claude_scan_session,
    claude_todos,
    load_session,
    load_subagent_records,
)
from agent_session_viewer.util import collect_parse_diagnostics

FIXTURES = Path(__file__).parent / "fixtures"
CLAUDE_SESSION = FIXTURES / "claude" / "session-fixture.jsonl"


def test_summary_uses_recorded_title_and_session_metadata() -> None:
    summary = claude_scan_session(CLAUDE_SESSION)["summary"]

    assert summary["title"] == "Fixture session title"
    assert summary["model"] == "claude-opus-5"
    assert summary["cwd"] == "C:/fixture"
    assert summary["head_branch"] == "main"
    assert summary["cli_version"] == "2.1.220"
    # permissionMode and effort reuse the panel slots Grok/Codex already render.
    assert summary["sandbox_profile"] == "acceptEdits"
    assert summary["reasoning_effort"] == "high"


def test_token_usage_folds_cache_reads_and_writes_into_input() -> None:
    tokens = claude_scan_session(CLAUDE_SESSION)["summary"]["tokens"]

    assert tokens["available"] is True
    # 100+40+60, then 10+0+200, then 5+0+300
    assert tokens["input"] == 715
    assert tokens["cached"] == 560
    assert tokens["output"] == 60
    assert tokens["uncached_input"] == 155
    assert tokens["total"] == 775
    assert tokens["source"] == "transcript · sum of message.usage"


def test_subagent_usage_is_included_and_broken_out_per_model() -> None:
    subagents = load_subagent_records(CLAUDE_SESSION)
    tokens = claude_scan_session(CLAUDE_SESSION, None, subagents)["summary"]["tokens"]

    rows = {row["model"]: row for row in tokens["by_model_rows"]}
    assert set(rows) == {"claude-opus-5", "claude-sonnet-5"}
    assert rows["claude-sonnet-5"]["model_calls"] == 1
    assert tokens["input"] == 715 + 60


def test_chat_counts_exclude_tool_result_only_records() -> None:
    counts = claude_scan_session(CLAUDE_SESSION)["summary"]["counts"]

    # Records carrying only a tool_result or tool_use are transport, and isMeta
    # records are injected reminders: two tool-result user records, one tool-use
    # assistant record, and one isMeta reminder are all excluded.
    assert counts["user"] == 1
    assert counts["assistant"] == 2
    assert counts["tool_call"] == 2
    assert counts["tool_result"] == 2
    assert counts["reasoning"] == 1


def test_edit_results_become_hunk_records() -> None:
    hunks = claude_scan_session(CLAUDE_SESSION)["hunks"]

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk["hunk_id"] == "toolu_edit1"
    assert hunk["file_path"] == "parser.py"
    assert hunk["source"] == "Edit"
    assert (hunk["added"], hunk["removed"]) == (2, 1)
    assert (hunk["start"], hunk["end"]) == (10, 13)


def test_read_results_do_not_become_hunk_records() -> None:
    assert all(h["source"] != "Read" for h in claude_scan_session(CLAUDE_SESSION)["hunks"])


def test_attachments_become_artifact_documents_and_events() -> None:
    scan = claude_scan_session(CLAUDE_SESSION)

    titles = [a["title"] for a in scan["artifacts"]]
    assert "Available skills" in titles
    assert any(e["meta"] == "attachment" for e in scan["events"])
    assert any(e["meta"] == "mode" for e in scan["events"])


def test_load_session_fills_every_panel() -> None:
    session = load_session(CLAUDE_SESSION)

    assert session["agent"] == "claude"
    assert session["title"] == "Fixture session title"
    assert session["summary"] is not None
    assert session["resources"] is not None
    assert session["artifacts"]
    assert session["hunks"]
    assert session["updates"]


def test_malformed_usage_degrades_without_aborting_the_scan(tmp_path: Path) -> None:
    records = [json.loads(line) for line in CLAUDE_SESSION.read_text(encoding="utf-8").splitlines()]
    for record in records:
        message = record.get("message")
        if isinstance(message, dict) and isinstance(message.get("usage"), dict):
            message["usage"]["input_tokens"] = {"unexpected": "object"}
    path = tmp_path / "broken-usage.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    with collect_parse_diagnostics() as diagnostics:
        summary = claude_scan_session(path)["summary"]

    assert summary["title"] == "Fixture session title"
    assert summary["tokens"]["available"] is True
    assert any(d["category"] == "invalid_number" for d in diagnostics)


def test_damaged_record_keeps_later_turns_and_summary(tmp_path: Path) -> None:
    lines = CLAUDE_SESSION.read_text(encoding="utf-8").splitlines()
    lines.insert(5, "{not valid json")
    path = tmp_path / "damaged.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")

    with collect_parse_diagnostics() as diagnostics:
        session = load_session(path)

    assert any(d["category"] == "invalid_json" for d in diagnostics)
    assert session["summary"]["title"] == "Fixture session title"
    assert any(turn["text"] == "The parser looks good." for turn in session["turns"])


def test_todos_are_read_from_the_claude_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    todos_dir = tmp_path / "todos"
    todos_dir.mkdir(parents=True)
    (todos_dir / "session-fixture-agent-session-fixture.json").write_text(
        json.dumps(
            [
                {"content": "Finish the parser", "status": "in_progress"},
                {"content": "Write the tests", "status": "pending"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(claude.config, "CLAUDE_HOME", tmp_path)

    todos = claude_todos("session-fixture")

    assert [t["content"] for t in todos] == ["Finish the parser", "Write the tests"]
    assert todos[0]["status"] == "in_progress"


def test_todos_tolerate_a_missing_or_damaged_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude.config, "CLAUDE_HOME", tmp_path)
    assert claude_todos("session-fixture") == []

    todos_dir = tmp_path / "todos"
    todos_dir.mkdir(parents=True)
    (todos_dir / "session-fixture-agent-x.json").write_text("{not json", encoding="utf-8")
    assert claude_todos("session-fixture") == []


def test_prompt_history_is_filtered_by_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "history.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"display": "mine", "sessionId": "session-fixture", "timestamp": 0}),
                json.dumps({"display": "other", "sessionId": "another", "timestamp": 0}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(claude.config, "CLAUDE_HOME", tmp_path)

    rows = claude.claude_prompt_history("session-fixture")

    assert [row["display"] for row in rows] == ["mine"]


def test_subagent_transcript_loads_as_its_own_session() -> None:
    path = FIXTURES / "claude" / "session-fixture" / "subagents" / "agent-fixture1.jsonl"

    session = load_session(path)

    assert claude.is_subagent_path(path) is True
    assert claude.claude_session_id(path) == "session-fixture"
    assert session["title"] == "subagent fixture1"
    # A subagent file has no nested subagents of its own.
    assert load_subagent_records(path) == {}
