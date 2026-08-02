"""Central, agent-aware filesystem authorization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .images import is_image_path
from .registry import AGENT_IDS, spec_for

# Hand-written because a Literal cannot be derived from the registry dict at type
# check time. tests/test_registry.py asserts the two stay in step.
Agent = Literal["grok", "claude", "codex", "cursor"]
AGENTS = AGENT_IDS


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
    spec = spec_for(agent)
    roots = spec.roots()

    if len(roots) == 1:
        # A single mandatory root: an absent root is a distinct failure worth
        # reporting as missing rather than denied.
        resolved, parts = _relative_to_resolved(path, roots[0])
        recognized = spec.validate(resolved, parts)
    else:
        # Several optional roots (Codex archives sessions into a second tree, which
        # need not exist). Skip roots that do not resolve and try the next one.
        resolved = _resolved_existing(path)
        recognized = False
        for root in roots:
            try:
                parts = resolved.relative_to(root.resolve(strict=True)).parts
            except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError, ValueError):
                continue
            if spec.validate(resolved, parts):
                recognized = True
                break

    if not recognized:
        raise PathDenied("Path does not match a recognized session")
    return AuthorizedSession(agent=agent, path=resolved)


def resolve_raw_path(session: AuthorizedSession) -> Path:
    """Derive a downloadable raw file from an already-authorized session."""
    raw_names = spec_for(session.agent).raw_names
    if not raw_names:
        # The session path is itself the transcript file.
        return session.path
    for name in raw_names:
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

    spec = spec_for(session.agent)
    for root in spec.media_roots(session.path):
        try:
            path.relative_to(root.resolve(strict=True))
            return path
        except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError, ValueError):
            continue

    if spec.media_fallback is not None and spec.media_fallback(path):
        return path

    raise PathDenied("Image is not associated with this session")
