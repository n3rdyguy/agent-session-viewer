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
