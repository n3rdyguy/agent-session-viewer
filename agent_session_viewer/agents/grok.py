"""Grok session metadata and transcript parsing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..file_reads import extract_shell_command, file_artifacts_for_tool_result
from ..images import extract_text, extract_text_and_images
from ..turns import format_tool_args, make_turn
from ..types import SessionData, empty_session
from ..util import (
    display_time,
    empty_token_usage,
    finalize_token_usage,
    human_time,
    iter_jsonl,
    load_json,
    pretty_json,
    report_record_failure,
    safe_int,
)

_STATUS_RANK = {"in_progress": 0, "pending": 1, "completed": 2, "cancelled": 3}


def _todo_item_fields(item: dict[str, Any], *, default_id: str) -> dict[str, str]:
    content = (
        item.get("content")
        or item.get("step")
        or item.get("text")
        or item.get("description")
        or item.get("subject")
        or item.get("activeForm")
        or ""
    )
    return {
        "id": str(item.get("id") or default_id),
        "content": str(content),
        "status": str(item.get("status") or "unknown"),
        "priority": str(item.get("priority") or ""),
    }


def _todos_from_mapping(items: dict[str, Any]) -> list[dict[str, Any]]:
    todos: list[dict[str, Any]] = []
    for tid, item in items.items():
        if isinstance(item, str) and item.strip():
            todos.append(
                {
                    "id": str(tid),
                    "content": item.strip(),
                    "status": "pending",
                    "priority": "",
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        todos.append(_todo_item_fields(item, default_id=str(tid)))
    return todos


def _todos_from_list(items: list[Any]) -> list[dict[str, Any]]:
    todos: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        if isinstance(item, str) and item.strip():
            todos.append(
                {
                    "id": str(index),
                    "content": item.strip(),
                    "status": "pending",
                    "priority": "",
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        todos.append(_todo_item_fields(item, default_id=str(index)))
    return todos


def normalize_todo_blob(blob: Any) -> list[dict[str, Any]]:
    """
    Normalize Grok todo storage shapes onto the shared todo list.

    Supports:
    - ``{"todos": {id: {content,status,priority}}}`` (current resources_state)
    - ``{"todos": [{id,content,status}, ...]}`` (list form / tool args)
    - bare dict-of-items or bare list-of-items
    - legacy ``Todo`` / unprefixed keys handled by the caller
    """
    if blob is None:
        return []
    if isinstance(blob, list):
        return _todos_from_list(blob)
    if not isinstance(blob, dict):
        return []
    if "todos" in blob:
        items = blob.get("todos")
        if isinstance(items, dict):
            return _todos_from_mapping(items)
        if isinstance(items, list):
            return _todos_from_list(items)
        return []
    # Bare id -> item map (or a single todo-shaped dict).
    if any(isinstance(v, dict) and ("content" in v or "status" in v) for v in blob.values()):
        return _todos_from_mapping(blob)
    if any(k in blob for k in ("content", "step", "status")):
        return _todos_from_list([blob])
    return []


def apply_todo_write(
    existing: dict[str, dict[str, str]],
    args: Any,
) -> dict[str, dict[str, str]]:
    """
    Apply one ``todo_write`` tool payload.

    ``merge: true`` updates/creates by id (status-only patches keep prior content).
    ``merge: false``/omitted replaces the whole checklist when items carry content.
    """
    data = args
    if isinstance(args, str):
        try:
            data = json.loads(args)
        except (TypeError, ValueError, json.JSONDecodeError):
            return existing
    if not isinstance(data, dict):
        return existing
    raw_items = data.get("todos")
    if raw_items is None:
        raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return existing
    merge = bool(data.get("merge"))
    state = dict(existing) if merge else {}
    for index, item in enumerate(raw_items, 1):
        if not isinstance(item, dict):
            continue
        tid = str(item.get("id") or index)
        prev = state.get(tid) if merge else None
        prev = prev if isinstance(prev, dict) else {}
        content = (
            item.get("content")
            if item.get("content") not in (None, "")
            else prev.get("content") or item.get("step") or item.get("text") or ""
        )
        status = item.get("status") if item.get("status") not in (None, "") else prev.get("status")
        priority = (
            item.get("priority")
            if item.get("priority") not in (None, "")
            else prev.get("priority") or ""
        )
        state[tid] = {
            "id": tid,
            "content": str(content or ""),
            "status": str(status or "unknown"),
            "priority": str(priority or ""),
        }
    return state


def todos_from_chat_history(path: Path) -> list[dict[str, Any]]:
    """Replay ``todo_write`` tool calls from chat_history when resources_state is empty."""
    history = path / "chat_history.jsonl" if path.is_dir() else path
    if not history.is_file():
        return []
    state: dict[str, dict[str, str]] = {}
    for obj in iter_jsonl(history):
        tool_calls = obj.get("tool_calls") if isinstance(obj.get("tool_calls"), list) else []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or "").lower()
            if name not in ("todo_write", "todowrite", "todo"):
                continue
            args = tc.get("arguments") if "arguments" in tc else tc.get("input")
            state = apply_todo_write(state, args)
    if not state:
        return []
    todos = list(state.values())
    todos.sort(key=lambda t: (_STATUS_RANK.get(t["status"], 9), t["id"]))
    return todos


def grok_prompt_history(
    path: Path,
    session_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """
    Prompts for this session from the project-level ``prompt_history.jsonl``.

    Grok stores history next to session folders:
    ``~/.grok/sessions/<encoded-cwd>/prompt_history.jsonl``.
    """
    if not session_id:
        return []
    session_dir = path if path.is_dir() else path.parent
    candidates = (
        session_dir / "prompt_history.jsonl",
        session_dir.parent / "prompt_history.jsonl",
    )
    history_path = next((p for p in candidates if p.is_file()), None)
    if history_path is None:
        return []
    rows: list[dict[str, Any]] = []
    for obj in iter_jsonl(history_path):
        sid = str(obj.get("session_id") or obj.get("sessionId") or "")
        if sid != session_id:
            continue
        display = obj.get("prompt") or obj.get("display") or obj.get("text") or ""
        if not isinstance(display, str) or not display.strip():
            continue
        row: dict[str, Any] = {
            "display": display,
            "time": human_time(obj.get("timestamp") or obj.get("ts")),
        }
        if obj.get("is_bash") is True:
            row["display"] = f"$ {display}" if not display.startswith("$ ") else display
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def grok_token_usage(path: Path) -> dict:
    """
    Estimate session token usage by summing turn_completed.usage from updates.jsonl.
    Also pulls latest context-window stats from signals.json when present.
    """
    usage = empty_token_usage()

    updates = path / "updates.jsonl"
    if updates.exists():
        for obj in iter_jsonl(updates):
            params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
            update = params.get("update") if isinstance(params.get("update"), dict) else {}
            if update.get("sessionUpdate") != "turn_completed":
                continue
            u = update.get("usage") or {}
            if not isinstance(u, dict):
                continue
            usage["turns"] += 1
            for target, field in (
                ("input", "inputTokens"),
                ("output", "outputTokens"),
                ("total", "totalTokens"),
                ("cached", "cachedReadTokens"),
                ("reasoning", "reasoningTokens"),
                ("model_calls", "modelCalls"),
                ("api_duration_ms", "apiDurationMs"),
            ):
                usage[target] += safe_int(u.get(field), path=updates, field=field)

            mu = u.get("modelUsage") or {}
            if isinstance(mu, dict):
                for model, stats in mu.items():
                    if not isinstance(stats, dict):
                        continue
                    bucket = usage["by_model"].setdefault(
                        model,
                        {
                            "input": 0,
                            "output": 0,
                            "cached": 0,
                            "reasoning": 0,
                            "model_calls": 0,
                        },
                    )
                    for target, field in (
                        ("input", "inputTokens"),
                        ("output", "outputTokens"),
                        ("cached", "cachedReadTokens"),
                        ("reasoning", "reasoningTokens"),
                        ("model_calls", "modelCalls"),
                    ):
                        bucket[target] += safe_int(stats.get(field), path=updates, field=field)

    if usage["turns"] > 0 or usage["input"] or usage["output"]:
        usage["source"] = "updates.jsonl · sum of turn_completed"

    signals = load_json(path / "signals.json") or {}
    if isinstance(signals, dict):
        ctx_used = signals.get("contextTokensUsed")
        ctx_win = signals.get("contextWindowTokens")
        if ctx_used is not None:
            usage["context_used"] = safe_int(
                ctx_used, path=path / "signals.json", field="contextTokensUsed"
            )
        if ctx_win is not None:
            usage["context_window"] = safe_int(
                ctx_win, path=path / "signals.json", field="contextWindowTokens"
            )

        # Fallback estimate when no turn_completed records exist
        if not (usage["turns"] > 0 or usage["input"] or usage["output"]) and usage["context_used"]:
            usage["source"] = "signals.json · context only (no turn totals)"
            usage["input"] = usage["context_used"]

    return finalize_token_usage(usage)


def grok_summary_card(path: Path) -> dict:
    meta = load_json(path / "summary.json") or {}
    info = meta.get("info") if isinstance(meta.get("info"), dict) else {}
    tokens = grok_token_usage(path)
    return {
        "id": info.get("id") or path.name,
        "title": meta.get("generated_title") or meta.get("session_summary") or path.name,
        "session_summary": meta.get("session_summary") or "",
        "cwd": info.get("cwd") or "",
        "created": human_time(meta.get("created_at")),
        "updated": human_time(meta.get("updated_at") or meta.get("last_active_at")),
        "model": meta.get("current_model_id") or "",
        "agent_name": meta.get("agent_name") or "",
        "sandbox_profile": meta.get("sandbox_profile") or "",
        "reasoning_effort": meta.get("reasoning_effort") or "",
        "num_messages": meta.get("num_messages"),
        "num_chat_messages": meta.get("num_chat_messages"),
        "request_id": meta.get("request_id") or "",
        "head_branch": meta.get("head_branch") or "",
        "head_commit": (meta.get("head_commit") or "")[:12],
        "git_root_dir": meta.get("git_root_dir") or "",
        "tokens": tokens,
    }


def grok_resources(path: Path) -> dict:
    data = load_json(path / "resources_state.json") or {}
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}

    # Prefer resources_state (authoritative snapshot). Fall back to replaying
    # todo_write tool calls from chat_history for older / incomplete sessions.
    todos: list[dict] = []
    for key in ("grok_build.Todo", "Todo", "todo", "todos"):
        if key not in state:
            continue
        todos = normalize_todo_blob(state.get(key))
        if todos:
            break
    if not todos:
        # Some builds store the checklist under params rather than state.
        for key in ("grok_build.Todo", "Todo"):
            if key in params:
                todos = normalize_todo_blob(params.get(key))
                if todos:
                    break
    if not todos:
        todos = todos_from_chat_history(path)
    if todos:
        todos.sort(
            key=lambda t: (_STATUS_RANK.get(str(t.get("status") or ""), 9), str(t.get("id") or ""))
        )

    scheduler = state.get("grok_build.Scheduler") or {}
    tasks = []
    if isinstance(scheduler, dict):
        raw_tasks = scheduler.get("tasks") or []
        if isinstance(raw_tasks, list):
            for t in raw_tasks:
                if isinstance(t, dict):
                    tasks.append(t)
                else:
                    tasks.append({"id": str(t)})

    reported = []
    rtc = state.get("grok_build.ReportedTaskCompletions") or {}
    if isinstance(rtc, dict) and isinstance(rtc.get("reported"), list):
        reported = [str(x) for x in rtc["reported"]]

    # Settings: only show non-null / non-default-ish values for readability
    settings: list[dict] = []
    for tool_name, conf in params.items():
        if not isinstance(conf, dict):
            settings.append({"tool": tool_name, "key": "", "value": pretty_json(conf, 200)})
            continue
        for key, val in conf.items():
            if val is None:
                continue
            settings.append(
                {
                    "tool": tool_name.replace("grok_build.", ""),
                    "key": key,
                    "value": pretty_json(val, 200)
                    if not isinstance(val, (str, int, float, bool))
                    else str(val),
                }
            )

    other_state = []
    artifacts = []
    skip = {
        "grok_build.Todo",
        "Todo",
        "todo",
        "todos",
        "grok_build.Scheduler",
        "grok_build.ReportedTaskCompletions",
    }
    for k, v in state.items():
        if k in skip:
            continue
        label = k.replace("grok_build.", "")
        # Prefer collapsible artifacts for larger / document-like blobs
        if isinstance(v, str) and len(v) > 200:
            artifacts.append(
                {
                    "id": f"state-{label}",
                    "title": label,
                    "subtitle": "resources_state",
                    "kind": "markdown",
                    "text": v,
                }
            )
        elif isinstance(v, (dict, list)) and len(pretty_json(v, 50000)) > 400:
            artifacts.append(
                {
                    "id": f"state-{label}",
                    "title": label,
                    "subtitle": "resources_state · json",
                    "kind": "json",
                    "text": pretty_json(v, 200000),
                }
            )
        else:
            other_state.append({"key": label, "value": pretty_json(v, 400)})

    # Prompt history (project-level prompt_history.jsonl filtered by session id)
    meta = load_json(path / "summary.json") if path.is_dir() else None
    meta = meta if isinstance(meta, dict) else {}
    info = meta.get("info") if isinstance(meta.get("info"), dict) else {}
    session_id = str(info.get("id") or (path.name if path.is_dir() else path.stem) or "")
    history = grok_prompt_history(path, session_id)

    return {
        "todos": todos,
        "scheduler_tasks": tasks,
        "reported_completions": reported,
        "settings": settings,
        "other_state": other_state,
        "artifacts": artifacts,
        "prompt_history": history,
    }


def grok_hunk_records(path: Path) -> list[dict]:
    f = path / "hunk_records.jsonl"
    if not f.exists():
        return []
    rows = []
    for o in iter_jsonl(f):
        rows.append(
            {
                "hunk_id": o.get("hunkId") or o.get("hunk_id") or "",
                "file_path": o.get("filePath") or o.get("file_path") or "",
                "event": o.get("eventType") or o.get("event") or "",
                "source": o.get("sourceType") or "",
                "added": o.get("linesAdded"),
                "removed": o.get("linesRemoved"),
                "start": o.get("hunkStart"),
                "end": o.get("hunkEnd"),
                "prompt_index": o.get("promptIndex"),
                "time": human_time(o.get("timestamp")),
                "author_id": o.get("authorId") or o.get("agentId") or "",
            }
        )
    return rows


def grok_terminal_logs(path: Path) -> list[dict]:
    td = path / "terminal"
    if not td.is_dir():
        return []
    logs = []
    for f in sorted(td.glob("*.log")):
        call_id = f.stem  # call-...
        size = f.stat().st_size
        preview = ""
        try:
            preview = f.read_text(encoding="utf-8", errors="replace")
            if len(preview) > 400:
                preview = preview[:400] + "…"
        except OSError:
            preview = ""
        logs.append(
            {
                "id": call_id,
                "path": str(f),
                "size": size,
                "preview": preview,
            }
        )
    return logs


def grok_recap_requests(path: Path) -> list[dict]:
    rd = path / "recap_requests"
    if not rd.is_dir():
        return []
    items = []
    for f in sorted(rd.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = load_json(f) or {}
        items.append(
            {
                "id": meta.get("request_id") or f.stem,
                "path": str(f),
                "created": human_time(meta.get("created_at")),
                "trigger": meta.get("trigger") or "",
                "model": meta.get("model") or "",
                "size": f.stat().st_size,
                "strip_reasoning": meta.get("strip_reasoning"),
                "x_grok_req_id": meta.get("x_grok_req_id") or "",
                "chat_len": len(meta.get("chat_history") or [])
                if isinstance(meta.get("chat_history"), list)
                else None,
            }
        )
    return items


def grok_updates_timeline(path: Path, max_events: int = 400) -> list[dict]:
    """Aggregate streaming updates.jsonl into a readable timeline."""
    f = path / "updates.jsonl"
    if not f.exists():
        return []

    events: list[dict] = []
    # Open buffers for streaming chunks
    user_buf: list[str] = []
    thought_buf: list[str] = []
    message_buf: list[str] = []
    tool_final: dict[str, dict] = {}  # toolCallId -> last meaningful update
    last_ts = None

    def flush_buf(role: str, parts: list[str], ts) -> None:
        text = "".join(parts).strip()
        parts.clear()
        if not text:
            return
        events.append(
            make_turn(
                role=role,
                time=display_time(ts),
                text=text,
            )
        )

    record_iter = iter_jsonl(f)
    while True:
        try:
            obj = next(record_iter)
        except StopIteration:
            break
        try:
            ts = obj.get("timestamp")
            last_ts = ts if ts is not None else last_ts
            params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
            update = params.get("update") if isinstance(params.get("update"), dict) else {}
            kind = update.get("sessionUpdate") or ""

            if kind == "user_message_chunk":
                if thought_buf:
                    flush_buf("reasoning", thought_buf, last_ts)
                if message_buf:
                    flush_buf("assistant", message_buf, last_ts)
                user_buf.append(extract_text(update.get("content")))
            elif kind == "agent_thought_chunk":
                if user_buf:
                    flush_buf("user", user_buf, last_ts)
                if message_buf:
                    flush_buf("assistant", message_buf, last_ts)
                thought_buf.append(extract_text(update.get("content")))
            elif kind == "agent_message_chunk":
                if user_buf:
                    flush_buf("user", user_buf, last_ts)
                if thought_buf:
                    flush_buf("reasoning", thought_buf, last_ts)
                message_buf.append(extract_text(update.get("content")))
            elif kind == "tool_call":
                if user_buf:
                    flush_buf("user", user_buf, last_ts)
                if thought_buf:
                    flush_buf("reasoning", thought_buf, last_ts)
                if message_buf:
                    flush_buf("assistant", message_buf, last_ts)
                tcid = update.get("toolCallId") or update.get("tool_call_id") or ""
                title = update.get("title") or update.get("kind") or "tool"
                raw_in = update.get("rawInput") or update.get("raw_input") or update.get("input")
                text = (
                    f"{title}\nid: {tcid}\n{format_tool_args(raw_in)}"
                    if tcid
                    else f"{title}\n{format_tool_args(raw_in)}"
                )
                events.append(
                    make_turn(
                        role="tool_call",
                        time=display_time(ts),
                        id=tcid,
                        text=text,
                    )
                )
                tool_final[tcid] = {"status": "started", "title": title}
            elif kind == "tool_call_update":
                tcid = update.get("toolCallId") or update.get("tool_call_id") or ""
                status = update.get("status") or update.get("kind") or ""
                # Keep latest status / content snapshot; emit only terminal-ish states later
                prev = tool_final.get(tcid) or {}
                content = extract_text(
                    update.get("content") or update.get("rawOutput") or update.get("raw_output")
                )
                if content:
                    prev["content"] = content
                if status:
                    prev["status"] = status
                if update.get("title"):
                    prev["title"] = update["title"]
                prev["ts"] = ts
                tool_final[tcid] = prev
                # Emit completed/failed updates inline
                if str(status).lower() in ("completed", "failed", "error", "cancelled"):
                    body = prev.get("content") or ""
                    events.append(
                        make_turn(
                            role="tool_result",
                            time=display_time(ts),
                            id=tcid,
                            text=f"status: {status}\nid: {tcid}\n{body}".strip(),
                        )
                    )
            elif kind == "task_backgrounded":
                if user_buf:
                    flush_buf("user", user_buf, last_ts)
                if thought_buf:
                    flush_buf("reasoning", thought_buf, last_ts)
                if message_buf:
                    flush_buf("assistant", message_buf, last_ts)
                tid = update.get("task_id") or update.get("tool_call_id") or ""
                cmd = update.get("command") or ""
                out = update.get("output_file") or ""
                events.append(
                    make_turn(
                        role="event",
                        time=display_time(ts),
                        id=tid,
                        text=f"task_backgrounded\nid: {tid}\ncommand: {cmd}\noutput_file: {out}",
                    )
                )
            elif kind == "task_completed":
                snap = (
                    update.get("task_snapshot")
                    if isinstance(update.get("task_snapshot"), dict)
                    else update
                )
                tid = snap.get("task_id") or update.get("task_id") or ""
                out = snap.get("output") or ""
                if len(str(out)) > 3000:
                    out = str(out)[:3000] + "…"
                events.append(
                    make_turn(
                        role="event",
                        time=display_time(ts),
                        id=tid,
                        text=f"task_completed\nid: {tid}\ncommand: {snap.get('command') or ''}\n{out}",
                    )
                )
            elif kind == "turn_completed":
                if user_buf:
                    flush_buf("user", user_buf, last_ts)
                if thought_buf:
                    flush_buf("reasoning", thought_buf, last_ts)
                if message_buf:
                    flush_buf("assistant", message_buf, last_ts)
                usage = update.get("usage") or {}
                usage_txt = pretty_json(usage, 800) if usage else ""
                events.append(
                    make_turn(
                        role="event",
                        time=display_time(ts),
                        id=update.get("prompt_id") or "",
                        text=f"turn_completed · stop={update.get('stop_reason') or '?'}\n{usage_txt}",
                    )
                )

        except Exception as exc:
            report_record_failure(f, exc)
            continue

    if user_buf:
        flush_buf("user", user_buf, last_ts)
    if thought_buf:
        flush_buf("reasoning", thought_buf, last_ts)
    if message_buf:
        flush_buf("assistant", message_buf, last_ts)

    if len(events) > max_events:
        head = events[: max_events // 2]
        tail = events[-(max_events // 2) :]
        marker = [
            make_turn(
                role="event",
                text=f"… {len(events) - max_events} updates omitted for display …",
            )
        ]
        return head + marker + tail
    return events


def grok_terminal_map(path: Path) -> dict[str, str]:
    """Map tool/call id -> full log text (capped)."""
    td = path / "terminal"
    out: dict[str, str] = {}
    if not td.is_dir():
        return out
    for f in td.glob("*.log"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            if len(text) > 20000:
                text = text[:20000] + "\n… [truncated]"
            out[f.stem] = text
        except OSError:
            continue
    return out


# ─────────────────────────────────────────────
# Conversation extractors
# ─────────────────────────────────────────────


def get_grok_conversation(path: Path) -> list[dict]:
    """Parse chat_history.jsonl with reasoning, tool calls, and terminal enrichment."""
    history = path / "chat_history.jsonl" if path.is_dir() else path
    if path.is_dir() and not history.exists():
        # fallback older layout
        for alt in (path / "updates.jsonl",):
            if alt.exists():
                return grok_updates_timeline(path)
        return []

    session_dir = path if path.is_dir() else path.parent
    term_map = grok_terminal_map(session_dir)
    session_cwd = None
    meta_value = load_json(session_dir / "summary.json")
    meta = meta_value if isinstance(meta_value, dict) else {}
    info = meta.get("info") if isinstance(meta.get("info"), dict) else {}
    session_cwd = info.get("cwd") or meta.get("cwd")

    turns: list[dict] = []
    idx = 0
    # call_id → {name, arguments, command?} for file-card pairing
    tool_meta_by_call: dict[str, dict[str, Any]] = {}

    def content_pair(raw: Any, extra_images: Any = None) -> tuple[str, list[dict]]:
        return extract_text_and_images(
            raw,
            extra_images=extra_images,
            session_dir=session_dir,
            cwd=session_cwd,
        )

    record_iter = iter_jsonl(history)
    while True:
        try:
            obj = next(record_iter)
        except StopIteration:
            break
        try:
            msg_type = str(obj.get("type") or obj.get("role") or "event").lower()
            model = obj.get("model_id") or obj.get("model") or ""
            idx += 1
            seq = f"#{idx}"

            if msg_type == "reasoning":
                rid = obj.get("id") or seq
                summary_parts = []
                summary_blocks = obj.get("summary") if isinstance(obj.get("summary"), list) else []
                for block in summary_blocks:
                    if isinstance(block, dict):
                        summary_parts.append(block.get("text") or extract_text(block))
                    else:
                        summary_parts.append(str(block))
                summary_text = "\n".join(p for p in summary_parts if p and str(p).strip())
                body_parts = []
                if summary_text:
                    body_parts.append(summary_text)
                if obj.get("encrypted_content"):
                    body_parts.append("<encrypted>")
                if not body_parts:
                    body_parts.append(
                        "<encrypted>"
                        if obj.get("encrypted_content") is not None
                        else "(empty reasoning)"
                    )
                status = obj.get("status") or ""
                effort = obj.get("reasoning_effort") or ""
                meta_bits = [b for b in (status, effort) if b]
                turns.append(
                    make_turn(
                        role="reasoning",
                        time=display_time(obj.get("timestamp")),
                        id=rid,
                        text="\n".join(body_parts),
                        model=model,
                        meta=" · ".join(meta_bits),
                    )
                )
                continue

            if msg_type == "assistant":
                text, images = content_pair(obj.get("content"), obj.get("images"))
                tool_calls = (
                    obj.get("tool_calls") if isinstance(obj.get("tool_calls"), list) else []
                )
                first_tc_id = None
                if tool_calls and isinstance(tool_calls[0], dict):
                    first_tc_id = tool_calls[0].get("id")
                aid = obj.get("id") or first_tc_id or seq
                if text.strip() or images:
                    turns.append(
                        make_turn(
                            role="assistant",
                            time=display_time(obj.get("timestamp")),
                            id=aid,
                            text=text,
                            model=model,
                            meta=obj.get("reasoning_effort") or "",
                            images=images,
                        )
                    )
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    tcid = str(tc.get("id") or "")
                    name = str(tc.get("name") or "tool")
                    raw_args = tc.get("arguments") if "arguments" in tc else tc.get("input")
                    if tcid:
                        meta_entry: dict[str, Any] = {"name": name}
                        if raw_args is not None:
                            meta_entry["arguments"] = raw_args
                        cmd = extract_shell_command(name, raw_args)
                        if cmd:
                            meta_entry["command"] = cmd
                        tool_meta_by_call[tcid] = meta_entry
                    args = format_tool_args(raw_args)
                    body = f"{name}\nid: {tcid}\n{args}".strip()
                    # Tool args may embed image paths
                    _, tc_images = content_pair(body)
                    turns.append(
                        make_turn(
                            role="tool_call",
                            time=display_time(obj.get("timestamp")),
                            id=tcid or seq,
                            text=body,
                            model=model,
                            meta=name,
                            images=tc_images,
                        )
                    )
                if not text.strip() and not tool_calls and not images:
                    turns.append(
                        make_turn(
                            role="assistant",
                            time=display_time(obj.get("timestamp")),
                            id=seq,
                            text="(empty assistant message)",
                            model=model,
                        )
                    )
                continue

            if msg_type == "tool_result":
                tcid = str(obj.get("tool_call_id") or obj.get("toolCallId") or "")
                content, images = content_pair(obj.get("content"), obj.get("images"))
                # Enrich from terminal log when result only points at a log / is thin
                log_text = term_map.get(tcid) if tcid else None
                if log_text:
                    if (
                        not content.strip()
                        or "output-file" in content
                        or "<output-file>" in content
                        or len(content) < 80
                    ):
                        content = (
                            (content + "\n\n--- terminal log ---\n" + log_text).strip()
                            if content.strip()
                            else log_text
                        )
                        # re-scan log for image paths
                        _, more = content_pair(content)
                        images = images + more
                file_artifacts = None
                file_read_prefix = None
                meta = tool_meta_by_call.get(tcid) if tcid else None
                if meta or content:
                    artifacts, prefix = file_artifacts_for_tool_result(
                        tool_name=(meta or {}).get("name"),
                        arguments=(meta or {}).get("arguments"),
                        command=(meta or {}).get("command"),
                        output=content or "",
                    )
                    if artifacts:
                        file_artifacts = list(artifacts)
                        file_read_prefix = prefix if prefix is not None else ""
                if not (content or "").strip() and not images and not file_artifacts:
                    content = "(empty tool result)"
                turns.append(
                    make_turn(
                        role="tool_result",
                        time=display_time(obj.get("timestamp")),
                        id=tcid or seq,
                        text=content,
                        images=images,
                        file_artifacts=file_artifacts,
                        file_read_prefix=file_read_prefix,
                    )
                )
                continue

            if msg_type in ("user", "system"):
                text, images = content_pair(obj.get("content"), obj.get("images"))
                synthetic = obj.get("synthetic_reason") or ""
                role = msg_type
                # Injected environment block (often without synthetic_reason):
                # <user_info>…</user_info> plus optional <git_status> etc.
                if msg_type == "user" and not synthetic and text.lstrip().startswith("<user_info>"):
                    synthetic = "user_info"
                if synthetic:
                    role = "system_reminder" if "reminder" in synthetic else f"user ({synthetic})"
                if msg_type == "system":
                    role = "system"
                uid = ""
                if obj.get("prompt_index") is not None:
                    uid = f"prompt:{obj.get('prompt_index')}"
                turns.append(
                    make_turn(
                        role=role,
                        time=display_time(obj.get("timestamp")),
                        id=uid or seq,
                        text=text or "(empty)",
                        model=model,
                        meta=synthetic,
                        images=images,
                    )
                )
                continue

            if msg_type == "backend_tool_call":
                kind = obj.get("kind") if isinstance(obj.get("kind"), dict) else {}
                tool_type = kind.get("tool_type") or "backend_tool"
                action = kind.get("action") if isinstance(kind.get("action"), dict) else {}
                action_type = action.get("type") or ""
                query = action.get("query") or ""
                sources = action.get("sources") if isinstance(action.get("sources"), list) else []
                lines = [f"{tool_type}" + (f" · {action_type}" if action_type else "")]
                if query:
                    lines.append(f"query: {query}")
                if sources:
                    lines.append("sources:")
                    for src in sources[:20]:
                        if isinstance(src, dict):
                            lines.append(
                                f"  - {src.get('url') or src.get('type') or pretty_json(src, 120)}"
                            )
                        else:
                            lines.append(f"  - {src}")
                    if len(sources) > 20:
                        lines.append(f"  … +{len(sources) - 20} more")
                rid = obj.get("id") or seq
                turns.append(
                    make_turn(
                        role="tool_call",
                        time=display_time(obj.get("timestamp")),
                        id=rid,
                        text="\n".join(lines),
                        model=model,
                        meta=tool_type,
                    )
                )
                continue

            # Unknown types - still show something useful
            rid = obj.get("id") or obj.get("tool_call_id") or seq
            text, images = content_pair(
                obj.get("content") or obj.get("message") or obj.get("text"),
                obj.get("images"),
            )
            if not text.strip() and not images:
                dump = {k: v for k, v in obj.items() if k not in ("encrypted_content",)}
                text = pretty_json(dump, 1200)
            turns.append(
                make_turn(
                    role=msg_type or "event",
                    time=display_time(obj.get("timestamp")),
                    id=rid,
                    text=text,
                    model=model,
                    images=images,
                )
            )
        except Exception as exc:
            report_record_failure(history, exc)
            continue

    return turns


def load_session(path: Path) -> SessionData:
    """Load a Grok session with the same shape Claude and Codex provide.

    A Grok session is normally a directory. The transcript parser also accepts a
    bare chat_history.jsonl, and keeps an older-layout fallback for sessions that
    only ever wrote updates.jsonl - so turns are read either way, while the
    sidecar panels below only exist for a real session directory.
    """
    session = empty_session("grok", path)
    session["turns"] = get_grok_conversation(path)
    if not path.is_dir():
        return session

    summary = grok_summary_card(path)
    resources = grok_resources(path)
    session.update(
        {
            "title": summary.get("title") or path.name,
            "summary": summary,
            "resources": resources,
            "artifacts": (resources or {}).get("artifacts") or [],
            "hunks": grok_hunk_records(path),
            "terminal_logs": grok_terminal_logs(path),
            "recaps": grok_recap_requests(path),
            "updates": grok_updates_timeline(path),
        }
    )
    return session
