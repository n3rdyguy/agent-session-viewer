"""Session discovery for the supported agent homes."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from .images import extract_text
from .registry import AGENT_SPECS
from .types import SessionCard
from .util import decode_jsonl_record, epoch_seconds, iter_jsonl, load_json

LOGGER = logging.getLogger(__name__)
_CODEX_HEADLINE_PUNCTUATION = frozenset(" .,:;!?'-_()/&+")
_TURN_ABORTED_RE = re.compile(r"\bturn_aborted\b", re.IGNORECASE)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")
_BRACKET_MARKER_RE = re.compile(r"^\[[\w -]+\]$")  # placeholder lines like [tool_result]
_DISCOVERY_HEAD_RECORDS = 128
_CLAUDE_EDGE_RECORDS = 8
_CLAUDE_HEADLINE_CHARS = 120
_CLAUDE_TITLE_MARKERS = ('"ai-title"', '"custom-title"', '"last-prompt"')
_CLAUDE_MODEL_MARKER = '"assistant"'
_CLAUDE_SCAN_MARKERS = (*_CLAUDE_TITLE_MARKERS, _CLAUDE_MODEL_MARKER)
_CLAUDE_SYNTHETIC_MODEL = '"<synthetic>"'

CacheKey = tuple[str, str, int, int]
_DISCOVERY_CACHE: dict[CacheKey, dict[str, Any]] = {}
# (agent, resolved path) -> the one cache key currently held for that file. Without it,
# evicting the previous size/mtime entry means scanning every key on each insert, which
# makes a cold scan of n sessions quadratic.
_CACHE_INDEX: dict[tuple[str, str], CacheKey] = {}
_CACHE_LOCK = threading.RLock()


def _timing_enabled() -> bool:
    return os.environ.get("ASV_TIMING_DEBUG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _log_timing(operation: str, started: float) -> None:
    if _timing_enabled():
        LOGGER.info("%s completed in %.1f ms", operation, (time.perf_counter() - started) * 1000)


def clear_discovery_cache() -> None:
    """Clear process-local discovery metadata, primarily for tests and diagnostics."""
    with _CACHE_LOCK:
        _DISCOVERY_CACHE.clear()
        _CACHE_INDEX.clear()


def _file_key(agent: str, path: Path) -> CacheKey | None:
    try:
        resolved = path.resolve(strict=True)
        stat = resolved.stat()
    except OSError:
        return None
    return (agent, str(resolved), stat.st_size, stat.st_mtime_ns)


def _cached_card(
    agent: str, path: Path, loader: Callable[[Path], dict[str, Any]]
) -> dict[str, Any] | None:
    key = _file_key(agent, path)
    if key is None:
        return None
    with _CACHE_LOCK:
        cached = _DISCOVERY_CACHE.get(key)
        if isinstance(cached, dict):
            return dict(cached)
        # The lock deliberately covers the small/bounded loader. This prevents duplicate
        # reads and makes concurrent list requests observe a single complete cache entry.
        value = loader(path)
        if not isinstance(value, dict):
            return None
        identity = (agent, key[1])
        previous = _CACHE_INDEX.get(identity)
        if previous is not None and previous != key:
            _DISCOVERY_CACHE.pop(previous, None)
        _CACHE_INDEX[identity] = key
        _DISCOVERY_CACHE[key] = dict(value)
        return dict(value)


def _prune_agent_cache(agent: str, live_paths: set[str]) -> None:
    with _CACHE_LOCK:
        for key in tuple(_DISCOVERY_CACHE):
            if key[0] == agent and key[1] not in live_paths:
                del _DISCOVERY_CACHE[key]
                _CACHE_INDEX.pop((agent, key[1]), None)


def safe_codex_headline(value: object) -> str:
    """Return a short human-readable Codex headline, rejecting markup/code."""
    if not isinstance(value, str):
        return ""
    line = next((part.strip() for part in value.splitlines() if part.strip()), "")
    line = re.sub(r"\s+", " ", line)
    if not line or not any(char.isalnum() for char in line):
        return ""
    if any(
        not (char.isalnum() or char.isspace() or char in _CODEX_HEADLINE_PUNCTUATION)
        for char in line
    ):
        return ""
    return line


def safe_claude_headline(value: object) -> str:
    """Return a short single-line Claude headline, rejecting agent markup blocks."""
    if not isinstance(value, str):
        return ""
    cleaned = _ANSI_ESCAPE_RE.sub("", value)
    cleaned = _CONTROL_CHARS_RE.sub("", cleaned)
    for part in cleaned.splitlines():
        line = " ".join(part.split())
        if not line or line.startswith("<") or not any(char.isalnum() for char in line):
            continue
        if _BRACKET_MARKER_RE.match(line):
            continue
        return line[:_CLAUDE_HEADLINE_CHARS]
    return ""


_CLAUDE_COMMAND_NAME_RE = re.compile(r"<command-name>\s*([^<]*?)\s*</command-name>")
_CLAUDE_COMMAND_ARGS_RE = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.DOTALL)


def claude_command_title(text: str) -> str:
    """Label for local-command sessions: the slash command (plus args) that ran."""
    if "<command-name>" not in text:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub("", _ANSI_ESCAPE_RE.sub("", text))
    match = _CLAUDE_COMMAND_NAME_RE.search(cleaned)
    if not match:
        return ""
    args = _CLAUDE_COMMAND_ARGS_RE.search(cleaned)
    label = " ".join(f"{match.group(1)} {args.group(1) if args else ''}".split())
    return label[:_CLAUDE_HEADLINE_CHARS]


def codex_headline_was_aborted(value: object) -> bool:
    """Whether the user message selected for a headline contains an abort marker."""
    return isinstance(value, str) and bool(_TURN_ABORTED_RE.search(value))


def discover_grok() -> list[SessionCard]:
    sessions = []
    root = AGENT_SPECS["grok"].looking_in()
    if not root.exists():
        _prune_agent_cache("grok", set())
        return sessions

    live_paths: set[str] = set()
    for group in sorted(root.iterdir()):
        if not group.is_dir():
            continue
        # Encoded cwd folder names: C%3A%5CUsers%5C...
        cwd_hint = unquote(group.name.replace("%2F", "/").replace("%3A", ":").replace("%5C", "\\"))
        cwd_file = group / ".cwd"
        if cwd_file.exists():
            try:
                cwd_hint = cwd_file.read_text(encoding="utf-8").strip() or cwd_hint
            except OSError as exc:
                LOGGER.warning("Could not read Grok cwd hint %s: %s", cwd_file, type(exc).__name__)

        for sid_dir in sorted(group.iterdir()):
            if not sid_dir.is_dir():
                continue
            summary_path = sid_dir / "summary.json"
            key = _file_key("grok", summary_path)
            if key is not None:
                live_paths.add(key[1])

            def load_grok_card(_path: Path) -> dict[str, Any]:
                meta = load_json(summary_path) or {}
                info = meta.get("info") if isinstance(meta.get("info"), dict) else {}
                title = (
                    meta.get("generated_title")
                    or meta.get("session_summary")
                    or meta.get("title")
                    or meta.get("summary")
                    or meta.get("name")
                    or sid_dir.name[:12]
                )
                return {
                    "agent": "grok",
                    "id": info.get("id") or sid_dir.name,
                    "path": str(sid_dir),
                    "cwd": info.get("cwd") or meta.get("cwd") or cwd_hint,
                    "title": str(title),
                    "created": meta.get("created_at") or meta.get("created"),
                    "updated": meta.get("updated_at")
                    or meta.get("last_active_at")
                    or meta.get("updated"),
                    "model": meta.get("current_model_id")
                    or meta.get("model")
                    or meta.get("model_id"),
                    "messages": meta.get("num_chat_messages")
                    or meta.get("num_messages")
                    or meta.get("message_count"),
                }

            card = _cached_card("grok", summary_path, load_grok_card)
            if card is not None:
                sessions.append(card)
                continue

            meta = load_json(summary_path) or {}
            info = meta.get("info") if isinstance(meta.get("info"), dict) else {}
            title = (
                meta.get("generated_title")
                or meta.get("session_summary")
                or meta.get("title")
                or meta.get("summary")
                or meta.get("name")
                or sid_dir.name[:12]
            )
            sessions.append(
                {
                    "agent": "grok",
                    "id": info.get("id") or sid_dir.name,
                    "path": str(sid_dir),
                    "cwd": info.get("cwd") or meta.get("cwd") or cwd_hint,
                    "title": str(title),
                    "created": meta.get("created_at") or meta.get("created"),
                    "updated": meta.get("updated_at")
                    or meta.get("last_active_at")
                    or meta.get("updated"),
                    "model": meta.get("current_model_id")
                    or meta.get("model")
                    or meta.get("model_id"),
                    "messages": meta.get("num_chat_messages")
                    or meta.get("num_messages")
                    or meta.get("message_count"),
                }
            )
    _prune_agent_cache("grok", live_paths)
    return sessions


def _scan_claude_card(path: Path) -> dict[str, Any]:
    first: list[tuple[int, str]] = []
    last: deque[tuple[int, str]] = deque(maxlen=_CLAUDE_EDGE_RECORDS)
    # Title records are small, untimestamped, and rewritten throughout a session, so
    # the newest one usually sits outside both edge windows. The same applies to the
    # model: long sessions often end in user/tool records, pushing the last assistant
    # record out of the tail window. Remember the last line matching each marker
    # instead of widening the window: one slot per marker keeps memory bounded and
    # adds at most one decode each.
    marker_lines: dict[str, tuple[int, str]] = {}
    nonblank_count = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line_number, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                nonblank_count += 1
                item = (line_number, line)
                if len(first) < _CLAUDE_EDGE_RECORDS:
                    first.append(item)
                else:
                    last.append(item)
                for marker in _CLAUDE_SCAN_MARKERS:
                    if marker not in line:
                        continue
                    if marker == _CLAUDE_MODEL_MARKER and _CLAUDE_SYNTHETIC_MODEL in line:
                        continue
                    marker_lines[marker] = item
    except OSError as exc:
        LOGGER.warning("Could not scan Claude session %s: %s", path, type(exc).__name__)
        return {}

    created = updated = model = None
    cwd = None
    ai_title = custom_title = last_prompt = ""
    headline = command_title = ""
    seen_lines: set[int] = set()
    for line_number, line in [*first, *last, *marker_lines.values()]:
        if line_number in seen_lines:
            continue
        seen_lines.add(line_number)
        obj = decode_jsonl_record(path, line_number, line)
        if obj is None:
            continue
        ts = obj.get("timestamp")
        if ts:
            created = created or ts
            # Marker lines decode after the tail window and may sit mid-file, so
            # their older timestamps must not regress the last-activity value.
            if updated is None or epoch_seconds(ts) >= epoch_seconds(updated):
                updated = ts
        # Records carry the real working directory; the encoded folder name is lossy.
        cwd = obj.get("cwd") or cwd
        record_type = obj.get("type")
        if record_type == "ai-title":
            ai_title = str(obj.get("aiTitle") or "") or ai_title
        elif record_type == "custom-title":
            custom_title = str(obj.get("customTitle") or "") or custom_title
        elif record_type == "last-prompt":
            last_prompt = str(obj.get("lastPrompt") or "") or last_prompt
        elif record_type in ("user", "assistant"):
            message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            if record_type == "assistant":
                candidate = str(message.get("model") or "")
                if candidate and not candidate.startswith("<"):
                    model = candidate
            elif not headline:
                text = extract_text(message.get("content"))
                headline = safe_claude_headline(text)
                if not command_title:
                    command_title = claude_command_title(text)
        elif record_type == "system" and not command_title:
            command_title = claude_command_title(str(obj.get("content") or ""))
    return {
        "created": created,
        "updated": updated,
        "model": model,
        "cwd": cwd,
        "title": ai_title or custom_title or safe_claude_headline(last_prompt),
        # Sessions that only ran slash commands have no prompt to name them by.
        "command": command_title,
        "headline": headline,
        # Exact chat counts need a full scan and are deferred to /view; the cheap
        # full-file record count stays the card value.
        "messages": nonblank_count,
    }


def discover_claude() -> list[SessionCard]:
    sessions = []
    root = AGENT_SPECS["claude"].looking_in()
    if not root.exists():
        _prune_agent_cache("claude", set())
        return sessions

    live_paths: set[str] = set()
    for proj in sorted(root.iterdir()):
        if not proj.is_dir():
            continue
        # Fallback only: the encoded folder name loses drive letters, separators, and
        # hyphens inside directory names. Records carry the real cwd.
        cwd_hint = proj.name.replace("--", "/").replace("-", "/")

        for f in sorted(proj.glob("*.jsonl")):
            if f.name.startswith("."):
                continue
            sid = f.stem
            key = _file_key("claude", f)
            if key is None:
                continue
            live_paths.add(key[1])
            card = _cached_card("claude", f, _scan_claude_card) or {}

            sessions.append(
                {
                    "agent": "claude",
                    "id": sid,
                    "path": str(f),
                    "cwd": card.get("cwd") or cwd_hint,
                    "title": card.get("title")
                    or card.get("headline")
                    or card.get("command")
                    or sid,
                    "headline": card.get("headline") or "",
                    "created": card.get("created"),
                    "updated": card.get("updated"),
                    "model": card.get("model"),
                    "messages": card.get("messages"),
                }
            )
    _prune_agent_cache("claude", live_paths)
    return sessions


def load_codex_session_index() -> dict[str, dict[str, Any]]:
    """Map session id → {thread_name, updated_at} from ~/.codex/session_index.jsonl."""
    index: dict[str, dict] = {}
    path = AGENT_SPECS["codex"].home() / "session_index.jsonl"
    if not path.exists():
        _prune_agent_cache("codex-index", set())
        return index

    def load_index(index_path: Path) -> dict[str, Any]:
        result: dict[str, dict] = {}
        for obj in iter_jsonl(index_path):
            sid = obj.get("id")
            if not sid:
                continue
            result[str(sid)] = {
                "thread_name": obj.get("thread_name") or "",
                "updated_at": obj.get("updated_at") or "",
            }
        return result

    key = _file_key("codex-index", path)
    if key is None:
        return index
    _prune_agent_cache("codex-index", {key[1]})
    cached = _cached_card("codex-index", path, load_index)
    return cached or index


def _scan_codex_card(path: Path) -> dict[str, Any]:
    sid = path.stem
    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
        path.stem,
        re.I,
    )
    if match:
        sid = match.group(1)

    created = model = cwd = None
    headline = ""
    aborted = False
    nonblank_count = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line_number, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                nonblank_count += 1
                # Metadata lives in the head window; the rest is only counted.
                if line_number > _DISCOVERY_HEAD_RECORDS:
                    continue
                obj = decode_jsonl_record(path, line_number, line)
                if obj is None:
                    continue
                created = created or obj.get("timestamp")
                record_type = obj.get("type")
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                if record_type == "session_meta":
                    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else payload
                    cwd = meta.get("cwd") or cwd
                    for key in ("id", "session_id"):
                        if isinstance(meta.get(key), str) and meta[key]:
                            sid = meta[key]
                            break
                    created = created or meta.get("timestamp")
                elif record_type == "turn_context":
                    model = payload.get("model") or model
                    cwd = payload.get("cwd") or cwd
                elif record_type == "response_item":
                    if (
                        payload.get("type") == "message"
                        and (payload.get("role") or "").lower() == "user"
                        and not headline
                    ):
                        message = extract_text(payload.get("content"))
                        candidate = safe_codex_headline(message)
                        if candidate:
                            headline = candidate
                            aborted = codex_headline_was_aborted(message)
                elif (
                    record_type == "event_msg"
                    and (payload.get("type") or "").lower() == "user_message"
                    and not headline
                ):
                    message = payload.get("message") or payload.get("text") or ""
                    candidate = safe_codex_headline(message)
                    if candidate:
                        headline = candidate
                        aborted = codex_headline_was_aborted(message)
                elif (
                    record_type == "event_msg" and payload.get("type") == "thread_settings_applied"
                ):
                    settings = (
                        payload.get("thread_settings")
                        if isinstance(payload.get("thread_settings"), dict)
                        else {}
                    )
                    model = settings.get("model") or model
                    cwd = settings.get("cwd") or cwd
    except OSError as exc:
        LOGGER.warning("Could not scan Codex session %s: %s", path, type(exc).__name__)
        return {}
    return {
        "id": sid,
        "cwd": cwd or "?",
        "headline": headline,
        "aborted": aborted,
        "created": created,
        "model": model,
        # Exact chat counts need a full decode and are deferred to /view; the
        # cheap full-file record count matches the Claude card value.
        "messages": nonblank_count,
    }


def discover_codex() -> list[SessionCard]:
    sessions = []
    titles = load_codex_session_index()
    live_paths: set[str] = set()

    for root in AGENT_SPECS["codex"].roots():
        if not root.exists():
            continue
        for path in sorted(root.rglob("rollout-*.jsonl")):
            key = _file_key("codex", path)
            if key is None:
                continue
            live_paths.add(key[1])
            card = _cached_card("codex", path, _scan_codex_card)
            if not card:
                continue
            sid = card["id"]
            idx = titles.get(sid) or {}
            headline = card.get("headline") or ""
            title = safe_codex_headline(idx.get("thread_name")) or headline or path.name
            updated = (
                idx.get("updated_at") or datetime.fromtimestamp(path.stat().st_mtime).isoformat()
            )
            sessions.append(
                {
                    "agent": "codex",
                    "id": sid,
                    "path": str(path),
                    "cwd": card.get("cwd") or "?",
                    "title": str(title)[:120],
                    "headline": headline,
                    "aborted": bool(card.get("aborted")),
                    "created": card.get("created"),
                    "updated": updated,
                    "model": card.get("model"),
                    "messages": card.get("messages"),
                }
            )
    _prune_agent_cache("codex", live_paths)
    return sessions


def session_sort_key(s: SessionCard) -> float:
    """Epoch seconds of a card's last activity; mixing agents' timestamp types is safe."""
    return epoch_seconds(s.get("updated") or s.get("created"))


# Registration table rather than a dispatch chain. It cannot live on the specs
# themselves: the parsers in agents/ import this module, so registry.py must not
# reach back into it.
_DISCOVERERS: dict[str, Callable[[], list[SessionCard]]] = {
    "grok": discover_grok,
    "claude": discover_claude,
    "codex": discover_codex,
}


def all_sessions(agent: str | None = None) -> list[SessionCard]:
    started = time.perf_counter()
    items = []
    for spec in AGENT_SPECS.values():
        if agent in (None, "all", spec.id):
            items.extend(_DISCOVERERS[spec.id]())

    items.sort(key=session_sort_key, reverse=True)
    _log_timing("session discovery", started)
    return items
