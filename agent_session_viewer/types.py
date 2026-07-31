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
    diagnostics: list[ParseDiagnostic]
