"""Small formatting and filesystem helpers shared across the viewer."""

from __future__ import annotations

import html
import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .registry import all_homes
from .types import ParseDiagnostic

LOGGER = logging.getLogger(__name__)
MAX_DIAGNOSTIC_MESSAGE = 160
MAX_PARSE_DIAGNOSTICS = 100
_DIAGNOSTICS: ContextVar[list[ParseDiagnostic] | None] = ContextVar(
    "asv_parse_diagnostics", default=None
)


def _record_parse_diagnostic(path: Path, line: int | None, category: str, message: str) -> None:
    diagnostics = _DIAGNOSTICS.get()
    if diagnostics is None or len(diagnostics) >= MAX_PARSE_DIAGNOSTICS:
        return
    bounded = " ".join(message.split())[:MAX_DIAGNOSTIC_MESSAGE]
    diagnostics.append({"path": str(path), "line": line, "category": category, "message": bounded})


@contextmanager
def collect_parse_diagnostics() -> Iterator[list[ParseDiagnostic]]:
    """Collect diagnostics emitted by JSONL readers in this execution context."""
    diagnostics: list[ParseDiagnostic] = []
    token = _DIAGNOSTICS.set(diagnostics)
    try:
        yield diagnostics
    finally:
        _DIAGNOSTICS.reset(token)


def safe_int(
    value: Any,
    *,
    path: Path,
    field: str,
    line: int | None = None,
    default: int = 0,
) -> int:
    """Convert an integer-like value or report a bounded field diagnostic."""
    if value is None or value == "":
        return default
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
        _record_parse_diagnostic(path, line, "invalid_number", f"{field} must be an integer")
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        _record_parse_diagnostic(path, line, "invalid_number", f"{field} must be an integer")
        return default


def report_record_failure(path: Path, exc: Exception) -> None:
    """Report an unexpected per-record parser failure without exposing its value."""
    _record_parse_diagnostic(
        path, None, "invalid_record", f"Record skipped after {type(exc).__name__}"
    )
    LOGGER.warning("Skipped invalid record in %s: %s", path, type(exc).__name__)
    LOGGER.debug("Record parser traceback", exc_info=exc)


def decode_html_entities(value: Any) -> str:
    """Decode nested HTML entities in a value intended for display."""
    if value is None:
        return ""
    decoded = str(value)
    # Tool results can pass through multiple HTML-aware serializers and arrive
    # as e.g. ``&amp;quot;``. Decode each layer before the template escapes the
    # final text for its actual HTML context.
    for _ in range(10):
        unescaped = html.unescape(decoded)
        if unescaped == decoded:
            break
        decoded = unescaped
    return decoded


def decode_view_data(value: Any) -> Any:
    """Return view data with display strings decoded once.

    Message/document bodies are decoded by the Markdown rendering boundary,
    while paths, URLs, and pre-rendered HTML must retain their exact values.
    """
    if isinstance(value, dict):
        return {
            key: (item if key in {"html", "path", "text", "url"} else decode_view_data(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [decode_view_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(decode_view_data(item) for item in value)
    if isinstance(value, str):
        return decode_html_entities(value)
    return value


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
    except (OverflowError, TypeError, ValueError):
        return ""


def display_time(ts: str | float | int | None) -> str:
    """Format a timestamp for display; ids belong in a turn's ``id`` field."""
    return human_time(ts)


def epoch_seconds(ts: str | float | int | None) -> float:
    """Normalize ISO strings and unix second/ms numbers to epoch seconds; 0.0 when unknown."""
    if ts is None or ts == "" or isinstance(ts, bool):
        return 0.0
    try:
        if isinstance(ts, (int, float)):
            value = float(ts)
        else:
            text = str(ts).strip()
            try:
                value = float(text)
            except ValueError:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        if value > 1e12:  # ms, same heuristic as human_time
            value = value / 1000.0
        return value
    except (OverflowError, OSError, TypeError, ValueError):
        return 0.0


def rel_time(ts: str | float | int | None, now: float | None = None) -> str:
    """Compact relative age: 'just now', '5m ago', '3h ago', '2d ago'; a date beyond 7 days."""
    epoch = epoch_seconds(ts)
    if epoch == 0.0:
        return ""
    diff = (time.time() if now is None else now) - epoch
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    if diff < 7 * 86400:
        return f"{int(diff // 86400)}d ago"
    try:
        return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def truncate(s: str, n: int = 140) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def format_tokens(n: int | float | None) -> str:
    if n is None:
        return "-"
    try:
        n = int(n)
    except (OverflowError, TypeError, ValueError):
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
        "input_fmt": "-",
        "output_fmt": "-",
        "total_fmt": "-",
        "cached_fmt": "-",
        "reasoning_fmt": "-",
        "uncached_fmt": "-",
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
        for model, stats in sorted(usage["by_model"].items(), key=lambda item: -item[1]["input"])
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
            except json.JSONDecodeError:
                return obj
        text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def decode_jsonl_record(path: Path, line_number: int, line: str) -> dict[str, Any] | None:
    """Decode one JSONL line using the shared object-only diagnostic policy."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        _record_parse_diagnostic(
            path,
            line_number,
            "invalid_json",
            f"Invalid JSON at column {exc.colno}",
        )
        return None
    if not isinstance(record, dict):
        _record_parse_diagnostic(
            path,
            line_number,
            "non_object",
            f"Expected an object, got {type(record).__name__}",
        )
        return None
    return record


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield mapping records, reporting and skipping damaged JSONL lines."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line_number, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                record = decode_jsonl_record(path, line_number, line)
                if record is not None:
                    yield record
    except OSError as exc:
        _record_parse_diagnostic(path, None, "io_error", type(exc).__name__)
        LOGGER.warning("Could not read JSONL file %s: %s", path, type(exc).__name__)


def path_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        resolved = path
    for root in all_homes():
        try:
            resolved.relative_to(root.resolve())
            return True
        except (OSError, RuntimeError, ValueError):
            # Fallback for odd Windows path forms
            rs, rr = (
                str(resolved).lower().replace("\\", "/"),
                str(root.resolve()).lower().replace("\\", "/"),
            )
            if rs == rr or rs.startswith(rr + "/"):
                return True
    return False
