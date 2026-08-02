"""One descriptor per supported agent.

Everything that used to be a repeated ``if agent == "..."`` chain reads from
:data:`AGENT_SPECS` instead: filesystem roots, path-shape authorization, media
boundaries, export headers, and the per-agent copy in the templates.

This module imports ``config`` and stdlib only. That is deliberate - ``util`` and
``discovery`` import it, and the parsers in ``agents/`` import *those*, so pulling
an agent module in here would close an import cycle. Loader wiring therefore lives
in ``agents/loaders.py``, one layer up the dependency chain.

Homes are read through ``getattr(config, ...)`` at call time rather than bound at
import, so tests can point a single module at a temporary directory.

Adding an agent: append a spec below, add a parser module exposing
``load_session(path) -> SessionData``, register it in ``agents/loaders.py`` and
``discovery._DISCOVERERS``, extend the ``Agent`` literal in ``authorization.py``,
and add one ``.badge`` rule to ``static/app.css``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from markupsafe import Markup

from . import config


@dataclass(frozen=True)
class SummaryField:
    """One row of the Markdown export header."""

    label: str
    key: str
    code: bool = False
    # Every row falls back to "-" for a missing value except the session id, which
    # renders the raw value. Preserved verbatim from the pre-registry export format.
    fallback: str | None = "-"

    def render(self, summary: dict[str, object]) -> str:
        value = summary.get(self.key)
        if self.fallback is not None:
            value = value or self.fallback
        return f"**{self.label}:** {f'`{value}`' if self.code else value}  "


_MODEL = SummaryField("Model", "model")
_CWD = SummaryField("CWD", "cwd", code=True)
_REASONING = SummaryField("Reasoning effort", "reasoning_effort")
_SESSION_ID = SummaryField("Session id", "id", code=True, fallback=None)


@dataclass(frozen=True)
class AgentSpec:
    """Everything the app needs to know about one agent."""

    id: str
    """Query-param value, badge CSS class, and discovery-cache namespace."""

    label: str
    """Display name for filter links and the console banner."""

    home_attr: str
    """Attribute on ``config`` holding this agent's home directory."""

    session_subdirs: tuple[str, ...]
    """Session roots the agent always has. The first is the "looking in" path."""

    validate: Callable[[Path, tuple[str, ...]], bool]
    """Does this resolved path, relative to a session root, look like a session?"""

    media_roots: Callable[[Path], tuple[Path, ...]]
    """Directories an image may live in, given an authorized session path."""

    summary_fields: tuple[SummaryField, ...]
    """Export header rows, in order."""

    hunks_label: str
    updates_label: str
    updates_hint: Markup
    updates_empty: str

    system_panel: bool = False
    """Whether injected system/developer turns are lifted out of the chat.

    Grok and Codex inject large system/developer/project-instruction blobs as chat
    turns; the view lifts those into a collapsed System panel. Claude does not
    persist the same injected base instructions in the transcript - its system turns
    (hooks, slash commands, reminders) stay inline in the chat.
    """

    optional_subdirs: tuple[str, ...] = ()
    """Session roots the agent creates lazily, if ever.

    Searched exactly like ``session_subdirs``; the distinction exists so an absent
    one is reported as "not created yet" rather than flagged as missing. Codex only
    creates ``archived_sessions`` the first time a session is archived.
    """

    raw_names: tuple[str, ...] = ()
    """Candidate filenames for /raw when the session is a directory, in preference
    order. Empty means the session path is itself the downloadable file."""

    media_fallback: Callable[[Path], bool] | None = None
    """Last-resort check for images stored outside every media root."""

    def home(self) -> Path:
        return getattr(config, self.home_attr)

    def root_specs(self) -> tuple[tuple[Path, bool], ...]:
        """``(path, optional)`` for every session root, required ones first."""
        home = self.home()
        return (
            *((home / sub, False) for sub in self.session_subdirs),
            *((home / sub, True) for sub in self.optional_subdirs),
        )

    def roots(self) -> tuple[Path, ...]:
        """Every session root to search, in preference order."""
        return tuple(path for path, _ in self.root_specs())

    def looking_in(self) -> Path:
        return self.roots()[0]

    def home_display(self) -> str:
        """``~/.grok`` when the home sits under the user's home directory."""
        home = self.home()
        try:
            return "~/" + home.relative_to(Path.home()).as_posix()
        except (OSError, RuntimeError, ValueError):
            return str(home)


# ─────────────────────────────────────────────
# Path-shape validators
# ─────────────────────────────────────────────


def _validate_grok(resolved: Path, parts: tuple[str, ...]) -> bool:
    """<encoded-cwd>/<session-id>/ - a directory, exactly two levels down."""
    return len(parts) == 2 and resolved.is_dir()


def _validate_claude(resolved: Path, parts: tuple[str, ...]) -> bool:
    """<project>/<session>.jsonl, or a subagent transcript recorded beside it at
    <project>/<session>/subagents/agent-<id>.jsonl. Nothing else in between."""
    if not (resolved.is_file() and resolved.suffix.lower() == ".jsonl"):
        return False
    return len(parts) == 2 or (
        len(parts) == 4 and parts[2] == "subagents" and parts[3].startswith("agent-")
    )


def _validate_codex(resolved: Path, parts: tuple[str, ...]) -> bool:
    """rollout-*.jsonl at any depth below a session root."""
    return bool(
        parts
        and resolved.is_file()
        and resolved.suffix.lower() == ".jsonl"
        and resolved.name.startswith("rollout-")
    )


