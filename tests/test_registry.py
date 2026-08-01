"""Drift guards for the agent registry.

The registry removes the per-agent if/elif chains, but a few things still have to
be listed by hand - a typing Literal, the discovery table, the loader table, and
one CSS rule. These tests fail loudly when one of them falls out of step.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from agent_session_viewer import authorization, discovery, session, util
from agent_session_viewer.agents.loaders import LOADERS
from agent_session_viewer.registry import AGENT_IDS, AGENT_SPECS


def test_agent_literal_matches_the_registry() -> None:
    """``authorization.Agent`` cannot be derived from the dict at type-check time."""
    assert set(get_args(authorization.Agent)) == set(AGENT_SPECS)
    assert AGENT_IDS == frozenset(AGENT_SPECS)


def test_every_spec_is_keyed_by_its_own_id() -> None:
    for key, spec in AGENT_SPECS.items():
        assert key == spec.id


@pytest.mark.parametrize("agent", sorted(AGENT_SPECS))
def test_every_agent_has_a_loader_and_a_discoverer(agent: str) -> None:
    assert callable(LOADERS[agent])
    assert callable(discovery._DISCOVERERS[agent])


def test_no_orphan_loaders_or_discoverers() -> None:
    assert set(LOADERS) == set(AGENT_SPECS)
    assert set(discovery._DISCOVERERS) == set(AGENT_SPECS)


@pytest.mark.parametrize("agent", sorted(AGENT_SPECS))
def test_path_allowed_covers_every_agent_home(agent: str, agent_homes: dict[str, Path]) -> None:
    spec = AGENT_SPECS[agent]
    assert util.path_allowed(spec.home())
    assert util.path_allowed(spec.looking_in() / "nested" / "session.jsonl")


@pytest.mark.parametrize("agent", sorted(AGENT_SPECS))
def test_homes_resolve_through_config_at_call_time(
    agent: str, agent_homes: dict[str, Path]
) -> None:
    """Reading homes lazily is what lets a single patch point redirect every module."""
    assert AGENT_SPECS[agent].home() == agent_homes[agent]


@pytest.mark.parametrize("agent", sorted(AGENT_SPECS))
def test_session_roots_are_created_under_the_home(agent: str, agent_homes: dict[str, Path]) -> None:
    spec = AGENT_SPECS[agent]
    assert spec.roots()
    assert spec.looking_in() == spec.roots()[0]
    for root in spec.roots():
        assert root.parent == agent_homes[agent]


@pytest.mark.parametrize("agent", sorted(AGENT_SPECS))
def test_summary_fields_render_without_a_summary_payload(agent: str) -> None:
    """A sparse summary must not raise; missing values fall back to a dash."""
    rendered = session.summary_to_markdown({"id": "abc"}, agent=agent)
    assert "**Model:** -  " in rendered
    assert "**Session id:** `abc`  " in rendered
    for field in AGENT_SPECS[agent].summary_fields:
        assert f"**{field.label}:**" in rendered


def test_summary_to_markdown_ignores_unknown_agents() -> None:
    assert session.summary_to_markdown({"id": "abc"}, agent="nope") == ""


def test_unknown_agent_loads_an_empty_session(tmp_path: Path) -> None:
    """Routes validate the agent first; loading must still degrade rather than raise."""
    loaded = session.load_session("nope", tmp_path / "session.jsonl")
    assert loaded["turns"] == []
    assert loaded["summary"] is None
    assert loaded["title"] == "session.jsonl"


def test_every_agent_has_a_badge_rule_in_the_stylesheet() -> None:
    """Badge colours cannot come from the registry under style-src 'self'."""
    css = (Path(util.__file__).parent / "static" / "app.css").read_text(encoding="utf-8")
    for agent in AGENT_SPECS:
        assert f".badge.{agent}" in css


def test_index_lists_a_filter_link_per_agent(client) -> None:
    body = client.get("/").get_data(as_text=True)
    for spec in AGENT_SPECS.values():
        assert f"agent={spec.id}" in body
        assert f">{spec.label}</a>" in body
