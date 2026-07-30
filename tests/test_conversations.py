from pathlib import Path

from agent_session_viewer.agents.codex import get_codex_conversation
from agent_session_viewer.agents.grok import get_grok_conversation

FIXTURES = Path(__file__).parent / "fixtures"


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
