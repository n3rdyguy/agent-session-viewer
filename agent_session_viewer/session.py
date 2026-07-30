"""Common session loading and Markdown export."""

from __future__ import annotations

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
from .util import collect_parse_diagnostics, iter_jsonl


def turns_to_markdown(turns: list[dict], title: str, agent: str, path: str, extra: str = "") -> str:
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


def load_session(agent: str, path: Path) -> dict:
    """
    Load everything the view/export routes need for one session.

    Returns turns, title, summary, resources, artifacts, hunks,
    terminal_logs, recaps, and updates (timeline / events tab).
    """
    with collect_parse_diagnostics() as diagnostics:
        session = _load_session(agent, path)
    session["diagnostics"] = diagnostics
    return session


def _load_session(agent: str, path: Path) -> dict:
    """Load a session while the caller owns diagnostic collection."""
    title = path.name
    turns = []
    summary = None
    resources = None
    artifacts = None
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
    return {
        "agent": agent,
        "path": path,
        "title": title,
        "turns": turns,
        "summary": summary,
        "resources": resources,
        "artifacts": artifacts,
        "hunks": hunks,
        "terminal_logs": terminal_logs,
        "recaps": recaps,
        "updates": updates,
    }


def summary_to_markdown(
    summary: dict | None,
    *,
    agent: str,
    resources: dict | None = None,
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
