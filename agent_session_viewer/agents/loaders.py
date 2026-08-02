"""Loader wiring for the agent parsers.

Separate from ``registry.py`` on purpose: the parsers below import ``util`` and
``discovery``, and those import the registry, so the registry cannot import them
back. This module sits above all of it and is imported only by ``session.py``.

Every parser exposes the same entrypoint - ``load_session(path) -> SessionData`` -
which is the whole agent interface. There is no base class or protocol; the shared
shape is the ``SessionData`` TypedDict.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..types import SessionData
from . import claude, codex, cursor, grok

LOADERS: dict[str, Callable[[Path], SessionData]] = {
    "grok": grok.load_session,
    "claude": claude.load_session,
    "codex": codex.load_session,
    "cursor": cursor.load_session,
}
