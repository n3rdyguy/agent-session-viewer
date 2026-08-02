"""Shared data shapes passed between parsers, routes, and templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict


class ImageInfo(TypedDict, total=False):
    kind: str
    path: str
    url: str
    label: str
    mime: str
    data_url: str


class Turn(TypedDict, total=False):
    role: str
    time: str
    id: str
    text: str
    model: str
    meta: str
    images: list[ImageInfo]
    html: str
    # Nested file-read cards inside tool_result (from shell file-pull commands)
    file_artifacts: list[dict[str, Any]]
    # Non-file tool_result text that belongs above the file cards (e.g. Exit code)
    file_read_prefix: str


class ParseDiagnostic(TypedDict):
    path: str
    line: int | None
    category: str
    message: str


class SessionCard(TypedDict, total=False):
    agent: str
    id: str
    path: str
    cwd: str
    title: str
    headline: str
    aborted: bool
    created: Any
    updated: Any
    model: Any
    messages: Any


class SubagentSession(TypedDict, total=False):
    """One child agent run attached to a parent session."""

    id: str
    name: str
    path: str
    model: str
    status: str
    description: str
    subagent_type: str
    turns: list[Turn]
    messages: int
    # When the child is itself a browsable session path (Grok sibling dirs).
    view_path: str
    view_agent: str


class SessionData(TypedDict, total=False):
    agent: str
    path: Path
    title: str
    turns: list[Turn]
    summary: dict[str, Any] | None
    resources: dict[str, Any] | None
    artifacts: list[dict[str, Any]] | None
    system_artifacts: list[dict[str, Any]] | None
    hunks: list[dict[str, Any]] | None
    terminal_logs: list[dict[str, Any]] | None
    recaps: list[dict[str, Any]] | None
    updates: list[dict[str, Any]] | None
    subagents: list[SubagentSession] | None
    diagnostics: list[ParseDiagnostic]


def empty_session(agent: str, path: Path) -> SessionData:
    """The shape an agent loader returns for a path it does not recognize.

    Routes only reach a loader with an authorized path, so this is a defensive
    default rather than an expected result.
    """
    return {
        "agent": agent,
        "path": path,
        "title": path.name,
        "turns": [],
        "summary": None,
        "resources": None,
        "artifacts": None,
        "system_artifacts": None,
        "hunks": None,
        "terminal_logs": None,
        "recaps": None,
        "updates": None,
        "subagents": None,
    }
