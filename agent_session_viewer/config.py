"""Filesystem locations used by the local session viewer."""

from __future__ import annotations

import os
import re
from pathlib import Path

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_dotenv(path: Path | None = None) -> Path | None:
    """Load a local .env file without replacing explicit process variables.

    The working directory is intentional: it makes source, module, and installed
    console entrypoints use the same project-local override file.
    """
    env_path = path or (Path.cwd() / ".env")
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return None
    except OSError:
        return None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY.fullmatch(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)
    return env_path


DOTENV_PATH = load_dotenv()
HOME = Path.home()
GROK_HOME = Path(os.environ.get("GROK_HOME", HOME / ".grok")).expanduser()
CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", HOME / ".claude")).expanduser()
CODEX_HOME = Path(os.environ.get("CODEX_HOME", HOME / ".codex")).expanduser()
