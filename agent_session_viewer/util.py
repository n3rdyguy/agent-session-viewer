"""Small formatting and filesystem helpers shared across the viewer."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import CLAUDE_HOME, CODEX_HOME, GROK_HOME


def human_time(ts: str | float | int | None) -> str:
    if ts is None or ts == "":
        return ""
    try:
        if isinstance(ts, (int, float)):
            # Grok updates use unix seconds; reject absurd values
            if ts > 1e12:  # ms
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        return (
            datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S")
        )
    except Exception:
        return str(ts)[:19]


def display_time(ts: str | float | int | None, fallback_id: str | None = None) -> str:
    """Prefer a real timestamp; otherwise show an id so the UI never shows bare '?'."""
    ht = human_time(ts)
    if ht:
        return ht
    if fallback_id:
        return str(fallback_id)
    return ""


def truncate(s: str, n: int = 140) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def format_tokens(n: int | float | None) -> str:
    if n is None:
        return "—"
    try:
        n = int(n)
    except Exception:
        return str(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M".rstrip("0").rstrip(".") + f" ({n:,})"
    return f"{n:,}"


def pretty_json(obj: Any, max_len: int = 12000) -> str:
    try:
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except Exception:
                return obj
        text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def path_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    roots = [GROK_HOME, CLAUDE_HOME, CODEX_HOME]
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except Exception:
            # Fallback for odd Windows path forms
            rs, rr = str(resolved).lower().replace("\\", "/"), str(root.resolve()).lower().replace("\\", "/")
            if rs == rr or rs.startswith(rr + "/"):
                return True
    return False


