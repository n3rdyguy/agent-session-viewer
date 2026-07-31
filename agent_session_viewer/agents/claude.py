"""Claude Code session scanning and transcript parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import config
from ..discovery import safe_claude_headline
from ..file_reads import extract_shell_command, file_artifacts_for_tool_result
from ..images import extract_text, extract_text_and_images
from ..turns import format_tool_args, make_turn
from ..types import SessionData, Turn
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

SUBAGENT_DIR = "subagents"
SUBAGENT_PREFIX = "agent-"
EDIT_TOOLS = frozenset(("Edit", "Write", "MultiEdit", "NotebookEdit"))
# Roles the chat stylesheet knows; anything else falls back to a neutral event bubble.
_BUBBLE_ROLES = frozenset(("user", "assistant", "system", "system_reminder"))
# Attachments that carry a document worth reading rather than a one-line event.
_DOCUMENT_ATTACHMENTS = {
    "skill_listing": "Available skills",
    "agent_listing_delta": "Available agents",
}
# Kept out of the chat: tool-registry deltas are pure bookkeeping, and task
# reminders are re-emitted after almost every turn but render as the todo list.
_NOISE_ATTACHMENTS = frozenset(("deferred_tools_delta", "task_reminder"))
MEMORY_FILENAME = "CLAUDE.md"
_MAX_MEMORY_CHARS = 64_000
# Claude never writes injected memory into the transcript, so these documents are
# whatever is on disk right now. Say so where it is always visible: the subtitle
# renders in the artifact header and is not part of either copy action, so the
# document body stays a byte-for-byte copy of the file.
MEMORY_DISK_NOTE = "on disk now, may differ from this session"


# ─────────────────────────────────────────────
# Session layout
# ─────────────────────────────────────────────


def is_subagent_path(path: Path) -> bool:
    """Whether a transcript is a subagent file rather than a main session."""
    return path.parent.name == SUBAGENT_DIR and path.name.startswith(SUBAGENT_PREFIX)


def claude_session_id(path: Path) -> str:
    """Session id for a main or subagent transcript."""
    if is_subagent_path(path):
        return path.parent.parent.name
    return path.stem


def subagent_files(path: Path) -> list[Path]:
    """Subagent transcripts recorded beside a main session file."""
    if is_subagent_path(path):
        return []
    directory = path.parent / path.stem / SUBAGENT_DIR
    if not directory.is_dir():
        return []
    try:
        return sorted(p for p in directory.glob(f"{SUBAGENT_PREFIX}*.jsonl") if p.is_file())
    except OSError:
        return []


def load_subagent_records(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Map subagent id to its records, keeping damaged files from failing the session."""
    out: dict[str, list[dict[str, Any]]] = {}
    for f in subagent_files(path):
        records = list(iter_jsonl(f))
        if records:
            out[f.stem[len(SUBAGENT_PREFIX) :]] = records
    return out


# ─────────────────────────────────────────────
# Sibling data under CLAUDE_HOME
# ─────────────────────────────────────────────


def claude_todos(session_id: str) -> list[dict[str, Any]]:
    """Todo checklist stored in ``CLAUDE_HOME/todos`` for this session."""
    if not session_id:
        return []
    directory = config.CLAUDE_HOME / "todos"
    if not directory.is_dir():
        return []
    todos: list[dict[str, Any]] = []
    try:
        files = sorted(directory.glob(f"{session_id}-agent-*.json"))
    except OSError:
        return []
    for f in files:
        items = load_json(f)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                continue
            todos.append(
                {
                    "id": str(item.get("id") or index),
                    "content": str(item.get("content") or item.get("activeForm") or ""),
                    "status": str(item.get("status") or "unknown"),
                    "priority": str(item.get("priority") or ""),
                }
            )
    status_rank = {"in_progress": 0, "pending": 1, "completed": 2, "cancelled": 3}
    todos.sort(key=lambda t: (status_rank.get(t["status"], 9), t["id"]))
    return todos


def _read_memory_file(path: Path, title: str, subtitle: str) -> dict[str, Any] | None:
    """Read one CLAUDE.md, refusing anything that is not still a real CLAUDE.md."""
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    # A symlink aimed elsewhere resolves to a different name, so this also stops
    # `<cwd>/CLAUDE.md -> ~/.ssh/id_rsa` from being rendered as memory.
    if resolved.name != MEMORY_FILENAME or not resolved.is_file():
        return None
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.strip():
        return None
    if len(text) > _MAX_MEMORY_CHARS:
        text = text[:_MAX_MEMORY_CHARS] + "\n\n… truncated for display …"
    return {
        "id": f"memory-{title.lower().replace(' ', '-')}",
        "title": title,
        "subtitle": f"{subtitle} · {MEMORY_DISK_NOTE}",
        "kind": "markdown",
        "text": text,
    }


