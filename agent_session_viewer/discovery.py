"""Session discovery for the supported agent homes."""

from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import unquote

from .config import CLAUDE_HOME, CODEX_HOME, GROK_HOME
from .util import load_json


def discover_grok() -> list[dict]:
    sessions = []
    root = GROK_HOME / "sessions"
    if not root.exists():
        return sessions

    for group in sorted(root.iterdir()):
        if not group.is_dir():
            continue
        # Encoded cwd folder names: C%3A%5CUsers%5C...
        cwd_hint = unquote(group.name.replace("%2F", "/").replace("%3A", ":").replace("%5C", "\\"))
        cwd_file = group / ".cwd"
        if cwd_file.exists():
            try:
                cwd_hint = cwd_file.read_text(encoding="utf-8").strip() or cwd_hint
            except Exception:
                pass

        for sid_dir in group.iterdir():
            if not sid_dir.is_dir():
                continue
            summary_path = sid_dir / "summary.json"
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
            sessions.append({
                "agent": "grok",
                "id": info.get("id") or sid_dir.name,
                "path": str(sid_dir),
                "cwd": info.get("cwd") or meta.get("cwd") or cwd_hint,
                "title": str(title)[:120],
                "created": meta.get("created_at") or meta.get("created"),
                "updated": meta.get("updated_at") or meta.get("last_active_at") or meta.get("updated"),
                "model": meta.get("current_model_id") or meta.get("model") or meta.get("model_id"),
                "messages": meta.get("num_chat_messages") or meta.get("num_messages") or meta.get("message_count"),
            })
    return sessions


def discover_claude() -> list[dict]:
    sessions = []
    root = CLAUDE_HOME / "projects"
    if not root.exists():
        return sessions

    for proj in root.iterdir():
        if not proj.is_dir():
            continue
        encoded = proj.name
        cwd_hint = "/" + encoded.lstrip("-").replace("--", "/.").replace("-", "/")

        for f in proj.glob("*.jsonl"):
            if f.name.startswith("."):
                continue
            sid = f.stem
            created = updated = model = None
            msg_count = 0
            try:
                with f.open(encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
                msg_count = len(lines)
                sample = lines[:8] + lines[-8:]
                for line in sample:
                    try:
                        obj = json.loads(line)
                        ts = obj.get("timestamp")
                        if ts:
                            created = created or ts
                            updated = ts
                        if obj.get("type") == "assistant":
                            model = (obj.get("message") or {}).get("model") or model
                    except Exception:
                        pass
            except Exception:
                pass

            sessions.append({
                "agent": "claude",
                "id": sid,
                "path": str(f),
                "cwd": cwd_hint,
                "title": sid[:18] + "…",
                "created": created,
                "updated": updated,
                "model": model,
                "messages": msg_count,
            })
    return sessions


def load_codex_session_index() -> dict[str, dict]:
    """Map session id → {thread_name, updated_at} from ~/.codex/session_index.jsonl."""
    index: dict[str, dict] = {}
    path = CODEX_HOME / "session_index.jsonl"
    if not path.exists():
        return index
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                sid = obj.get("id")
                if not sid:
                    continue
                # Later lines win (index may list the same id more than once)
                index[str(sid)] = {
                    "thread_name": obj.get("thread_name") or "",
                    "updated_at": obj.get("updated_at") or "",
                }
    except Exception:
        pass
    return index


def discover_codex() -> list[dict]:
    sessions = []
    titles = load_codex_session_index()

    for sub in ("sessions", "archived_sessions"):
        root = CODEX_HOME / sub
        if not root.exists():
            continue
        for f in root.rglob("rollout-*.jsonl"):
            sid = f.stem
            # Prefer UUID from filename suffix when present
            for part in f.stem.split("-"):
                if len(part) >= 32 and part.count("-") >= 0:
                    pass
            # rollout-2026-07-26T16-39-31-019f9edd-ea9c-7741-ad03-59daedd955a2
            m = re.search(
                r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
                f.stem,
                re.I,
            )
            if m:
                sid = m.group(1)

            created = updated = model = cwd = None
            msg_count = 0
            try:
                with f.open(encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh):
                        if not line.strip():
                            continue
                        msg_count += 1
                        # Meta usually at the start; still accept model updates early
                        if i > 120 and created and cwd and model:
                            # Fast-count the rest of the file without full JSON parse
                            for rest in fh:
                                if rest.strip():
                                    msg_count += 1
                            break
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        ts = obj.get("timestamp")
                        if ts:
                            created = created or ts
                            updated = ts
                        t = obj.get("type")
                        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                        if t == "session_meta":
                            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else payload
                            cwd = meta.get("cwd") or cwd
                            for key in ("id", "session_id"):
                                if isinstance(meta.get(key), str) and meta[key]:
                                    sid = meta[key]
                                    break
                            if meta.get("timestamp"):
                                created = created or meta.get("timestamp")
                        elif t == "turn_context":
                            model = payload.get("model") or model
                            cwd = payload.get("cwd") or cwd
                        elif t == "event_msg" and (payload.get("type") == "thread_settings_applied"):
                            settings = payload.get("thread_settings") or {}
                            model = settings.get("model") or model
                            cwd = settings.get("cwd") or cwd
            except Exception:
                pass

            # Prefer index timestamp / file mtime for "updated" (early scan may miss the end)
            try:
                mtime_iso = datetime.fromtimestamp(f.stat().st_mtime).isoformat()
            except Exception:
                mtime_iso = None
            idx = titles.get(sid) or {}
            title = idx.get("thread_name") or f.name[:55]
            if idx.get("updated_at"):
                updated = idx["updated_at"]
            elif mtime_iso:
                updated = mtime_iso
            elif not updated and mtime_iso:
                updated = mtime_iso

            sessions.append({
                "agent": "codex",
                "id": sid,
                "path": str(f),
                "cwd": cwd or "?",
                "title": str(title)[:120],
                "created": created,
                "updated": updated,
                "model": model,
                "messages": msg_count,
            })
    return sessions


def all_sessions(agent: str | None = None) -> list[dict]:
    items = []
    if agent in (None, "grok", "all"):
        items.extend(discover_grok())
    if agent in (None, "claude", "all"):
        items.extend(discover_claude())
    if agent in (None, "codex", "all"):
        items.extend(discover_codex())

    def key(s):
        return s.get("updated") or s.get("created") or ""

    items.sort(key=key, reverse=True)
    return items


# ─────────────────────────────────────────────
# Grok session context (summary, todos, side files)
# ─────────────────────────────────────────────

