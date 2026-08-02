"""Cursor agent-transcript scanning and parsing.

Cursor stores agent chats under::

    ~/.cursor/projects/<project-id>/agent-transcripts/<session-id>/<session-id>.jsonl

Records look like Claude-style role/message envelopes with content blocks
(``text``, ``tool_use``, ``tool_result``). User prompts are often wrapped in
``<user_query>…</user_query>``.

Desktop SQLite (``state.vscdb``) and CLI ``store.db`` stores are not loaded here —
only the on-disk agent-transcript JSONL tree under ``CURSOR_HOME/projects``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..images import extract_text
from ..turns import format_tool_args, make_turn
from ..types import SessionData, Turn, empty_session
from ..util import (
    collect_parse_diagnostics,
    display_time,
    empty_token_usage,
    finalize_token_usage,
    human_time,
    iter_jsonl,
    report_record_failure,
)

_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
_TIMESTAMP_RE = re.compile(r"<timestamp>\s*(.*?)\s*</timestamp>", re.DOTALL | re.IGNORECASE)
_BLOCKED_USER_PREFIXES = (
    "<environment_context",
    "<user_instructions",
    "<system_reminder",
    "<manually_attached_skills",
    "<timestamp",
)
_TRANSCRIPT_DIR = "agent-transcripts"


def is_cursor_transcript(path: Path) -> bool:
    """``…/agent-transcripts/<id>/<id>.jsonl`` under a Cursor projects tree."""
    try:
        if not (path.is_file() and path.suffix.lower() == ".jsonl"):
            return False
    except OSError:
        return False
    if path.parent.name != path.stem:
        return False
    return path.parent.parent.name == _TRANSCRIPT_DIR


def project_id_from_transcript(path: Path) -> str:
    """Folder name under ``projects/`` (encoded workspace id)."""
    # …/projects/<project>/agent-transcripts/<sid>/<sid>.jsonl
    try:
        return path.parent.parent.parent.name
    except (IndexError, AttributeError):
        return ""


def decode_project_cwd_hint(project_id: str) -> str:
    """Best-effort path from Cursor's project folder slug (lossy for hyphens)."""
    if not project_id:
        return ""
    # e.g. c-Users-Martin-projects-apps-mjrr-dk → C:\Users\Martin\projects\...
    if len(project_id) > 2 and project_id[1] == "-" and project_id[0].isalpha():
        drive = project_id[0].upper() + ":\\"
        rest = project_id[2:].replace("-", "\\")
        return drive + rest
    return project_id.replace("-", "/")


def extract_user_query(text: str) -> str:
    """Pull ``<user_query>`` bodies; drop pure environment wrappers."""
    if not text:
        return ""
    matches = _USER_QUERY_RE.findall(text)
    if matches:
        return "\n".join(m.strip() for m in matches if m.strip())
    stripped = text.lstrip()
    if stripped.startswith(_BLOCKED_USER_PREFIXES):
        # May still contain a query after wrappers
        if "<user_query>" in text:
            return extract_user_query(text[text.find("<user_query>") :])
        return ""
    return text.strip()


def extract_timestamp_label(text: str) -> str:
    match = _TIMESTAMP_RE.search(text or "")
    return match.group(1).strip() if match else ""


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if isinstance(content, dict):
        return [content]
    if isinstance(content, str) and content.strip():
        return [{"type": "text", "text": content}]
    return []


def _block_text(block: dict[str, Any]) -> str:
    for key in ("text", "content", "output"):
        val = block.get(key)
        if isinstance(val, str):
            return val
    return extract_text(block.get("text") or block.get("content") or "")


