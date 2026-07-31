"""Group session cards into per-project buckets derived from cwd."""

from __future__ import annotations

from typing import TypedDict

from .discovery import session_sort_key
from .types import SessionCard

NO_PROJECT_NAME = "(no project)"


class ProjectGroup(TypedDict):
    key: str  # normalized casefolded path; "" for the no-project bucket
    name: str  # last path segment, or "(no project)"
    cwd: str  # first-seen raw path for display; "" for the no-project bucket
    sessions: list[SessionCard]
    latest: float
    count: int


def normalize_cwd(cwd: object) -> str:
    """Grouping key for a card's cwd: slashes unified, trailing slash and case ignored.

    Returns "" for anything that does not name a directory, including the
    Codex "?" placeholder.
    """
    if not isinstance(cwd, str):
        return ""
    text = cwd.strip()
    if text in ("", "?"):
        return ""
    return text.replace("\\", "/").rstrip("/").casefold()


def _project_name(cwd: str) -> str:
    text = cwd.replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] or text


def group_by_project(sessions: list[SessionCard]) -> list[ProjectGroup]:
    """Bucket cards by normalized cwd; groups and their sessions newest-first."""
    groups: dict[str, ProjectGroup] = {}
    for card in sessions:
        key = normalize_cwd(card.get("cwd"))
        group = groups.get(key)
        if group is None:
            raw_cwd = str(card.get("cwd") or "").strip() if key else ""
            group = groups[key] = {
                "key": key,
                "name": _project_name(raw_cwd) if key else NO_PROJECT_NAME,
                "cwd": raw_cwd,
                "sessions": [],
                "latest": 0.0,
                "count": 0,
            }
        group["sessions"].append(card)
    for group in groups.values():
        group["sessions"].sort(key=session_sort_key, reverse=True)
        group["latest"] = session_sort_key(group["sessions"][0])
        group["count"] = len(group["sessions"])
    return sorted(groups.values(), key=lambda g: g["latest"], reverse=True)