def _validate_cursor(resolved: Path, parts: tuple[str, ...]) -> bool:
    """<project>/agent-transcripts/<session-id>/<session-id>.jsonl under projects/."""
    if not (resolved.is_file() and resolved.suffix.lower() == ".jsonl"):
        return False
    # parts relative to projects/: project, agent-transcripts, sid, sid.jsonl
    return (
        len(parts) == 4
        and parts[1] == "agent-transcripts"
        and parts[2] == resolved.stem
        and parts[3] == resolved.name
    )


# ─────────────────────────────────────────────
# Media boundaries
# ─────────────────────────────────────────────


def _grok_media_roots(session: Path) -> tuple[Path, ...]:
    return (session,)


def _claude_media_roots(session: Path) -> tuple[Path, ...]:
    # Subagent transcripts live two levels below the project directory that owns
    # any associated media, so authorize against the project directory itself.
    parent = session.parent
    if parent.name == "subagents":
        project = parent.parent.parent
        session_id = parent.parent.name
    else:
        project = parent
        session_id = session.stem
    # Pasted images are cached outside the projects tree, in a directory named by
    # the owning session id; only that session's cache is in bounds.
    return (project, config.CLAUDE_HOME / "image-cache" / session_id)


def _codex_media_roots(session: Path) -> tuple[Path, ...]:
    return (session.parent, config.CODEX_HOME / "generated_images")


def _codex_clipboard_fallback(path: Path) -> bool:
    """Codex writes pasted images to the system temp directory, not its own home."""
    if not path.name.lower().startswith("codex-clipboard-"):
        return False
    temp_root = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    try:
        path.relative_to(temp_root.resolve(strict=True))
    except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError, ValueError):
        return False
    return True


def _cursor_media_roots(session: Path) -> tuple[Path, ...]:
    # Transcript lives at …/agent-transcripts/<id>/<id>.jsonl; media may sit beside it
    # or under the project folder.
    return (session.parent, session.parent.parent.parent)


# ─────────────────────────────────────────────
# The registry
# ─────────────────────────────────────────────

# Markup because these carry developer-authored inline markup. They are module
# constants and never interpolate session content.
_UPDATES_JSONL_HINT = Markup(
    "Aggregated from <code>updates.jsonl</code> (stream chunks collapsed; tool ids preserved)."
)

GROK = AgentSpec(
    id="grok",
    label="Grok",
    home_attr="GROK_HOME",
    session_subdirs=("sessions",),
    validate=_validate_grok,
    media_roots=_grok_media_roots,
    raw_names=("chat_history.jsonl", "summary.json"),
    system_panel=True,
    summary_fields=(
        _MODEL,
        _CWD,
        SummaryField("Agent", "agent_name"),
        _REASONING,
        _SESSION_ID,
    ),
    hunks_label="Hunk records",
    updates_label="Updates stream",
    updates_hint=_UPDATES_JSONL_HINT,
    updates_empty="No updates.jsonl events found.",
)

CLAUDE = AgentSpec(
    id="claude",
    label="Claude",
    home_attr="CLAUDE_HOME",
    session_subdirs=("projects",),
    validate=_validate_claude,
    media_roots=_claude_media_roots,
    system_panel=False,
    summary_fields=(
        _MODEL,
        _CWD,
        SummaryField("Permission mode", "sandbox_profile"),
        _REASONING,
        SummaryField("Branch", "head_branch"),
        SummaryField("CLI version", "cli_version"),
        _SESSION_ID,
    ),
    hunks_label="Hunk records",
    updates_label="Events timeline",
    updates_hint=Markup(
        "Permission-mode changes, queue operations, hook summaries, turn durations, "
        "file-history snapshots, attachments, and subagent runs from the transcript "
        "(not the full chat - see Chat history)."
    ),
    updates_empty="No timeline events found.",
)

CODEX = AgentSpec(
    id="codex",
    label="Codex",
    home_attr="CODEX_HOME",
    session_subdirs=("sessions",),
    optional_subdirs=("archived_sessions",),
    validate=_validate_codex,
    media_roots=_codex_media_roots,
    media_fallback=_codex_clipboard_fallback,
    system_panel=True,
    summary_fields=(
        _MODEL,
        _CWD,
        SummaryField("Originator", "agent_name"),
        _REASONING,
        SummaryField("Sandbox", "sandbox_profile"),
        _SESSION_ID,
    ),
    hunks_label="Patches",
    updates_label="Events timeline",
    updates_hint=Markup(
        "Task / patch / image events from the Codex rollout (not the full chat - see Chat history)."
    ),
    updates_empty="No timeline events found.",
)

CURSOR = AgentSpec(
    id="cursor",
    label="Cursor",
    home_attr="CURSOR_HOME",
    session_subdirs=("projects",),
    validate=_validate_cursor,
    media_roots=_cursor_media_roots,
    system_panel=False,
    summary_fields=(
        _MODEL,
        _CWD,
        SummaryField("Agent", "agent_name"),
        _SESSION_ID,
    ),
    hunks_label="Hunk records",
    updates_label="Events timeline",
    updates_hint=Markup(
        "Cursor agent-transcript JSONL under <code>projects/…/agent-transcripts/</code> "
        "(chat only; desktop SQLite stores are not browsed)."
    ),
    updates_empty="No timeline events found.",
)

# Insertion order drives the filter links, the empty-state paths, and the console
# banner, and matches the order sessions are discovered in.
AGENT_SPECS: dict[str, AgentSpec] = {
    spec.id: spec for spec in (GROK, CLAUDE, CODEX, CURSOR)
}
AGENT_IDS: frozenset[str] = frozenset(AGENT_SPECS)


def spec_for(agent: str) -> AgentSpec:
    """Descriptor for an agent id that has already been validated by ``parse_agent``."""
    return AGENT_SPECS[agent]


def all_homes() -> tuple[Path, ...]:
    return tuple(spec.home() for spec in AGENT_SPECS.values())
