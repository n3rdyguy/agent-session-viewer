"""Grok session metadata and transcript parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..images import extract_text, extract_text_and_images
from ..turns import format_tool_args, make_turn
from ..util import (
    display_time,
    empty_token_usage,
    finalize_token_usage,
    human_time,
    iter_jsonl,
    load_json,
    pretty_json,
)


def grok_token_usage(path: Path) -> dict:
    """
    Estimate session token usage by summing turn_completed.usage from updates.jsonl.
    Also pulls latest context-window stats from signals.json when present.
    """
    usage = empty_token_usage()

    updates = path / "updates.jsonl"
    if updates.exists():
        try:
            for obj in iter_jsonl(updates):
                update = ((obj.get("params") or {}).get("update") or {})
                if update.get("sessionUpdate") != "turn_completed":
                    continue
                u = update.get("usage") or {}
                if not isinstance(u, dict):
                    continue
                usage["turns"] += 1
                usage["input"] += int(u.get("inputTokens") or 0)
                usage["output"] += int(u.get("outputTokens") or 0)
                usage["total"] += int(u.get("totalTokens") or 0)
                usage["cached"] += int(u.get("cachedReadTokens") or 0)
                usage["reasoning"] += int(u.get("reasoningTokens") or 0)
                usage["model_calls"] += int(u.get("modelCalls") or 0)
                usage["api_duration_ms"] += int(u.get("apiDurationMs") or 0)

                mu = u.get("modelUsage") or {}
                if isinstance(mu, dict):
                    for model, stats in mu.items():
                        if not isinstance(stats, dict):
                            continue
                        bucket = usage["by_model"].setdefault(
                            model,
                            {"input": 0, "output": 0, "cached": 0, "reasoning": 0, "model_calls": 0},
                        )
                        bucket["input"] += int(stats.get("inputTokens") or 0)
                        bucket["output"] += int(stats.get("outputTokens") or 0)
                        bucket["cached"] += int(stats.get("cachedReadTokens") or 0)
                        bucket["reasoning"] += int(stats.get("reasoningTokens") or 0)
                        bucket["model_calls"] += int(stats.get("modelCalls") or 0)
        except Exception:
            pass

    if usage["turns"] > 0 or usage["input"] or usage["output"]:
        usage["source"] = "updates.jsonl · sum of turn_completed"

    signals = load_json(path / "signals.json") or {}
    if isinstance(signals, dict):
        ctx_used = signals.get("contextTokensUsed")
        ctx_win = signals.get("contextWindowTokens")
        if ctx_used is not None:
            usage["context_used"] = int(ctx_used)
        if ctx_win is not None:
            usage["context_window"] = int(ctx_win)

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

    todos: list[dict] = []
    todo_blob = state.get("grok_build.Todo") or state.get("Todo") or {}
    if isinstance(todo_blob, dict):
        items = todo_blob.get("todos") if isinstance(todo_blob.get("todos"), dict) else todo_blob
        if isinstance(items, dict):
            for tid, item in items.items():
                if not isinstance(item, dict):
                    continue
                todos.append({
                    "id": str(tid),
                    "content": item.get("content") or "",
                    "status": item.get("status") or "unknown",
                    "priority": item.get("priority") or "",
                })
            # Keep insertion order from file; status sort secondary
            status_rank = {"in_progress": 0, "pending": 1, "completed": 2, "cancelled": 3}
            todos.sort(key=lambda t: (status_rank.get(t["status"], 9), t["id"]))

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
            settings.append({
                "tool": tool_name.replace("grok_build.", ""),
                "key": key,
                "value": pretty_json(val, 200) if not isinstance(val, (str, int, float, bool)) else str(val),
            })

    other_state = []
    artifacts = []
    skip = {"grok_build.Todo", "Todo", "grok_build.Scheduler", "grok_build.ReportedTaskCompletions"}
    for k, v in state.items():
        if k in skip:
            continue
        label = k.replace("grok_build.", "")
        # Prefer collapsible artifacts for larger / document-like blobs
        if isinstance(v, str) and len(v) > 200:
            artifacts.append({
                "id": f"state-{label}",
                "title": label,
                "subtitle": "resources_state",
                "kind": "markdown",
                "text": v,
            })
        elif isinstance(v, (dict, list)) and len(pretty_json(v, 50000)) > 400:
            artifacts.append({
                "id": f"state-{label}",
                "title": label,
                "subtitle": "resources_state · json",
                "kind": "json",
                "text": pretty_json(v, 200000),
            })
        else:
            other_state.append({"key": label, "value": pretty_json(v, 400)})

    return {
        "todos": todos,
        "scheduler_tasks": tasks,
        "reported_completions": reported,
        "settings": settings,
        "other_state": other_state,
        "artifacts": artifacts,
    }


def grok_hunk_records(path: Path) -> list[dict]:
    f = path / "hunk_records.jsonl"
    if not f.exists():
        return []
    rows = []
    try:
        for o in iter_jsonl(f):
            rows.append({
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
            })
    except Exception:
        pass
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
        except Exception:
            preview = ""
        logs.append({
            "id": call_id,
            "path": str(f),
            "size": size,
            "preview": preview,
        })
    return logs


def grok_recap_requests(path: Path) -> list[dict]:
    rd = path / "recap_requests"
    if not rd.is_dir():
        return []
    items = []
    for f in sorted(rd.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = load_json(f) or {}
        items.append({
            "id": meta.get("request_id") or f.stem,
            "path": str(f),
            "created": human_time(meta.get("created_at")),
            "trigger": meta.get("trigger") or "",
            "model": meta.get("model") or "",
            "size": f.stat().st_size,
            "strip_reasoning": meta.get("strip_reasoning"),
            "x_grok_req_id": meta.get("x_grok_req_id") or "",
            "chat_len": len(meta.get("chat_history") or []) if isinstance(meta.get("chat_history"), list) else None,
        })
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
        events.append(make_turn(
            role=role,
            time=display_time(ts),
            text=text,
        ))

    try:
        for obj in iter_jsonl(f):
            ts = obj.get("timestamp")
            last_ts = ts if ts is not None else last_ts
            params = obj.get("params") or {}
            update = params.get("update") or {}
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
                text = f"{title}\nid: {tcid}\n{format_tool_args(raw_in)}" if tcid else f"{title}\n{format_tool_args(raw_in)}"
                events.append(make_turn(
                    role="tool_call",
                    time=display_time(ts),
                    id=tcid,
                    text=text,
                ))
                tool_final[tcid] = {"status": "started", "title": title}
            elif kind == "tool_call_update":
                tcid = update.get("toolCallId") or update.get("tool_call_id") or ""
                status = update.get("status") or update.get("kind") or ""
                # Keep latest status / content snapshot; emit only terminal-ish states later
                prev = tool_final.get(tcid) or {}
                content = extract_text(update.get("content") or update.get("rawOutput") or update.get("raw_output"))
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
                    events.append(make_turn(
                        role="tool_result",
                        time=display_time(ts),
                        id=tcid,
                        text=f"status: {status}\nid: {tcid}\n{body}".strip(),
                    ))
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
                events.append(make_turn(
                    role="event",
                    time=display_time(ts),
                    id=tid,
                    text=f"task_backgrounded\nid: {tid}\ncommand: {cmd}\noutput_file: {out}",
                ))
            elif kind == "task_completed":
                snap = update.get("task_snapshot") or update
                tid = snap.get("task_id") or update.get("task_id") or ""
                out = snap.get("output") or ""
                if len(str(out)) > 3000:
                    out = str(out)[:3000] + "…"
                events.append(make_turn(
                    role="event",
                    time=display_time(ts),
                    id=tid,
                    text=f"task_completed\nid: {tid}\ncommand: {snap.get('command') or ''}\n{out}",
                ))
            elif kind == "turn_completed":
                if user_buf:
                    flush_buf("user", user_buf, last_ts)
                if thought_buf:
                    flush_buf("reasoning", thought_buf, last_ts)
                if message_buf:
                    flush_buf("assistant", message_buf, last_ts)
                usage = update.get("usage") or {}
                usage_txt = pretty_json(usage, 800) if usage else ""
                events.append(make_turn(
                    role="event",
                    time=display_time(ts),
                    id=update.get("prompt_id") or "",
                    text=f"turn_completed · stop={update.get('stop_reason') or '?'}\n{usage_txt}",
                ))

        if user_buf:
            flush_buf("user", user_buf, last_ts)
        if thought_buf:
            flush_buf("reasoning", thought_buf, last_ts)
        if message_buf:
            flush_buf("assistant", message_buf, last_ts)
    except Exception:
        pass

    if len(events) > max_events:
        head = events[: max_events // 2]
        tail = events[-(max_events // 2) :]
        marker = [make_turn(
            role="event",
            text=f"… {len(events) - max_events} updates omitted for display …",
        )]
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
        except Exception:
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
    try:
        meta = load_json(session_dir / "summary.json") or {}
        info = meta.get("info") if isinstance(meta.get("info"), dict) else {}
        session_cwd = info.get("cwd") or meta.get("cwd")
    except Exception:
        pass

    turns: list[dict] = []
    idx = 0

    def content_pair(raw: Any, extra_images: Any = None) -> tuple[str, list[dict]]:
        return extract_text_and_images(
            raw,
            extra_images=extra_images,
            session_dir=session_dir,
            cwd=session_cwd,
        )

    try:
        for obj in iter_jsonl(history):
            msg_type = (obj.get("type") or obj.get("role") or "event").lower()
            model = obj.get("model_id") or obj.get("model") or ""
            idx += 1
            seq = f"#{idx}"

            if msg_type == "reasoning":
                rid = obj.get("id") or seq
                summary_parts = []
                for block in obj.get("summary") or []:
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
                    body_parts.append("<encrypted>" if obj.get("encrypted_content") is not None else "(empty reasoning)")
                status = obj.get("status") or ""
                effort = obj.get("reasoning_effort") or ""
                meta_bits = [b for b in (status, effort) if b]
                turns.append(make_turn(
                    role="reasoning",
                    time=display_time(obj.get("timestamp")),
                    id=rid,
                    text="\n".join(body_parts),
                    model=model,
                    meta=" · ".join(meta_bits),
                ))
                continue

            if msg_type == "assistant":
                text, images = content_pair(obj.get("content"), obj.get("images"))
                tool_calls = obj.get("tool_calls") or []
                first_tc_id = None
                if tool_calls and isinstance(tool_calls, list):
                    first_tc_id = (tool_calls[0] or {}).get("id")
                aid = obj.get("id") or first_tc_id or seq
                if text.strip() or images:
                    turns.append(make_turn(
                        role="assistant",
                        time=display_time(obj.get("timestamp")),
                        id=aid,
                        text=text,
                        model=model,
                        meta=obj.get("reasoning_effort") or "",
                        images=images,
                    ))
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    tcid = tc.get("id") or ""
                    name = tc.get("name") or "tool"
                    args = format_tool_args(tc.get("arguments") or tc.get("input"))
                    body = f"{name}\nid: {tcid}\n{args}".strip()
                    # Tool args may embed image paths
                    _, tc_images = content_pair(body)
                    turns.append(make_turn(
                        role="tool_call",
                        time=display_time(obj.get("timestamp")),
                        id=tcid or seq,
                        text=body,
                        model=model,
                        meta=name,
                        images=tc_images,
                    ))
                if not text.strip() and not tool_calls and not images:
                    turns.append(make_turn(
                        role="assistant",
                        time=display_time(obj.get("timestamp")),
                        id=seq,
                        text="(empty assistant message)",
                        model=model,
                    ))
                continue

            if msg_type == "tool_result":
                tcid = obj.get("tool_call_id") or obj.get("toolCallId") or ""
                content, images = content_pair(obj.get("content"), obj.get("images"))
                # Enrich from terminal log when result only points at a log / is thin
                log_text = term_map.get(tcid) if tcid else None
                if log_text:
                    if (not content.strip()
                            or "output-file" in content
                            or "<output-file>" in content
                            or len(content) < 80):
                        content = (content + "\n\n--- terminal log ---\n" + log_text).strip() if content.strip() else log_text
                        # re-scan log for image paths
                        _, more = content_pair(content)
                        images = images + more
                turns.append(make_turn(
                    role="tool_result",
                    time=display_time(obj.get("timestamp")),
                    id=tcid or seq,
                    text=content or "(empty tool result)",
                    images=images,
                ))
                continue

            if msg_type in ("user", "system"):
                text, images = content_pair(obj.get("content"), obj.get("images"))
                synthetic = obj.get("synthetic_reason") or ""
                role = msg_type
                if synthetic:
                    role = "system_reminder" if "reminder" in synthetic else f"user ({synthetic})"
                if msg_type == "system":
                    role = "system"
                uid = ""
                if obj.get("prompt_index") is not None:
                    uid = f"prompt:{obj.get('prompt_index')}"
                turns.append(make_turn(
                    role=role,
                    time=display_time(obj.get("timestamp")),
                    id=uid or seq,
                    text=text or "(empty)",
                    model=model,
                    meta=synthetic,
                    images=images,
                ))
                continue

            if msg_type == "backend_tool_call":
                kind = obj.get("kind") if isinstance(obj.get("kind"), dict) else {}
                tool_type = kind.get("tool_type") or "backend_tool"
                action = kind.get("action") if isinstance(kind.get("action"), dict) else {}
                action_type = action.get("type") or ""
                query = action.get("query") or ""
                sources = action.get("sources") or []
                lines = [f"{tool_type}" + (f" · {action_type}" if action_type else "")]
                if query:
                    lines.append(f"query: {query}")
                if sources:
                    lines.append("sources:")
                    for src in sources[:20]:
                        if isinstance(src, dict):
                            lines.append(f"  - {src.get('url') or src.get('type') or pretty_json(src, 120)}")
                        else:
                            lines.append(f"  - {src}")
                    if len(sources) > 20:
                        lines.append(f"  … +{len(sources) - 20} more")
                rid = obj.get("id") or seq
                turns.append(make_turn(
                    role="tool_call",
                    time=display_time(obj.get("timestamp")),
                    id=rid,
                    text="\n".join(lines),
                    model=model,
                    meta=tool_type,
                ))
                continue

            # Unknown types — still show something useful
            rid = obj.get("id") or obj.get("tool_call_id") or seq
            text, images = content_pair(
                obj.get("content") or obj.get("message") or obj.get("text"),
                obj.get("images"),
            )
            if not text.strip() and not images:
                dump = {k: v for k, v in obj.items() if k not in ("encrypted_content",)}
                text = pretty_json(dump, 1200)
            turns.append(make_turn(
                role=msg_type or "event",
                time=display_time(obj.get("timestamp")),
                id=rid,
                text=text,
                model=model,
                images=images,
            ))
    except Exception:
        pass

    return turns


# ─────────────────────────────────────────────
# Codex session context + conversation
# ─────────────────────────────────────────────

