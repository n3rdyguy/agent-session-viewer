"""Common session loading and Markdown export."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path

from .agents.claude import load_session as load_claude_session
from .agents.codex import codex_scan_session, get_codex_conversation
from .agents.grok import (
    get_grok_conversation,
    grok_hunk_records,
    grok_recap_requests,
    grok_resources,
    grok_summary_card,
    grok_terminal_logs,
    grok_updates_timeline,
)
from .markdown_output import format_markdown_content
from .types import SessionData, Turn
from .util import collect_parse_diagnostics, iter_jsonl

LOGGER = logging.getLogger(__name__)

# Grok/Codex inject large system/developer/project-instruction blobs as chat
# turns; the view lifts those into a collapsed System panel. Claude does not
# persist the same injected base instructions in the transcript — its system
# turns (hooks, slash commands, reminders) stay inline in the chat.
_SYSTEM_PANEL_AGENTS = frozenset({"grok", "codex"})


def is_system_panel_turn(turn: Turn | dict) -> bool:
    """True for system / developer / synthetic user-instruction turns."""
    role = str(turn.get("role") or "").strip().lower()
    if role in ("system", "system_reminder", "developer"):
        return True
    # Grok: synthetic "user (project_instructions)", "user (user_info)", …
    if role.startswith("user (") and role.endswith(")"):
        return True
    # Fallback: Grok environment block recorded as a plain user turn.
    text = str(turn.get("text") or "").lstrip()
    if role == "user" and text.startswith("<user_info>"):
        return True
    return False


def system_turn_title(turn: Turn | dict) -> str:
    """Human label for a system-panel artifact built from a chat turn."""
    meta = str(turn.get("meta") or "").strip()
    role = str(turn.get("role") or "system").strip()
    if meta and meta not in (turn.get("id"),):
        return meta.replace("_", " ")
    role_l = role.lower()
    if role_l.startswith("user (") and role_l.endswith(")"):
        return role[6:-1].replace("_", " ")
    text = str(turn.get("text") or "").lstrip()
    if role_l == "user" and text.startswith("<user_info>"):
        return "user info"
    return role.replace("_", " ")


def turns_to_system_artifacts(turns: list[Turn]) -> list[dict]:
    """Map system-ish turns onto the shared artifact-doc shape."""
    docs: list[dict] = []
    for index, turn in enumerate(turns, 1):
        if not is_system_panel_turn(turn):
            continue
        title = system_turn_title(turn)
        subtitle_parts = [p for p in (turn.get("time"), turn.get("id")) if p]
        docs.append(
            {
                "id": str(turn.get("id") or f"system-{index}"),
                "title": title,
                "subtitle": " · ".join(str(p) for p in subtitle_parts),
                "kind": "markdown",
                "text": str(turn.get("text") or ""),
                "role": str(turn.get("role") or "system"),
                "meta": str(turn.get("meta") or ""),
            }
        )
    return docs


def split_system_panel_turns(turns: list[Turn]) -> tuple[list[Turn], list[dict]]:
    """Return (chat_turns, system_artifacts) for Grok/Codex views."""
    chat: list[Turn] = []
    system_turns: list[Turn] = []
    for turn in turns:
        if is_system_panel_turn(turn):
            system_turns.append(turn)
        else:
            chat.append(turn)
    return chat, turns_to_system_artifacts(system_turns)


def turns_to_markdown(turns: list[Turn], title: str, agent: str, path: str, extra: str = "") -> str:
    lines = [
        f"# {title}",
        "",
        f"**Agent:** {agent}  ",
        f"**Path:** `{path}`  ",
        f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if extra:
        lines.extend([extra, ""])
    lines.extend(["---", ""])
    for t in turns:
        turn_role = str(t["role"] or "")
        role = turn_role.upper()
        header = f"### {role}"
        if t.get("time"):
            header += f" · {t['time']}"
        if t.get("id"):
            header += f" · `{t['id']}`"
        lines.append(header)
        if t.get("model"):
            lines.append(f"*Model: {t['model']}*")
        if t.get("meta"):
            lines.append(f"*Meta: {t['meta']}*")
        lines.append("")
        assumes_markdown = turn_role.lower() in {
            "system",
            "system_reminder",
            "developer",
        }
        lines.append(format_markdown_content(t["text"], assume_markdown=assumes_markdown))
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Session load (shared by /view and /export)
# ─────────────────────────────────────────────


def load_session(agent: str, path: Path) -> SessionData:
    """
    Load everything the view/export routes need for one session.

    Returns turns, title, summary, resources, artifacts, hunks,
    terminal_logs, recaps, and updates (timeline / events tab).
    """
    started = time.perf_counter()
    try:
        with collect_parse_diagnostics() as diagnostics:
            session = _load_session(agent, path)
    finally:
        if os.environ.get("ASV_TIMING_DEBUG", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            LOGGER.info(
                "session loading (%s) completed in %.1f ms",
                agent,
                (time.perf_counter() - started) * 1000,
            )
    session["diagnostics"] = diagnostics
    return session


def _load_session(agent: str, path: Path) -> SessionData:
    """Load a session while the caller owns diagnostic collection."""
    title = path.name
    turns: list[Turn] = []
    summary = None
    resources = None
    artifacts = None
    system_artifacts: list[dict] | None = None
    hunks = None
    terminal_logs = None
    recaps = None
    updates = None

    if agent == "claude" and path.is_file():
        return load_claude_session(path)

    if agent == "codex" and path.is_file():
        # Parse and decode the rollout once, then reuse those records for both
        # the transcript and its summary/tokens/events/patches.
        records = list(iter_jsonl(path))
        scan = codex_scan_session(path, records)
        meta = scan.get("meta") if isinstance(scan.get("meta"), dict) else {}
        turns = get_codex_conversation(path, records, session_cwd=meta.get("cwd"))
        summary = scan["summary"]
        title = summary.get("title") or title
        resources = scan["resources"]
        artifacts = scan.get("artifacts") or []
        hunks = scan["hunks"]
        # Reuse "updates" tab for Codex task/patch/image timeline
        updates = scan["events"]
    elif agent == "grok":
        turns = get_grok_conversation(path)

    if agent == "grok" and path.is_dir():
        summary = grok_summary_card(path)
        title = summary.get("title") or title
        resources = grok_resources(path)
        artifacts = (resources or {}).get("artifacts") or []
        hunks = grok_hunk_records(path)
        terminal_logs = grok_terminal_logs(path)
        recaps = grok_recap_requests(path)
        updates = grok_updates_timeline(path)

    if agent in _SYSTEM_PANEL_AGENTS and turns:
        turns, system_artifacts = split_system_panel_turns(turns)

    return {
        "agent": agent,
        "path": path,
        "title": title,
        "turns": turns,
        "summary": summary,
        "resources": resources,
        "artifacts": artifacts,
        "system_artifacts": system_artifacts,
        "hunks": hunks,
        "terminal_logs": terminal_logs,
        "recaps": recaps,
        "updates": updates,
    }


def summary_to_markdown(
    summary: dict[str, object] | None,
    *,
    agent: str,
    resources: dict[str, object] | None = None,
) -> str:
    """Shared export header (model/cwd/tokens/todos) for Grok and Codex."""
    if not summary:
        return ""

    if agent == "grok":
        lines = [
            f"**Model:** {summary.get('model') or '—'}  ",
            f"**CWD:** `{summary.get('cwd') or '—'}`  ",
            f"**Agent:** {summary.get('agent_name') or '—'}  ",
            f"**Reasoning effort:** {summary.get('reasoning_effort') or '—'}  ",
            f"**Session id:** `{summary.get('id')}`  ",
        ]
    elif agent == "codex":
        lines = [
            f"**Model:** {summary.get('model') or '—'}  ",
            f"**CWD:** `{summary.get('cwd') or '—'}`  ",
            f"**Originator:** {summary.get('agent_name') or '—'}  ",
            f"**Reasoning effort:** {summary.get('reasoning_effort') or '—'}  ",
            f"**Sandbox:** {summary.get('sandbox_profile') or '—'}  ",
            f"**Session id:** `{summary.get('id')}`  ",
        ]
    elif agent == "claude":
        lines = [
            f"**Model:** {summary.get('model') or '—'}  ",
            f"**CWD:** `{summary.get('cwd') or '—'}`  ",
            f"**Permission mode:** {summary.get('sandbox_profile') or '—'}  ",
            f"**Reasoning effort:** {summary.get('reasoning_effort') or '—'}  ",
            f"**Branch:** {summary.get('head_branch') or '—'}  ",
            f"**CLI version:** {summary.get('cli_version') or '—'}  ",
            f"**Session id:** `{summary.get('id')}`  ",
        ]
    else:
        return ""

    tok = summary.get("tokens") or {}
    if tok.get("available"):
        lines.extend(
            [
                "",
                "### Estimated token usage",
                f"- **Input:** {tok.get('input_fmt')}  ",
                f"- **Output:** {tok.get('output_fmt')}  ",
                f"- **Cached read:** {tok.get('cached_fmt')}  ",
                f"- **Reasoning:** {tok.get('reasoning_fmt')}  ",
                f"- **Uncached input:** {tok.get('uncached_fmt')}  ",
                f"- **Total:** {tok.get('total_fmt')}  ",
                f"- *Source: {tok.get('source')}*",
            ]
        )

    if resources and resources.get("todos"):
        lines.append("")
        lines.append("### Todos")
        for t in resources["todos"]:
            mark = "x" if t.get("status") == "completed" else " "
            lines.append(f"- [{mark}] `{t.get('id')}` {t.get('content')} ({t.get('status')})")

    return "\n".join(lines)


def system_artifacts_to_markdown(system_artifacts: list[dict] | None) -> str:
    """Export lifted system instructions for Grok/Codex markdown downloads."""
    if not system_artifacts:
        return ""
    lines = ["### System instructions", ""]
    for doc in system_artifacts:
        title = doc.get("title") or "system"
        subtitle = doc.get("subtitle") or ""
        header = f"#### {title}"
        if subtitle:
            header += f" · {subtitle}"
        lines.append(header)
        lines.append("")
        text = str(doc.get("text") or "")
        lines.append(format_markdown_content(text, assume_markdown=True))
        lines.append("")
    return "\n".join(lines).rstrip()
