"""Central, agent-aware filesystem authorization."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from . import config
from .images import is_image_path

Agent = Literal["grok", "claude", "codex"]
AGENTS = frozenset(("grok", "claude", "codex"))


class AuthorizationError(Exception):
    """Base class for deterministic route authorization failures."""


class InvalidAgent(AuthorizationError):
    pass


class PathDenied(AuthorizationError):
    pass


class PathMissing(AuthorizationError):
    pass


@dataclass(frozen=True)
class AuthorizedSession:
    agent: Agent
    path: Path


def parse_agent(value: str | None) -> Agent:
    if value not in AGENTS:
        raise InvalidAgent("Unknown or missing agent")
    return cast(Agent, value)


def _resolved_existing(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise PathMissing("Path not found") from None
    except (OSError, RuntimeError, ValueError):
        raise PathDenied("Path not allowed") from None


def _relative_to_resolved(path: Path, root: Path) -> tuple[Path, tuple[str, ...]]:
    resolved = _resolved_existing(path)
    try:
        resolved_root = root.resolve(strict=True)
        relative = resolved.relative_to(resolved_root)
    except (FileNotFoundError, NotADirectoryError):
        raise PathMissing("Session root not found") from None
    except (OSError, RuntimeError, ValueError):
        raise PathDenied("Path not allowed") from None
    return resolved, relative.parts


def resolve_session_path(agent: Agent, requested: str) -> AuthorizedSession:
    """Resolve a path only when it matches the selected agent's session layout."""
    if not requested or "\x00" in requested:
        raise PathDenied("Path not allowed")
    path = Path(requested).expanduser()

    if agent == "grok":
        resolved, parts = _relative_to_resolved(path, config.GROK_HOME / "sessions")
        recognized = len(parts) == 2 and resolved.is_dir()
    elif agent == "claude":
        resolved, parts = _relative_to_resolved(path, config.CLAUDE_HOME / "projects")
        is_jsonl = resolved.is_file() and resolved.suffix.lower() == ".jsonl"
        # <project>/<session>.jsonl, or a subagent transcript recorded beside it at
        # <project>/<session>/subagents/agent-<id>.jsonl. Nothing else in between.
        recognized = is_jsonl and (
            len(parts) == 2
            or (len(parts) == 4 and parts[2] == "subagents" and parts[3].startswith("agent-"))
        )
    else:
        candidates = (
            config.CODEX_HOME / "sessions",
            config.CODEX_HOME / "archived_sessions",
        )
        resolved = _resolved_existing(path)
        parts = ()
        recognized = False
        for root in candidates:
            try:
                root_resolved = root.resolve(strict=True)
                parts = resolved.relative_to(root_resolved).parts
            except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError, ValueError):
                continue
            if (
                parts
                and resolved.is_file()
                and resolved.suffix.lower() == ".jsonl"
                and resolved.name.startswith("rollout-")
            ):
                recognized = True
                break

    if not recognized:
        raise PathDenied("Path does not match a recognized session")
    return AuthorizedSession(agent=agent, path=resolved)


def resolve_raw_path(session: AuthorizedSession) -> Path:
    """Derive a downloadable raw file from an already-authorized session."""
    if session.agent != "grok":
        return session.path
    for name in ("chat_history.jsonl", "summary.json"):
        candidate = session.path / name
        try:
            if candidate.is_file():
                resolved = _resolved_existing(candidate)
                resolved.relative_to(session.path)
                return resolved
        except ValueError:
            raise PathDenied("Raw file escapes the session") from None
        except OSError:
            continue
    raise PathMissing("No raw file for this session")


def resolve_media_path(session: AuthorizedSession, requested: str) -> Path:
    """Authorize passive image media in a session-specific filesystem boundary."""
    if not requested or "\x00" in requested:
        raise PathDenied("Path not allowed")
    path = _resolved_existing(Path(requested).expanduser())
    if not path.is_file():
        raise PathMissing("Image not found")
    if not is_image_path(path):
        raise PathDenied("Not a supported image file")

    roots: tuple[Path, ...]
    if session.agent == "grok":
        roots = (session.path,)
    elif session.agent == "claude":
        # Subagent transcripts live two levels below the project directory that owns
        # any associated media, so authorize against the project directory itself.
        parent = session.path.parent
        if parent.name == "subagents":
            project = parent.parent.parent
            session_id = parent.parent.name
        else:
            project = parent
            session_id = session.path.stem
        # Pasted images are cached outside the projects tree, in a directory named
        # by the owning session id; only that session's cache is in bounds.
        roots = (project, config.CLAUDE_HOME / "image-cache" / session_id)
    else:
        roots = (
            session.path.parent,
            config.CODEX_HOME / "generated_images",
        )

    for root in roots:
        try:
            path.relative_to(root.resolve(strict=True))
            return path
        except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError, ValueError):
            continue

    if session.agent == "codex" and path.name.lower().startswith("codex-clipboard-"):
        temp_root = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
        try:
            path.relative_to(temp_root.resolve(strict=True))
            return path
        except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError, ValueError):
            pass

    raise PathDenied("Image is not associated with this session")