def claude_memory_documents(cwd: str) -> list[dict[str, Any]]:
    """CLAUDE.md memory that applies to a session, newest scope first."""
    documents: list[dict[str, Any]] = []
    if cwd:
        project_path = Path(cwd) / MEMORY_FILENAME
        project = _read_memory_file(project_path, "Project memory", str(project_path))
        if project:
            documents.append(project)
    user = _read_memory_file(
        config.CLAUDE_HOME / MEMORY_FILENAME, "User memory", f"~/.claude/{MEMORY_FILENAME}"
    )
    if user:
        documents.append(user)
    return documents


def tasks_to_todos(items: Any) -> list[dict[str, Any]]:
    """Map a ``task_reminder`` task list onto the shared todo shape."""
    todos: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return todos
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        content = item.get("subject") or item.get("activeForm") or item.get("description") or ""
        blocked_by = item.get("blockedBy")
        todos.append(
            {
                "id": str(item.get("id") or index),
                "content": str(content),
                "status": str(item.get("status") or "unknown"),
                # Tasks have no priority; surfacing blockers is more useful here.
                "priority": (
                    f"blocked by {', '.join(str(b) for b in blocked_by)}"
                    if isinstance(blocked_by, list) and blocked_by
                    else ""
                ),
            }
        )
    return todos


