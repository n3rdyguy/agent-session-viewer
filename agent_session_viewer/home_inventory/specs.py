"""Descriptors for agent homes scanned by the inventory page.

Separate from session ``AGENT_SPECS``: Cursor is inventory-only, and the paths here
are config surface area rather than session trees.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import config


@dataclass(frozen=True)
class SkillRoot:
    """One directory that may contain skill packages (``*/SKILL.md``)."""

    rel: str
    """Path relative to the agent home."""

    source: str
    """Tag shown in the UI: user, bundled, skills-cursor, …"""


@dataclass(frozen=True)
class HomeSpec:
    """Everything the inventory scanner needs for one agent home."""

    id: str
    label: str
    home_attr: str
    """Attribute on ``config`` holding this agent's home directory."""

    badge: str
    """Short badge text (e.g. GR, CL)."""

    settings_files: tuple[str, ...] = ()
    """Config filenames relative to home (JSON or TOML)."""

    instruction_names: tuple[str, ...] = ()
    """Instruction / memory filenames at home root."""

    skill_roots: tuple[SkillRoot, ...] = ()
    hooks_json: tuple[str, ...] = ()
    """Standalone hook JSON files (Cursor) or globs resolved as exact paths."""

    hooks_dir: str | None = None
    """Directory of ``*.json`` hook files (Grok)."""

    disabled_hooks_file: str | None = None
    rules_dirs: tuple[str, ...] = ()
    agents_dirs: tuple[str, ...] = ()
    commands_dirs: tuple[str, ...] = ()
    plugin_meta_files: tuple[str, ...] = ()
    """JSON metadata files listing installed plugins (not cache trees)."""

    version_files: tuple[str, ...] = ()
    mcp_json_files: tuple[str, ...] = ()
    statusline_files: tuple[str, ...] = ()
    notable_dirs: tuple[str, ...] = ()
    """Dirs listed in the home map (existence + shallow child count only)."""

    def home(self) -> Path:
        return getattr(config, self.home_attr)

    def home_display(self) -> str:
        home = self.home()
        try:
            return "~/" + home.relative_to(Path.home()).as_posix()
        except (OSError, RuntimeError, ValueError):
            return str(home)


_INSTRUCTION_COMMON = (
    "AGENTS.md",
    "AGENTS.override.md",
    "AGENTS__.md",
    "AGENT.md",
    "Agents.md",
    "CLAUDE.md",
    "Claude.md",
    "CLAUDE.local.md",
    "CLAUDE__.md",
)

GROK = HomeSpec(
    id="grok",
    label="Grok",
    home_attr="GROK_HOME",
    badge="GR",
    settings_files=("config.toml",),
    instruction_names=_INSTRUCTION_COMMON,
    skill_roots=(
        SkillRoot("skills", "user"),
        SkillRoot("bundled/skills", "bundled"),
    ),
    hooks_dir="hooks",
    disabled_hooks_file="disabled-hooks",
    rules_dirs=("rules", "bundled/agents", "bundled/personas", "bundled/roles"),
    agents_dirs=(),
    plugin_meta_files=(),
    version_files=("version.json",),
    notable_dirs=(
        "sessions",
        "skills",
        "bundled",
        "hooks",
        "installed-plugins",
        "marketplace-cache",
        "docs",
        "logs",
    ),
)

CLAUDE = HomeSpec(
    id="claude",
    label="Claude",
    home_attr="CLAUDE_HOME",
    badge="CL",
    settings_files=("settings.json", "settings.local.json"),
    instruction_names=_INSTRUCTION_COMMON,
    skill_roots=(SkillRoot("skills", "user"),),
    rules_dirs=("rules",),
    agents_dirs=("agents",),
    commands_dirs=("commands",),
    plugin_meta_files=(
        "plugins/installed_plugins.json",
        "plugins/known_marketplaces.json",
    ),
    statusline_files=("statusline.ps1", "statusline.sh", "statusline.js"),
    notable_dirs=(
        "projects",
        "skills",
        "plugins",
        "hooks",
        "rules",
        "commands",
        "agents",
        "file-history",
        "debug",
    ),
)

CODEX = HomeSpec(
    id="codex",
    label="Codex",
    home_attr="CODEX_HOME",
    badge="CO",
    settings_files=("config.toml",),
    instruction_names=_INSTRUCTION_COMMON,
    skill_roots=(SkillRoot("skills", "user"),),
    rules_dirs=("rules",),
    plugin_meta_files=(),
    version_files=("version.json",),
    notable_dirs=(
        "sessions",
        "archived_sessions",
        "skills",
        "plugins",
        "rules",
        "cache",
    ),
)

CURSOR = HomeSpec(
    id="cursor",
    label="Cursor",
    home_attr="CURSOR_HOME",
    badge="CU",
    settings_files=("argv.json",),
    instruction_names=_INSTRUCTION_COMMON,
    skill_roots=(
        SkillRoot("skills", "user"),
        SkillRoot("skills-cursor", "skills-cursor"),
    ),
    hooks_json=("hooks.json",),
    rules_dirs=("rules",),
    agents_dirs=("agents",),
    mcp_json_files=("mcp.json",),
    notable_dirs=(
        "skills",
        "skills-cursor",
        "agents",
        "rules",
        "plugins",
        "projects",
        "extensions",
    ),
)

HOME_SPECS: tuple[HomeSpec, ...] = (GROK, CLAUDE, CODEX, CURSOR)
HOME_SPEC_BY_ID: dict[str, HomeSpec] = {s.id: s for s in HOME_SPECS}

# Max bytes of text loaded from any single allowlisted file.
MAX_TEXT_BYTES = 256 * 1024
