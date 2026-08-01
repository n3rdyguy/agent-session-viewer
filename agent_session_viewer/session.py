"""Common session loading and Markdown export."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path

from .agents.loaders import LOADERS
from .images import rebind_turn_media_links
from .markdown_output import format_markdown_content
from .registry import AGENT_SPECS, spec_for
from .types import SessionData, Turn, empty_session
from .util import collect_parse_diagnostics

LOGGER = logging.getLogger(__name__)


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


def _normalize_prompt_text(value: object) -> str:
    """Collapse whitespace and strip a leading shell ``$ `` prefix for matching."""
    text = str(value or "").strip()
    if text.startswith("$ "):
        text = text[2:].lstrip()
    return " ".join(text.split())


def attach_prompt_history_anchors(
    history: list[dict] | None,
    turns: list[Turn] | list[dict] | None,
) -> list[dict]:
    """Attach ``turn_anchor`` ids so prompt-history rows can deep-link into chat.

    Prefers text matches against plain user turns, then fills remaining non-bash
    rows in chronological order.
    """
    if not history:
        return []
    turns = turns or []
    user_indices: list[int] = []
    for i, turn in enumerate(turns):
        role = str(turn.get("role") or "").strip().lower()
        if role != "user":
            continue
        if is_system_panel_turn(turn):
            continue
        user_indices.append(i)

    used: set[int] = set()
    linked: list[dict] = []
    for row in history:
        item = dict(row)
        display = _normalize_prompt_text(item.get("display"))
        is_bash = bool(item.get("is_bash")) or str(item.get("display") or "").startswith("$ ")
        anchor: str | None = None

        if display:
            for idx in user_indices:
                if idx in used:
                    continue
                turn_text = _normalize_prompt_text(turns[idx].get("text"))
                if not turn_text:
                    continue
                if (
                    display == turn_text
                    or turn_text.startswith(display)
                    or display.startswith(turn_text[:200])
                ):
                    anchor = f"turn-{idx}"
                    used.add(idx)
                    break

        if anchor is None and not is_bash:
            for idx in user_indices:
                if idx not in used:
                    anchor = f"turn-{idx}"
                    used.add(idx)
                    break

        if anchor:
            item["turn_anchor"] = anchor
        linked.append(item)
    return linked


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
        file_artifacts = t.get("file_artifacts") or []
        if file_artifacts:
            split_arts = [a for a in file_artifacts if a.get("split") and a.get("text")]
            # Prefer prefix (top non-file meta) over full text to avoid duplicating
            # file bodies that are exported as subsections below.
            prefix = t.get("file_read_prefix")
            if prefix is None:
                prefix = t.get("text") or ""
            if str(prefix).strip():
                lines.append(format_markdown_content(str(prefix), assume_markdown=assumes_markdown))
                lines.append("")
            for a in split_arts:
                label = a.get("label") or a.get("path") or "file"
                lines.append(f"#### {label}")
                lines.append("")
                path = str(a.get("path") or "")
                art_md = path.lower().endswith((".md", ".markdown", ".mdown", ".mkd"))
                lines.append(
                    format_markdown_content(str(a.get("text") or ""), assume_markdown=art_md)
                )
                lines.append("")
        else:
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
    # make_turn runs before agent/session are known to the path linker; rebind
    # /media links so path previews work for every agent.
    session_agent = str(session.get("agent") or agent)
    session_path = session.get("path") or path
    rebind_turn_media_links(session.get("turns"), agent=session_agent, session=session_path)
    rebind_turn_media_links(session.get("updates"), agent=session_agent, session=session_path)
    # Deep-link prompt-history rows to the matching user turns in chat.
    resources = session.get("resources")
    if isinstance(resources, dict) and resources.get("prompt_history"):
        resources = dict(resources)
        resources["prompt_history"] = attach_prompt_history_anchors(
            resources.get("prompt_history"),
            session.get("turns"),
        )
        session["resources"] = resources
    return session


def _load_session(agent: str, path: Path) -> SessionData:
    """Load a session while the caller owns diagnostic collection."""
    loader = LOADERS.get(agent)
    if loader is None:
        # Routes validate the agent first, so this is a defensive default.
        return empty_session(agent, path)
    session = loader(path)
    turns = session.get("turns")
    if spec_for(agent).system_panel and turns:
        session["turns"], session["system_artifacts"] = split_system_panel_turns(turns)
    return session


def summary_to_markdown(
    summary: dict[str, object] | None,
    *,
    agent: str,
    resources: dict[str, object] | None = None,
) -> str:
    """Shared export header (model/cwd/tokens/todos), with per-agent header rows."""
    if not summary:
        return ""

    spec = AGENT_SPECS.get(agent)
    if spec is None:
        return ""
    lines = [field.render(summary) for field in spec.summary_fields]

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