def claude_prompt_history(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Prompts recorded for this session in ``CLAUDE_HOME/history.jsonl``."""
    if not session_id:
        return []
    path = config.CLAUDE_HOME / "history.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for obj in iter_jsonl(path):
        if str(obj.get("sessionId") or "") != session_id:
            continue
        display = obj.get("display")
        if not isinstance(display, str) or not display.strip():
            continue
        rows.append({"display": display, "time": human_time(obj.get("timestamp"))})
        if len(rows) >= limit:
            break
    return rows


# ─────────────────────────────────────────────
# Record helpers
# ─────────────────────────────────────────────


def _message(obj: dict[str, Any]) -> dict[str, Any]:
    message = obj.get("message")
    return message if isinstance(message, dict) else {}


def _content_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _structured_patch_counts(patch: Any) -> tuple[int, int, int | None, int | None]:
    """Added/removed line counts and line range from a ``structuredPatch`` list."""
    added = removed = 0
    start: int | None = None
    end: int | None = None
    if not isinstance(patch, list):
        return added, removed, start, end
    for hunk in patch:
        if not isinstance(hunk, dict):
            continue
        old_start = hunk.get("newStart") or hunk.get("oldStart")
        if isinstance(old_start, int):
            start = old_start if start is None else min(start, old_start)
            span = hunk.get("newLines") or hunk.get("oldLines") or 0
            if isinstance(span, int):
                stop = old_start + span
                end = stop if end is None else max(end, stop)
        for line in hunk.get("lines") or []:
            if not isinstance(line, str):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
    return added, removed, start, end


def _tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return extract_text(content)


def _attachment_text(attachment: dict[str, Any]) -> str:
    """Readable body for an injected attachment, or empty when it carries no prose."""
    content = attachment.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content:
        return extract_text(content)
    # Structured attachments (permissions, plan-mode pointers) have no prose body;
    # show their fields rather than a bare type name. Empty and zero values are
    # bookkeeping (e.g. an empty task reminder) and leave nothing worth showing.
    fields = {key: value for key, value in attachment.items() if key != "type" and value}
    return pretty_json(fields, 2000) if fields else ""


def _accumulate_usage(tokens: dict[str, Any], message: dict[str, Any], path: Path) -> int | None:
    """Add one message's usage to the running totals; return its context size."""
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    if not usage:
        return None
    raw_input = safe_int(usage.get("input_tokens"), path=path, field="input_tokens")
    cache_read = safe_int(
        usage.get("cache_read_input_tokens"), path=path, field="cache_read_input_tokens"
    )
    cache_create = safe_int(
        usage.get("cache_creation_input_tokens"), path=path, field="cache_creation_input_tokens"
    )
    output = safe_int(usage.get("output_tokens"), path=path, field="output_tokens")
    # Claude reports uncached input separately from cache reads/writes; fold them
    # together so the shared uncached = input - cached math stays meaningful.
    total_input = raw_input + cache_read + cache_create
    tokens["input"] += total_input
    tokens["cached"] += cache_read
    tokens["output"] += output
    tokens["turns"] += 1
    tokens["model_calls"] += 1
    model = str(message.get("model") or "")
    if model:
        bucket = tokens["by_model"].setdefault(
            model, {"input": 0, "output": 0, "cached": 0, "reasoning": 0, "model_calls": 0}
        )
        bucket["input"] += total_input
        bucket["cached"] += cache_read
        bucket["output"] += output
        bucket["model_calls"] += 1
    return total_input + output


# ─────────────────────────────────────────────
# Single-pass session scan
# ─────────────────────────────────────────────


def claude_scan_session(
    path: Path,
    records: list[dict[str, Any]] | None = None,
    subagents: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """
    Single-pass scan of a Claude transcript for summary, tokens, edits, and events.

    Parsed records may be supplied by ``load_session`` so the conversation and
    metadata views reuse one JSONL read.
    """
    session_id = claude_session_id(path)
    ai_title = custom_title = agent_name = last_prompt = ""
    cwd = git_branch = version = ""
    permission_mode = mode = effort = ""
    entrypoint = ""
    created = updated = ""
    headline = ""
    slug = ""

    counts = {
        "lines": 0,
        "user": 0,
        "assistant": 0,
        "reasoning": 0,
        "tool_call": 0,
        "tool_result": 0,
        "sidechain": 0,
    }
    tokens = empty_token_usage()
    context_used: int | None = None
    hunks: list[dict[str, Any]] = []
    events: list[Turn] = []
    artifacts: list[dict[str, Any]] = []
    tool_names: dict[str, str] = {}
    seen_documents: set[str] = set()
    task_items: list[Any] = []

    for obj in records if records is not None else iter_jsonl(path):
        counts["lines"] += 1
        ts = obj.get("timestamp") or ""
        if ts:
            created = created or str(ts)
            updated = str(ts)
        cwd = obj.get("cwd") or cwd
        git_branch = obj.get("gitBranch") or git_branch
        version = obj.get("version") or version
        entrypoint = obj.get("entrypoint") or entrypoint
        slug = obj.get("slug") or slug
        if obj.get("effort"):
            effort = str(obj["effort"])
        if obj.get("isSidechain"):
            counts["sidechain"] += 1

        record_type = obj.get("type")

        if record_type == "ai-title":
            ai_title = str(obj.get("aiTitle") or "") or ai_title
        elif record_type == "custom-title":
            custom_title = str(obj.get("customTitle") or "") or custom_title
        elif record_type == "agent-name":
            agent_name = str(obj.get("agentName") or "") or agent_name
        elif record_type == "last-prompt":
            last_prompt = str(obj.get("lastPrompt") or "") or last_prompt
        elif record_type == "mode":
            mode = str(obj.get("mode") or "") or mode
        elif record_type == "permission-mode":
            permission_mode = str(obj.get("permissionMode") or "") or permission_mode
            events.append(
                make_turn(
                    role="event",
                    time=display_time(ts),
                    text=f"permission-mode · {permission_mode}",
                    meta="mode",
                )
            )
        elif record_type == "queue-operation":
            events.append(
                make_turn(
                    role="event",
                    time=display_time(ts),
                    text=f"queue · {obj.get('operation') or ''}\n{obj.get('content') or ''}".strip(),
                    meta="queue",
                )
            )
        elif record_type == "file-history-snapshot":
            snapshot = obj.get("snapshot") if isinstance(obj.get("snapshot"), dict) else {}
            backups = (
                snapshot.get("trackedFileBackups")
                if isinstance(snapshot.get("trackedFileBackups"), dict)
                else {}
            )
            events.append(
                make_turn(
                    role="event",
                    time=display_time(ts or snapshot.get("timestamp")),
                    id=str(obj.get("messageId") or ""),
                    text=f"file-history-snapshot · {len(backups)} tracked file(s)",
                    meta="snapshot",
                )
            )
        elif record_type == "system":
            subtype = str(obj.get("subtype") or "system")
            body = str(obj.get("content") or "")
            if subtype == "turn_duration":
                body = f"duration_ms: {obj.get('durationMs')}\nmessages: {obj.get('messageCount')}"
            elif subtype == "stop_hook_summary":
                infos = obj.get("hookInfos") if isinstance(obj.get("hookInfos"), list) else []
                body = f"{len(infos)} hook(s)\n{pretty_json(infos, 600)}"
            events.append(
                make_turn(
                    role="event",
                    time=display_time(ts),
                    id=str(obj.get("toolUseID") or ""),
                    text=f"{subtype}\n{body}".strip(),
                    meta=str(obj.get("level") or "system"),
                )
            )
        elif record_type == "attachment":
            attachment = obj.get("attachment") if isinstance(obj.get("attachment"), dict) else {}
            kind = str(attachment.get("type") or "attachment")
            if kind == "task_reminder":
                items = attachment.get("content")
                # Reminders are re-emitted throughout a session and go empty once the
                # list is cleared, so keep the most recent one that still had tasks.
                if isinstance(items, list) and items:
                    task_items = items
            title = _DOCUMENT_ATTACHMENTS.get(kind)
            if title and kind not in seen_documents:
                body = attachment.get("content")
                if not isinstance(body, str):
                    body = pretty_json(attachment.get("addedLines") or attachment, 20000)
                if body.strip():
                    seen_documents.add(kind)
                    artifacts.append(
                        {
                            "id": f"attachment-{kind}",
                            "title": title,
                            "subtitle": kind,
                            "kind": "markdown",
                            "text": body,
                        }
                    )
            events.append(
                make_turn(
                    role="event",
                    time=display_time(ts),
                    text=f"attachment · {kind}",
                    meta="attachment",
                )
            )

        if record_type not in ("user", "assistant"):
            continue

        message = _message(obj)
        role = str(message.get("role") or record_type).lower()
        context_used = _accumulate_usage(tokens, message, path) or context_used

        has_text = isinstance(message.get("content"), str) and bool(message["content"].strip())
        for block in _content_blocks(message):
            block_type = block.get("type")
            if block_type == "thinking":
                counts["reasoning"] += 1
            elif block_type == "tool_use":
                counts["tool_call"] += 1
                call_id = str(block.get("id") or "")
                name = str(block.get("name") or "tool")
                if call_id:
                    tool_names[call_id] = name
            elif block_type == "tool_result":
                counts["tool_result"] += 1
            elif block_type == "text":
                has_text = True
                if role == "user" and not headline:
                    headline = safe_claude_headline(block.get("text"))

        if role == "user" and not headline and isinstance(message.get("content"), str):
            headline = safe_claude_headline(message.get("content"))

        # Records whose only content is a tool_result are transport, and isMeta
        # records are injected reminders — neither is conversation.
        if has_text and not obj.get("isMeta"):
            if role == "assistant":
                counts["assistant"] += 1
            elif role == "user":
                counts["user"] += 1

        result = obj.get("toolUseResult")
        if isinstance(result, dict):
            result_id = ""
            for block in _content_blocks(message):
                if block.get("type") == "tool_result":
                    result_id = str(block.get("tool_use_id") or "")
                    break
            tool_name = tool_names.get(result_id, "")
            file_path = result.get("filePath") or result.get("file_path")
            if tool_name in EDIT_TOOLS or (file_path and "structuredPatch" in result):
                added, removed, start, end = _structured_patch_counts(result.get("structuredPatch"))
                hunks.append(
                    {
                        "hunk_id": result_id or (tool_name or "edit"),
                        "file_path": str(file_path or ""),
                        "event": "create" if result.get("type") == "create" else "edit",
                        "source": tool_name or "edit",
                        "added": added or None,
                        "removed": removed or None,
                        "start": start,
                        "end": end,
                        "prompt_index": None,
                        "time": human_time(ts),
                        "author_id": "",
                    }
                )

    # Subagent transcripts contribute to chat counts even though they live in
    # their own files beside the session.
    for agent_id, agent_records in (subagents or {}).items():
        for obj in agent_records:
            message = _message(obj)
            role = str(message.get("role") or obj.get("type") or "").lower()
            _accumulate_usage(tokens, message, path)
            counts["sidechain"] += 1
            blocks = _content_blocks(message)
            has_text = isinstance(message.get("content"), str) and bool(message["content"].strip())
            for block in blocks:
                if block.get("type") == "text":
                    has_text = True
                elif block.get("type") == "tool_use":
                    counts["tool_call"] += 1
                elif block.get("type") == "tool_result":
                    counts["tool_result"] += 1
                elif block.get("type") == "thinking":
                    counts["reasoning"] += 1
            if not has_text:
                continue
            if role == "assistant":
                counts["assistant"] += 1
            elif role == "user":
                counts["user"] += 1
        events.append(
            make_turn(
                role="event",
                text=f"subagent · {agent_id} · {len(agent_records)} records",
                meta="subagent",
            )
        )

    if context_used is not None:
        tokens["context_used"] = context_used
    if tokens["turns"]:
        tokens["total"] = tokens["input"] + tokens["output"]
        tokens["source"] = "transcript · sum of message.usage"
    tokens = finalize_token_usage(tokens)

    # Memory documents lead the artifact list: they are the closest Claude has to
    # Codex's injected AGENTS.md / base instructions.
    artifacts[:0] = claude_memory_documents(cwd)

    history = claude_prompt_history(session_id)
    if history:
        artifacts.append(
            {
                "id": "prompt-history",
                "title": "Prompt history",
                "subtitle": f"history.jsonl · {len(history)} prompt(s)",
                "kind": "markdown",
                "text": "\n".join(
                    f"- {row['time']} — {row['display']}" if row["time"] else f"- {row['display']}"
                    for row in history
                ),
            }
        )

    settings_rows: list[dict[str, Any]] = []
    for key, value in (
        ("model", tokens["by_model_rows"][0]["model"] if tokens["by_model_rows"] else ""),
        ("version", version),
        ("permission_mode", permission_mode),
        ("mode", mode),
        ("reasoning_effort", effort),
        ("entrypoint", entrypoint),
        ("slug", slug),
        ("cwd", cwd),
    ):
        if value:
            settings_rows.append({"tool": "session", "key": key, "value": str(value)})
    if git_branch:
        settings_rows.append({"tool": "git", "key": "branch", "value": git_branch})

    model = tokens["by_model_rows"][0]["model"] if tokens["by_model_rows"] else ""
    title = ai_title or custom_title or safe_claude_headline(last_prompt) or headline or path.name
    if is_subagent_path(path):
        title = f"subagent {path.stem[len(SUBAGENT_PREFIX) :]}"

    summary = {
        "id": session_id,
        "title": str(title),
        "session_summary": headline or safe_claude_headline(last_prompt),
        "aborted": False,
        "cwd": cwd,
        "created": human_time(created),
        "updated": human_time(updated),
        "model": model,
        "agent_name": agent_name or "claude",
        "sandbox_profile": permission_mode,
        "reasoning_effort": effort,
        "num_messages": counts["lines"],
        "num_chat_messages": counts["user"] + counts["assistant"],
        "request_id": session_id,
        "head_branch": git_branch,
        "head_commit": "",
        "git_root_dir": "",
        "tokens": tokens,
        "cli_version": version,
        "counts": counts,
    }

    resources = {
        # Newer Claude Code records tasks in the transcript; older versions wrote
        # them to CLAUDE_HOME/todos, which is kept as a fallback.
        "todos": tasks_to_todos(task_items) or claude_todos(session_id),
        "scheduler_tasks": [],
        "reported_completions": [],
        "settings": settings_rows,
        "other_state": [],
    }

    return {
        "summary": summary,
        "resources": resources,
        "hunks": hunks,
        "events": events,
        "artifacts": artifacts,
        "meta": {"cwd": cwd, "session_id": session_id, "git_branch": git_branch},
    }


def claude_summary_card(path: Path) -> dict[str, Any]:
    return claude_scan_session(path)["summary"]


# ─────────────────────────────────────────────
# Conversation
# ─────────────────────────────────────────────


def _turns_from_records(
    path: Path,
    records: list[dict[str, Any]],
    session_dir: Path,
    session_cwd: str | None,
    agent_label: str = "",
) -> list[tuple[str, int, Turn]]:
    """Build sortable turns from one transcript's records."""
    out: list[tuple[str, int, Turn]] = []
    idx = 0
    last_ts = ""
    # tool_use id → {name, arguments, command?} for file-card pairing
    tool_meta_by_call: dict[str, dict[str, Any]] = {}

    def content_pair(raw: Any) -> tuple[str, list[dict]]:
        return extract_text_and_images(raw, session_dir=session_dir, cwd=session_cwd)

    def add(turn: Turn, ts: str) -> None:
        out.append((ts, len(out), turn))

    record_iter = iter(records)
    while True:
        try:
            obj = next(record_iter)
        except StopIteration:
            break
        try:
            idx += 1
            seq = f"#{idx}"
            ts = str(obj.get("timestamp") or "")
            last_ts = ts or last_ts
            sort_ts = ts or last_ts
            record_type = obj.get("type")

            if record_type == "system":
                body = str(obj.get("content") or "")
                if not body.strip():
                    continue
                add(
                    make_turn(
                        role="system",
                        time=display_time(ts),
                        id=str(obj.get("uuid") or seq),
                        text=body,
                        meta=str(obj.get("subtype") or "system"),
                    ),
                    sort_ts,
                )
                continue

            if record_type == "attachment":
                attachment = (
                    obj.get("attachment") if isinstance(obj.get("attachment"), dict) else {}
                )
                kind = str(attachment.get("type") or "attachment")
                # Large listings are already artifacts, and tool-registry deltas are
                # pure bookkeeping; repeating either would bury the conversation.
                # Both still appear as one-liners in the events tab.
                if kind in _DOCUMENT_ATTACHMENTS or kind in _NOISE_ATTACHMENTS:
                    continue
                body = _attachment_text(attachment)
                if not body.strip():
                    continue
                add(
                    make_turn(
                        role="system",
                        time=display_time(ts),
                        id=str(obj.get("uuid") or seq),
                        text=body,
                        meta=kind,
                    ),
                    sort_ts,
                )
                continue

            if record_type not in ("user", "assistant"):
                continue

            message = _message(obj)
            role = str(message.get("role") or record_type).lower()
            model = str(message.get("model") or "")
            meta_bits = [b for b in (agent_label, str(obj.get("attributionSkill") or "")) if b]
            meta = " · ".join(meta_bits)
            blocks = _content_blocks(message)
            # Claude injects reminders as ordinary user records flagged isMeta, which
            # would otherwise be indistinguishable from something the user typed.
            if role == "user" and obj.get("isMeta"):
                role = "system_reminder"

            if not blocks:
                text, images = content_pair(message.get("content"))
                if text.strip() or images:
                    add(
                        make_turn(
                            role=role if role in _BUBBLE_ROLES else "event",
                            time=display_time(ts),
                            id=str(obj.get("uuid") or seq),
                            text=text,
                            model=model,
                            meta=meta,
                            images=images,
                        ),
                        sort_ts,
                    )
                continue

            body_parts: list[str] = []
            body_images: list[dict] = []

            def flush_body() -> None:
                """Emit accumulated text so it keeps its place among tool blocks."""
                if not body_parts and not body_images:
                    return
                add(
                    make_turn(
                        role=role if role in _BUBBLE_ROLES else "event",
                        time=display_time(ts),
                        id=str(obj.get("uuid") or seq),
                        text="\n".join(body_parts),
                        model=model,
                        meta=meta,
                        images=list(body_images),
                    ),
                    sort_ts,
                )
                body_parts.clear()
                body_images.clear()

            for block in blocks:
                block_type = block.get("type")

                if block_type == "text":
                    text, images = content_pair(block.get("text"))
                    if text.strip():
                        body_parts.append(text)
                    body_images.extend(images)

                elif block_type == "thinking":
                    flush_body()
                    thinking = str(block.get("thinking") or "").strip()
                    parts = [thinking] if thinking else []
                    if block.get("signature"):
                        parts.append("<encrypted>")
                    if not parts:
                        parts.append("(empty reasoning)")
                    add(
                        make_turn(
                            role="reasoning",
                            time=display_time(ts),
                            id=str(obj.get("uuid") or seq),
                            text="\n".join(parts),
                            model=model,
                            meta=meta or "thinking",
                        ),
                        sort_ts,
                    )

                elif block_type == "tool_use":
                    flush_body()
                    call_id = str(block.get("id") or "")
                    name = str(block.get("name") or "tool")
                    raw_input = block.get("input")
                    if call_id:
                        meta_entry: dict[str, Any] = {"name": name}
                        if raw_input is not None:
                            meta_entry["arguments"] = raw_input
                        cmd = extract_shell_command(name, raw_input)
                        if not cmd and name.lower() in {"bash", "shell", "zsh"}:
                            # Claude Bash stores the command under input.command
                            if isinstance(raw_input, dict) and isinstance(
                                raw_input.get("command"), str
                            ):
                                cmd = raw_input["command"]
                        if cmd:
                            meta_entry["command"] = cmd
                        tool_meta_by_call[call_id] = meta_entry
                    args = format_tool_args(raw_input)
                    text = f"{name}\nid: {call_id}\n{args}".strip()
                    _, images = content_pair(text)
                    add(
                        make_turn(
                            role="tool_call",
                            time=display_time(ts),
                            id=call_id or seq,
                            text=text,
                            model=model,
                            meta=" · ".join(b for b in (name, agent_label) if b),
                            images=images,
                        ),
                        sort_ts,
                    )

                elif block_type == "tool_result":
                    flush_body()
                    call_id = str(block.get("tool_use_id") or "")
                    text, images = content_pair(_tool_result_text(block))
                    if block.get("is_error"):
                        text = f"error: {text}" if text else "error"
                    file_artifacts = None
                    file_read_prefix = None
                    meta = tool_meta_by_call.get(call_id) if call_id else None
                    if meta or text:
                        artifacts, prefix = file_artifacts_for_tool_result(
                            tool_name=(meta or {}).get("name"),
                            arguments=(meta or {}).get("arguments"),
                            command=(meta or {}).get("command"),
                            output=text or "",
                        )
                        if artifacts:
                            file_artifacts = list(artifacts)
                            file_read_prefix = prefix if prefix is not None else ""
                    if not (text or "").strip() and not images and not file_artifacts:
                        text = "(empty tool result)"
                    add(
                        make_turn(
                            role="tool_result",
                            time=display_time(ts),
                            id=call_id or seq,
                            text=text,
                            meta=agent_label,
                            images=images,
                            file_artifacts=file_artifacts,
                            file_read_prefix=file_read_prefix,
                        ),
                        sort_ts,
                    )

                elif block_type in ("image", "input_image"):
                    _, images = extract_text_and_images(
                        [block], session_dir=session_dir, cwd=session_cwd
                    )
                    body_images.extend(images)

                else:
                    text, images = content_pair(block)
                    if text.strip():
                        body_parts.append(text)
                    body_images.extend(images)

            flush_body()
        except Exception as exc:
            report_record_failure(path, exc)
            continue

    return out


def get_claude_conversation(
    path: Path,
    records: list[dict[str, Any]] | None = None,
    subagents: dict[str, list[dict[str, Any]]] | None = None,
    session_cwd: str | None = None,
) -> list[Turn]:
    """Full Claude transcript with subagent turns merged inline and tagged."""
    if records is None:
        records = list(iter_jsonl(path))
    session_dir = path.parent if path.is_file() else path

    collected = _turns_from_records(path, records, session_dir, session_cwd)
    for agent_id, agent_records in (subagents or {}).items():
        label = _subagent_label(agent_records, agent_id)
        collected.extend(
            _turns_from_records(path, agent_records, session_dir, session_cwd, agent_label=label)
        )

    # Timestamps are ISO-8601 with a trailing Z, so lexical order is chronological.
    # Insertion order breaks ties and keeps records without a timestamp in place.
    collected.sort(key=lambda item: (item[0], item[1]))
    return [turn for _, _, turn in collected]


def _subagent_label(records: list[dict[str, Any]], agent_id: str) -> str:
    for obj in records:
        name = obj.get("attributionAgent")
        if isinstance(name, str) and name:
            return f"subagent: {name}"
    return f"subagent: {agent_id}"


def get_conversation(path: Path) -> list[Turn]:
    """Backwards-compatible entrypoint used by the v1 conversation helper."""
    return get_claude_conversation(path)


def load_session(path: Path) -> SessionData:
    """Load a Claude session with the same shape Grok and Codex provide."""
    records = list(iter_jsonl(path))
    subagents = load_subagent_records(path)
    scan = claude_scan_session(path, records, subagents)
    summary = scan["summary"]
    meta = scan["meta"]
    return {
        "agent": "claude",
        "path": path,
        "title": summary.get("title") or path.name,
        "turns": get_claude_conversation(
            path, records, subagents, session_cwd=meta.get("cwd") or None
        ),
        "summary": summary,
        "resources": scan["resources"],
        "artifacts": scan["artifacts"],
        "hunks": scan["hunks"],
        "terminal_logs": None,
        "recaps": None,
        "updates": scan["events"],
    }
