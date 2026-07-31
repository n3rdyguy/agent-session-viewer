from pathlib import Path

import pytest

from agent_session_viewer.agents.claude import get_claude_conversation, load_subagent_records
from agent_session_viewer.agents.codex import get_codex_conversation
from agent_session_viewer.agents.grok import get_grok_conversation

FIXTURES = Path(__file__).parent / "fixtures"
CLAUDE_SESSION = FIXTURES / "claude" / "session-fixture.jsonl"


def test_grok_fixture_turn_roles_and_ids() -> None:
    turns = get_grok_conversation(FIXTURES / "grok")

    assert [turn["role"] for turn in turns] == [
        "user",
        "assistant",
        "tool_call",
        "tool_result",
        "reasoning",
    ]
    assert [turn["id"] for turn in turns] == [
        "prompt:7",
        "assistant-1",
        "call-1",
        "call-1",
        "reason-1",
    ]
    assert all(turn["time"] for turn in turns)


def test_codex_fixture_turn_roles_and_ids() -> None:
    turns = get_codex_conversation(FIXTURES / "codex" / "rollout-test.jsonl")

    assert [turn["role"] for turn in turns] == [
        "system",
        "user",
        "reasoning",
        "tool_call",
        "tool_result",
        "assistant",
    ]
    assert [turn["id"] for turn in turns] == [
        "#2",
        "#3",
        "reason-1",
        "call-1",
        "call-1",
        "#7",
    ]
    assert all(turn["time"] for turn in turns)


def test_codex_chat_omits_task_lifecycle_events(tmp_path: Path) -> None:
    """task_started/complete belong on the Events timeline, not Chat history."""
    from agent_session_viewer.session import load_session

    rollout = tmp_path / "rollout-codex-task-event.jsonl"
    rollout.write_text(
        "\n".join(
            [
                '{"timestamp":"2026-07-30T08:00:00Z","type":"session_meta","payload":{"id":"codex-task","cwd":"C:/p"}}',
                '{"timestamp":"2026-07-30T08:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"t1","model_context_window":200000}}',
                '{"timestamp":"2026-07-30T08:00:02Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}',
                '{"timestamp":"2026-07-30T08:00:03Z","type":"event_msg","payload":{"type":"agent_message","message":"hi"}}',
                '{"timestamp":"2026-07-30T08:00:04Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"t1","duration_ms":10}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = load_session("codex", rollout)
    assert [t["role"] for t in session["turns"]] == ["user", "assistant"]
    assert all(t["role"] != "event" for t in session["turns"])
    assert any(
        t["role"] == "event" and "task_started" in (t.get("text") or "")
        for t in (session.get("updates") or [])
    )


def test_claude_fixture_turn_roles_and_tool_call_ids() -> None:
    subagents = load_subagent_records(CLAUDE_SESSION)
    turns = get_claude_conversation(CLAUDE_SESSION, subagents=subagents)

    assert [turn["role"] for turn in turns] == [
        "user",
        "reasoning",
        "assistant",
        "tool_call",
        "tool_result",
        "user",
        "assistant",
        "tool_call",
        "tool_result",
        "system",
        "system_reminder",
        "system",
        "assistant",
    ]
    # Tool results carry the id of the call they answer, as Grok and Codex do.
    calls = [t for t in turns if t["role"] in ("tool_call", "tool_result")]
    assert [t["id"] for t in calls] == [
        "toolu_read1",
        "toolu_read1",
        "toolu_edit1",
        "toolu_edit1",
    ]
    assert all(turn["time"] for turn in turns)


def test_claude_thinking_without_text_is_marked_encrypted() -> None:
    turns = get_claude_conversation(CLAUDE_SESSION)
    reasoning = [t for t in turns if t["role"] == "reasoning"]

    assert len(reasoning) == 1
    assert reasoning[0]["text"] == "<encrypted>"


def test_claude_subagent_turns_are_inlined_chronologically_and_tagged() -> None:
    subagents = load_subagent_records(CLAUDE_SESSION)
    turns = get_claude_conversation(CLAUDE_SESSION, subagents=subagents)

    tagged = [t for t in turns if "subagent" in t["meta"]]
    assert [t["meta"] for t in tagged] == ["subagent: Explore", "subagent: Explore"]
    # The subagent ran between the first tool result and the following edit call.
    roles = [t["role"] for t in turns]
    assert roles.index("user") < roles.index("tool_call")
    assert turns.index(tagged[0]) > roles.index("tool_result")


def test_claude_conversation_without_subagents_omits_sidechain_turns() -> None:
    turns = get_claude_conversation(CLAUDE_SESSION)

    assert all("subagent" not in turn["meta"] for turn in turns)


def test_claude_injected_reminders_are_not_shown_as_user_messages() -> None:
    """isMeta user records are injected context, not something the user typed."""
    turns = get_claude_conversation(CLAUDE_SESSION)

    reminders = [t for t in turns if t["role"] == "system_reminder"]
    assert len(reminders) == 1
    assert "Injected reminder text." in reminders[0]["text"]
    assert all("Injected reminder text." not in t["text"] for t in turns if t["role"] == "user")


def test_claude_attachments_render_as_system_turns() -> None:
    turns = get_claude_conversation(CLAUDE_SESSION)
    kinds = [t["meta"] for t in turns if t["role"] == "system"]

    assert "plan_mode" in kinds
    assert "local_command" in kinds


@pytest.mark.parametrize("kind", ["task_reminder", "deferred_tools_delta", "skill_listing"])
def test_claude_bookkeeping_attachments_stay_out_of_the_chat(kind: str) -> None:
    """Empty reminders, tool-registry deltas, and listings would bury the conversation."""
    turns = get_claude_conversation(CLAUDE_SESSION)

    assert all(turn["meta"] != kind for turn in turns)
