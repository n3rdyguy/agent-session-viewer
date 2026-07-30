import json
from pathlib import Path

from agent_session_viewer.agents.grok import grok_token_usage
from agent_session_viewer.session import load_session
from agent_session_viewer.util import collect_parse_diagnostics, iter_jsonl


def test_iter_jsonl_skips_bad_and_non_object_records_without_truncating(tmp_path: Path):
    path = tmp_path / "damaged.jsonl"
    secret = "do-not-echo-this-secret"
    path.write_text(
        "\n".join(
            [
                "",
                json.dumps({"id": 1}),
                f'{{broken "{secret}"',
                json.dumps(["not", "an", "object"]),
                "null",
                json.dumps({"id": 2}),
            ]
        ),
        encoding="utf-8",
    )

    with collect_parse_diagnostics() as diagnostics:
        records = list(iter_jsonl(path))

    assert records == [{"id": 1}, {"id": 2}]
    assert [item["category"] for item in diagnostics] == [
        "invalid_json",
        "non_object",
        "non_object",
    ]
    assert [item["line"] for item in diagnostics] == [3, 4, 5]
    assert all(len(item["message"]) <= 160 for item in diagnostics)
    assert secret not in repr(diagnostics)


def test_claude_session_keeps_records_after_damage_and_reports_diagnostics(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"role": "user", "content": "before"}}),
                "{damaged",
                json.dumps(42),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"role": "assistant", "content": "after"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    session = load_session("claude", path)

    assert [turn["text"] for turn in session["turns"]] == ["before", "after"]
    assert [item["category"] for item in session["diagnostics"]] == [
        "invalid_json",
        "non_object",
    ]


def test_clean_session_has_no_diagnostics(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "clean"}}),
        encoding="utf-8",
    )

    assert load_session("claude", path)["diagnostics"] == []


def test_view_shows_bounded_skipped_record_warning(client, agent_homes):
    path = agent_homes["claude"] / "projects" / "damaged" / "session.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "{bad secret-value-that-must-not-appear",
                json.dumps({"type": "user", "message": {"role": "user", "content": "survives"}}),
            ]
        ),
        encoding="utf-8",
    )

    response = client.get("/view", query_string={"agent": "claude", "path": str(path)})

    assert response.status_code == 200
    assert b"1 record skipped" in response.data
    assert b"survives" in response.data
    assert b"secret-value-that-must-not-appear" not in response.data


def test_invalid_codex_token_numbers_warn_without_losing_later_records(tmp_path: Path):
    path = tmp_path / "rollout-damaged.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "model_context_window": {"secret": "hidden"},
                                "last_token_usage": {
                                    "input_tokens": "not-a-number",
                                    "total_tokens": ["wrong"],
                                },
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "still here"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    session = load_session("codex", path)

    assert any(turn["text"] == "still here" for turn in session["turns"])
    assert {item["category"] for item in session["diagnostics"]} == {"invalid_number"}
    assert "hidden" not in repr(session["diagnostics"])


def test_invalid_grok_token_numbers_warn_and_keep_valid_values(tmp_path: Path):
    updates = tmp_path / "updates.jsonl"
    updates.write_text(
        json.dumps(
            {
                "params": {
                    "update": {
                        "sessionUpdate": "turn_completed",
                        "usage": {"inputTokens": "bad", "outputTokens": "7"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with collect_parse_diagnostics() as diagnostics:
        usage = grok_token_usage(tmp_path)

    assert usage["input"] == 0
    assert usage["output"] == 7
    assert [item["category"] for item in diagnostics] == ["invalid_number"]


def test_jsonl_read_error_is_reported_without_raising(tmp_path: Path, monkeypatch):
    path = tmp_path / "unreadable.jsonl"
    path.write_text("{}", encoding="utf-8")
    original_open = Path.open

    def fail_target_open(self, *args, **kwargs):
        if self == path:
            raise PermissionError("sensitive operating system detail")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_target_open)
    with collect_parse_diagnostics() as diagnostics:
        assert list(iter_jsonl(path)) == []

    assert diagnostics == [
        {
            "path": str(path),
            "line": None,
            "category": "io_error",
            "message": "PermissionError",
        }
    ]


def test_diagnostics_are_count_bounded(tmp_path: Path):
    path = tmp_path / "many-errors.jsonl"
    path.write_text("\n".join("{bad" for _ in range(150)), encoding="utf-8")

    with collect_parse_diagnostics() as diagnostics:
        assert list(iter_jsonl(path)) == []

    assert len(diagnostics) == 100


def test_wrong_record_fields_do_not_truncate_grok_transcript(tmp_path: Path):
    history = tmp_path / "chat_history.jsonl"
    history.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": ["unexpected"],
                        "timestamp": {"invalid": "timestamp"},
                        "tool_calls": "not-a-list",
                    }
                ),
                json.dumps({"type": "assistant", "content": "later message"}),
            ]
        ),
        encoding="utf-8",
    )

    session = load_session("grok", tmp_path)

    assert any(turn["text"] == "later message" for turn in session["turns"])


def test_file_change_during_iteration_does_not_crash(tmp_path: Path):
    path = tmp_path / "changing.jsonl"
    path.write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    records = iter_jsonl(path)

    assert next(records) == {"id": 1}
    path.write_text('{"id": 3}\n', encoding="utf-8")

    # Buffered readers may retain the old second line or observe EOF after replacement.
    assert list(records) in ([], [{"id": 2}])


def test_parser_failure_is_per_record_and_later_updates_survive(tmp_path: Path):
    updates = tmp_path / "updates.jsonl"
    updates.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "params": {
                            "update": {
                                "sessionUpdate": "tool_call",
                                "toolCallId": ["unhashable"],
                            }
                        }
                    }
                ),
                json.dumps(
                    {
                        "params": {
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": "after failure",
                            }
                        }
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    session = load_session("grok", tmp_path)

    assert any(turn["text"] == "after failure" for turn in session["turns"])
    assert any(item["category"] == "invalid_record" for item in session["diagnostics"])
    assert "unhashable" not in repr(session["diagnostics"])
