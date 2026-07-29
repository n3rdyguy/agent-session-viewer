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
        return ""


def display_time(ts: str | float | int | None) -> str:
    """Format a timestamp for display; ids belong in a turn's ``id`` field."""
    return human_time(ts)


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


def empty_token_usage() -> dict:
    return {
        "input": 0,
        "output": 0,
        "total": 0,
        "cached": 0,
        "reasoning": 0,
        "model_calls": 0,
        "api_duration_ms": 0,
        "turns": 0,
        "uncached_input": 0,
        "by_model": {},
        "by_model_rows": [],
        "context_used": None,
        "context_window": None,
        "context_pct": None,
        "available": False,
        "input_fmt": "—",
        "output_fmt": "—",
        "total_fmt": "—",
        "cached_fmt": "—",
        "reasoning_fmt": "—",
        "uncached_fmt": "—",
        "source": "",
        "bar": {"uncached_pct": 0, "cached_pct": 0, "out_pct": 0, "reason_pct": 0},
    }


def finalize_token_usage(usage: dict) -> dict:
    if usage["turns"] > 0 or usage["input"] or usage["output"]:
        usage["available"] = True
        usage["uncached_input"] = max(0, usage["input"] - usage["cached"])
        usage["input_fmt"] = format_tokens(usage["input"])
        usage["output_fmt"] = format_tokens(usage["output"])
        usage["total_fmt"] = format_tokens(usage["total"] or (usage["input"] + usage["output"]))
        usage["cached_fmt"] = format_tokens(usage["cached"])
        usage["reasoning_fmt"] = format_tokens(usage["reasoning"])
        usage["uncached_fmt"] = format_tokens(usage["uncached_input"])
        bar_total = max(usage["input"] + usage["output"], 1)
        out_non_reason = max(usage["output"] - usage["reasoning"], 0)
        usage["bar"] = {
            "uncached_pct": round(100.0 * usage["uncached_input"] / bar_total, 2),
            "cached_pct": round(100.0 * usage["cached"] / bar_total, 2),
            "out_pct": round(100.0 * out_non_reason / bar_total, 2),
            "reason_pct": round(100.0 * usage["reasoning"] / bar_total, 2),
        }
    usage["by_model_rows"] = [
        {
            "model": model,
            "input_fmt": format_tokens(stats["input"]),
            "output_fmt": format_tokens(stats["output"]),
            "cached_fmt": format_tokens(stats["cached"]),
            "reasoning_fmt": format_tokens(stats["reasoning"]),
            "model_calls": stats["model_calls"],
        }
        for model, stats in sorted(
            usage["by_model"].items(), key=lambda item: -item[1]["input"]
        )
    ]
    if usage.get("context_used") is not None:
        usage["context_used_fmt"] = format_tokens(usage["context_used"])
    if usage.get("context_window") is not None:
        usage["context_window_fmt"] = format_tokens(usage["context_window"])
    if usage.get("context_used") is not None and usage.get("context_window"):
        usage["context_pct"] = round(
            100.0 * usage["context_used"] / max(usage["context_window"], 1), 1
        )
    return usage


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


def iter_jsonl(path: Path):
    """Yield decoded objects from a JSONL file, skipping blank or invalid lines."""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


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