def load_session(path: Path) -> SessionData:
    """Parse a Cursor agent-transcript JSONL into the shared session shape."""
    if not is_cursor_transcript(path):
        return empty_session("cursor", path)

    with collect_parse_diagnostics() as diagnostics:
        turns: list[Turn] = []
        first_user = ""
        cwd = ""
        model = ""
        token_usage = empty_token_usage()
        line_no = 0

        for line_no, rec in enumerate(iter_jsonl(path), start=1):
            if not isinstance(rec, dict):
                report_record_failure(path, line_no, "non_object", "Cursor record is not an object")
                continue
            try:
                role = str(rec.get("role") or "").lower()
                if role in {"system", "developer"}:
                    # Keep system/developer out of the main chat (noise / instructions).
                    continue
                if role not in {"user", "assistant", "tool"}:
                    continue

                message = rec.get("message") if isinstance(rec.get("message"), dict) else rec
                content = message.get("content") if isinstance(message, dict) else rec.get("content")
                blocks = _content_blocks(content)
                texts: list[str] = []
                time_label = ""

                if role == "user":
                    raw_parts: list[str] = []
                    for block in blocks:
                        if block.get("type") in {"text", "input_text", None} or "text" in block:
                            raw_parts.append(_block_text(block))
                    raw = "\n".join(p for p in raw_parts if p)
                    time_label = extract_timestamp_label(raw)
                    query = extract_user_query(raw)
                    if not query:
                        continue
                    if not first_user:
                        first_user = query
                    texts.append(query)
                    turns.append(
                        make_turn(
                            role="user",
                            text="\n".join(texts),
                            time=time_label or display_time(rec.get("timestamp") or rec.get("time")),
                        )
                    )
                    continue

                if role == "tool":
                    # Standalone tool result rows
                    result_text = extract_text(content) if content is not None else ""
                    tool_id = str(rec.get("tool_call_id") or rec.get("call_id") or "")
                    turns.append(
                        make_turn(
                            role="tool_result",
                            text=result_text or "(empty tool result)",
                            meta=f"call {tool_id}" if tool_id else "",
                            time=display_time(rec.get("timestamp") or rec.get("time")),
                        )
                    )
                    continue

                # assistant
                if isinstance(rec.get("model"), str) and rec["model"] and not model:
                    model = rec["model"]
                if isinstance(message, dict) and isinstance(message.get("model"), str):
                    if message["model"] and not model:
                        model = message["model"]

                for block in blocks:
                    btype = str(block.get("type") or "")
                    if btype in {"thinking", "reasoning", "redacted_thinking", "signature"}:
                        continue
                    if btype in {"text", "input_text", "output_text", ""} and (
                        "text" in block or btype.startswith("text") or btype.endswith("text")
                    ):
                        t = _block_text(block)
                        if t and t.strip() and t.strip() != "[REDACTED]":
                            texts.append(t)
                    elif btype in {"tool_use", "tool_call"}:
                        if texts:
                            turns.append(
                                make_turn(
                                    role="assistant",
                                    text="\n\n".join(texts),
                                    model=model,
                                    time=display_time(rec.get("timestamp")),
                                )
                            )
                            texts = []
                        name = str(block.get("name") or "tool")
                        call_id = str(block.get("id") or block.get("call_id") or "")
                        args = block.get("input", block.get("arguments", {}))
                        # Cwd hint from shell-style tools
                        if not cwd and isinstance(args, dict):
                            wd = args.get("working_directory") or args.get("cwd")
                            if isinstance(wd, str) and wd.strip():
                                cwd = wd.strip()
                            tdir = args.get("target_directory") or args.get("path")
                            if not cwd and isinstance(tdir, str) and len(tdir) > 3:
                                # Prefer parent of a file path only as last resort later
                                pass
                        turns.append(
                            make_turn(
                                role="tool",
                                text=format_tool_args(args),
                                meta=f"{name}" + (f" · {call_id}" if call_id else ""),
                                model=model,
                                time=display_time(rec.get("timestamp")),
                            )
                        )
                    elif btype in {"tool_result", "tool_output"}:
                        if texts:
                            turns.append(
                                make_turn(
                                    role="assistant",
                                    text="\n\n".join(texts),
                                    model=model,
                                    time=display_time(rec.get("timestamp")),
                                )
                            )
                            texts = []
                        tid = str(block.get("tool_use_id") or block.get("call_id") or "")
                        turns.append(
                            make_turn(
                                role="tool_result",
                                text=_block_text(block) or extract_text(block.get("content")),
                                meta=f"call {tid}" if tid else "",
                                time=display_time(rec.get("timestamp")),
                            )
                        )

                # tool_calls array (OpenAI-ish)
                top_calls = rec.get("tool_calls")
                if isinstance(top_calls, list):
                    if texts:
                        turns.append(
                            make_turn(
                                role="assistant",
                                text="\n\n".join(texts),
                                model=model,
                                time=display_time(rec.get("timestamp")),
                            )
                        )
                        texts = []
                    for call in top_calls:
                        if not isinstance(call, dict):
                            continue
                        function = (
                            call.get("function")
                            if isinstance(call.get("function"), dict)
                            else call
                        )
                        name = str(function.get("name") or "tool")
                        turns.append(
                            make_turn(
                                role="tool",
                                text=format_tool_args(
                                    function.get("arguments", function.get("input", {}))
                                ),
                                meta=name,
                                model=model,
                            )
                        )

                if texts:
                    turns.append(
                        make_turn(
                            role="assistant",
                            text="\n\n".join(texts),
                            model=model,
                            time=display_time(rec.get("timestamp")),
                        )
                    )
            except Exception as exc:  # noqa: BLE001 — one bad record must not kill the session
                report_record_failure(path, line_no, "parse_error", str(exc))

        if not cwd:
            cwd = decode_project_cwd_hint(project_id_from_transcript(path))

        try:
            mtime = path.stat().st_mtime
            updated = human_time(mtime)
        except OSError:
            updated = ""

        title = first_user.splitlines()[0][:120] if first_user else path.stem
        summary = {
            "id": path.stem,
            "model": model or None,
            "cwd": cwd or None,
            "title": title,
            "messages": len(turns),
            "updated": updated,
            "agent_name": "Cursor",
            "token_usage": finalize_token_usage(token_usage),
        }

        return {
            "agent": "cursor",
            "path": path,
            "title": title,
            "turns": turns,
            "summary": summary,
            "resources": None,
            "artifacts": None,
            "system_artifacts": None,
            "hunks": None,
            "terminal_logs": None,
            "recaps": None,
            "updates": None,
            "diagnostics": list(diagnostics),
        }
