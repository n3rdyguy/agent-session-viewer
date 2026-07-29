"""Filesystem locations used by the local session viewer."""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
GROK_HOME = Path(os.environ.get("GROK_HOME", HOME / ".grok")).expanduser()
CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", HOME / ".claude")).expanduser()
CODEX_HOME = Path(os.environ.get("CODEX_HOME", HOME / ".codex")).expanduser()
