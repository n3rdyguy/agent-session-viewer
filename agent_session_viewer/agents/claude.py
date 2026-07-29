"""Claude Code session loading."""

from __future__ import annotations

from pathlib import Path

from ..images import extract_text
from ..turns import make_turn
from ..util import display_time, iter_jsonl


def get_conversation(path: Path) -> list[dict]:
    turns = []
    for obj in iter_jsonl(path):
        if obj.get("type") not in ("user", "assistant"):
            continue
        msg = obj.get("message") or {}
        role = (msg.get("role") or obj.get("type")).lower()
        text = extract_text(msg.get("content"))
        if text.strip():
            turns.append(
                make_turn(
                    role=role,
                    time=display_time(obj.get("timestamp")),
                    text=text,
                    model=msg.get("model", "") or "",
                )
            )
    return turns


def load_session(path: Path) -> dict:
    """Return the common session shape; Claude's richer extras remain WIP."""
    return {
        "agent": "claude",
        "path": path,
        "title": path.name,
        "turns": get_conversation(path),
        "summary": None,
        "resources": None,
        "artifacts": [],
        "hunks": None,
        "terminal_logs": None,
        "recaps": None,
        "updates": None,
    }
